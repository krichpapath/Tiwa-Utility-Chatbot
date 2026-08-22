"""Tiwa Discord bot — entrypoint. The brain lives in tiwa/."""
import asyncio
import faulthandler
import logging
import os
from collections import defaultdict, deque
from datetime import datetime

import discord
from discord.ext import tasks

from tiwa import gcal, memory, music, pipeline, tools, voice  # tiwa.llm loads .env

# Discord sends an RTCP sender report (type 200) every few seconds per speaker.
# voice_recv has no handler for it and logs each one at INFO, which buries the
# lines you actually want. Harmless packet, noisy log — warnings still show.
logging.getLogger("discord.ext.voice_recv.reader").setLevel(logging.WARNING)

db = memory.connect()

# She vanished once with no traceback and no error row — the console was gone and
# the log simply stopped mid-song. Playback runs through PyAV, which is native
# code, so a crash there kills the process without ever reaching Python. stdlib
# faulthandler is the only thing that catches that: it writes the C and Python
# stacks of every thread straight to a file the moment the process faults.
_crash = open(memory.DATA_DIR / "crash.log", "a", buffering=1)
faulthandler.enable(_crash)

history = defaultdict(lambda: deque(maxlen=40))  # channel_id -> chat messages
locks = defaultdict(asyncio.Lock)  # serialize replies per channel

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

HOME = os.environ.get("TIWA_HOME_CHANNEL")  # channel id for unprompted messages; unset = quiet
pending_confirms = {}  # confirm-message id -> plain-language calendar request


voice_channel = {}  # guild id -> text channel to mirror the transcript into


async def _apologise(channel, e: Exception):
    """A turn died. Say so in the channel — plainly, not in her voice.

    Not in her voice on purpose: the model is what just failed, so there is
    nothing to write her line with, and faking one would be the bluffing failure
    wearing a different hat. A 401 is worth naming because it is the one anybody
    can actually fix.
    """
    why = f"{type(e).__name__}"
    if "401" in str(e):
        why = "401 Unauthorized — OPENROUTER_API_KEY is wrong, expired, or being " \
              "shadowed by a system environment variable"
    elif "ConnectError" in why or "ConnectionError" in why:
        why = "cannot reach the model provider — network, or ollama not running"
    memory.log(db, "error", f"turn failed: {type(e).__name__}: {str(e)[:200]}")
    print(f"[bot] turn failed: {type(e).__name__}: {e}")
    await channel.send(f"⚠️ my brain call failed — {why}")


def _later(channel):
    """How a mini that finished after she spoke reaches the chat: a second
    message, never an edit. She sends it herself — the facts stay inside."""
    async def send(line: str):
        history[channel.id].append({"role": "assistant", "content": line})
        await channel.send(line[:2000])

    return send


async def _heard(name: str, text: str):
    """Everything said in voice becomes chatlog (G6). The model runs ONLY when
    her name is in it (G7) — Whisper is cheap, the LLM is not."""
    for ch in list(voice_channel.values()):
        history[ch.id].append({"role": "user", "content": f"{name}: {text}"})
        memory.log(db, "voice", f"{name}: {text}")
        asked = voice.wake(text)
        await ch.send(f"🎙 **{name}:** {text}" + ("" if asked is None else "  ← for me"))
        if asked is None:
            continue  # heard, logged, not answered. No tokens spent.
        async with locks[ch.id]:
            try:
                reply = await pipeline.respond(db, list(history[ch.id]), name, asked,
                                               on_late=_later(ch))
            except Exception as e:
                await _apologise(ch, e)
                continue
            if not reply:
                continue
            history[ch.id].append({"role": "assistant", "content": reply})
            ctx = memory.history_context(list(history[ch.id]))
            asyncio.create_task(
                asyncio.to_thread(memory.extract, db, name, asked, reply, ctx)
            )
            await ch.send(reply[:2000])
            await voice.say(ch.guild, reply)  # G8: answer out loud, ducks music
            await _flush_music(ch)
            await _flush_leave(ch, asked)  # she can be told to leave out loud too


_deck_gen = 0  # bumped by every deliberate play; see `after` below


async def _start(channel, hit):
    """Play `hit` now, and chain into the queue when it ends."""
    global _deck_gen
    vc = channel.guild.voice_client
    if vc is None:
        return

    # `vc.stop()` below makes discord.py fire the OUTGOING track's `after` — the
    # same callback a song reaching its end fires. So swapping tracks used to
    # look exactly like "the song finished", and pulled the next queue item on
    # top of the song we were in the middle of starting. Live log, 20:39:15:
    # play_music('Mili') stopped Dvorak, Dvorak's after popped the queue, and
    # Mili and 'Limbus Company OST' both logged `playing` in the same second.
    #
    # So each play takes a ticket. A callback only owns the deck if its ticket
    # is still the current one — bumped BEFORE the stop, so the outgoing track's
    # callback is already stale by the time it runs on the audio thread.
    _deck_gen += 1
    mine = _deck_gen
    if vc.is_playing():
        vc.stop()
    loop = client.loop

    def after(error):
        if error:
            print(f"[music] playback error: {error!r}")
        if mine != _deck_gen:
            return  # replaced on purpose — the track that replaced us owns the deck
        music.NOW["title"] = None
        # a finished song pulls the next one; scheduled onto the bot's loop
        # because `after` runs on discord's audio thread
        asyncio.run_coroutine_threadsafe(_next(channel), loop)

    src = None
    try:
        src = music.source_for(hit)
        if music.VOLUME != 1.0:  # set from the control panel
            src = discord.PCMVolumeTransformer(src, volume=music.VOLUME)
        vc.play(src, after=after)
    except Exception as e:  # opus, frame timing, disconnects
        print(f"[music] play failed: {type(e).__name__}: {e}")
        # discord.py cleans up sources IT accepted. This one it never took, so
        # its decode thread and its open PyAV container are ours to close —
        # otherwise every failed play leaks a thread reading a CDN forever.
        if src is not None:
            src.cleanup()
        music.NOW["title"] = None  # we already stopped whatever was on
        await channel.send(f"found it but couldn't play it: {e}")
        return
    music.NOW.update(title=hit["title"], query=hit.get("query", ""))
    memory.log(db, "music", f"playing {hit['title']}")
    print(f"[music] playing: {hit['title']}")
    await channel.send(f"▶ **{hit['title']}**")


async def _next(channel):
    """Advance to the queued song, if any."""
    if not music.QUEUE:
        return
    await _start(channel, music.QUEUE.pop(0))


async def _find(channel, query):
    print(f"[music] searching: {query!r}")
    try:
        hit = await asyncio.to_thread(music.find, query)
        hit["query"] = query  # kept so a mid-song drop can re-resolve the url
        return hit
    except Exception as e:
        print(f"[music] search failed: {type(e).__name__}: {e}")
        await channel.send(f"couldn't find that: {e}")
        return None


async def _flush_music(channel, author=None):
    """Drain everything she asked the DJ to do this turn. Runs after the reply,
    so music never blocks her talking."""
    jobs = list(tools.DJ)
    tools.DJ.clear()
    if tools.PENDING_MUSIC is not None:  # play/stop from the older tools
        jobs.insert(0, ("stop" if tools.PENDING_MUSIC == "" else "play",
                        tools.PENDING_MUSIC))
        tools.PENDING_MUSIC = None
    if not jobs:
        return

    vc = channel.guild.voice_client
    if vc is None and author is not None:
        # asking for music IS asking her to come in — no need to say join first
        result = await voice.join(author)
        if not result.startswith("joined"):
            await channel.send(result)
            return
        voice_channel[channel.guild.id] = channel
        vc = channel.guild.voice_client
    if vc is None:
        await channel.send("i'm not in a voice channel")
        return

    global _deck_gen
    for action, arg in jobs:
        if action == "stop":
            music.QUEUE.clear()
            music.NOW["title"] = None
            # stop means the deck is done, so take the ticket away from the
            # outgoing track's callback. Without this it still schedules a
            # _next(), which runs at the NEXT await — by which time a later job
            # in this same flush may have queued something, and the stale
            # callback pops it and starts playing over the stop.
            _deck_gen += 1
            vc.stop()
        elif action == "skip":
            if not music.NOW["title"]:
                await channel.send("nothing playing")
            else:
                skipped = music.NOW["title"]
                vc.stop()  # `after` pulls the next track by itself
                await channel.send(f"⏭ skipped **{skipped}**")
        elif action == "queue":
            hit = await _find(channel, arg)
            if hit:
                if music.NOW["title"]:
                    music.QUEUE.append(hit)
                    await channel.send(f"➕ queued **{hit['title']}**"
                                       f" (#{len(music.QUEUE)})")
                else:
                    await _start(channel, hit)
        elif action == "play":
            hit = await _find(channel, arg)
            if hit:
                await _start(channel, hit)


async def _hang_up(guild) -> str:
    """Leave the call. Disconnecting kills playback, so the deck goes with it."""
    voice_channel.pop(guild.id, None)
    music.QUEUE.clear()
    music.NOW["title"] = None
    return await voice.leave(guild)


async def _flush_leave(channel, text):
    """She asked to leave. Runs last, after the reply and the music."""
    if not tools.PENDING_LEAVE:
        return
    tools.PENDING_LEAVE = False
    if not voice.wants_now(text):
        return  # "ออกไปทีหลังนะ" is a plan, not an ask — same veto as joining
    await channel.send(await _hang_up(channel.guild))


async def _flush_calendar_queue(channel):
    """calendar_write only queues; every write is gated behind Krich's ✅ here."""
    while tools.PENDING_CALENDAR:
        text = tools.PENDING_CALENDAR.pop(0)
        m = await channel.send(f"📅 calendar change: **{text}** — ✅ to confirm, ❌ to drop")
        await m.add_reaction("✅")
        await m.add_reaction("❌")
        pending_confirms[m.id] = text


@tasks.loop(seconds=30)
async def keep_listening():
    """voice_recv stops listening for good after ONE decode error. Restart it.

    Only meaningful when her ears are on. With TIWA_LISTEN=0 (the default)
    voice.listen() returns without calling vc.listen(), so is_listening() is
    False forever — this loop read that as "stopped", logged a restart, restarted
    nothing, and did it again 30 seconds later. Measured on a real session: 36
    restart rows out of 36 ticks, and 0 utterances, which is 36 of 60 log rows.
    """
    if not voice.LISTEN:
        return
    for gid, ch in list(voice_channel.items()):
        vc = ch.guild.voice_client
        if vc is None:
            voice_channel.pop(gid, None)
        elif not vc.is_listening():
            # log the restart, not the attempt: a line saying "restarting" when
            # nothing restarted is what buried the log in the first place
            if voice.listen(ch.guild, _heard, client.loop) == "listening":
                memory.log(db, "voice", "listening had stopped — restarted")


last_unprompted = datetime.min  # ponytail: >=3h between unprompted messages, no spam


@tasks.loop(minutes=30)
async def idle_turn():
    """Unprompted message on the heartbeat. Guarded, because a provider outage
    used to kill the LOOP, not just the tick.

    docs/open-questions.md carried this as the one open bug since July:
    pipeline.idle()'s speak pass is unguarded, and discord.py's tasks.loop logs
    the exception and then STOPS unless an error handler is registered. One
    outage made her silent until the process restarted, with nothing on screen
    saying why. _settle() already catches its own errors; the pass around it
    did not.
    """
    global last_unprompted
    try:
        if not 9 <= datetime.now().hour < 23:  # quiet hours
            return
        if (datetime.now() - last_unprompted).total_seconds() < 3 * 3600:
            return
        thought = await pipeline.idle(db)
        channel = client.get_channel(int(HOME)) or await client.fetch_channel(int(HOME))
        if thought:
            last_unprompted = datetime.now()
            history[channel.id].append({"role": "assistant", "content": thought})
            await channel.send(thought)
        await _flush_calendar_queue(channel)  # idle turns may queue calendar changes too
    except Exception as e:
        # swallowed on purpose: the next tick retries in 30 minutes. Nobody is
        # waiting on an unprompted message, so a loud channel post would be noise.
        memory.log(db, "error", f"heartbeat failed: {type(e).__name__}: {str(e)[:200]}")
        print(f"[bot] heartbeat failed, loop continues: {type(e).__name__}: {e}")


@client.event
async def on_ready():
    print(f"logged in as {client.user}")
    print(f"[music] {music.ready()}")
    if HOME and not idle_turn.is_running():
        idle_turn.start()
    if not keep_listening.is_running():
        keep_listening.start()


@client.event
async def on_raw_reaction_add(payload):
    text = pending_confirms.get(payload.message_id)
    if text is None or payload.user_id == client.user.id or str(payload.emoji) not in "✅❌":
        return
    del pending_confirms[payload.message_id]  # one shot
    channel = client.get_channel(payload.channel_id) or await client.fetch_channel(
        payload.channel_id
    )
    if str(payload.emoji) == "✅":
        result = await asyncio.to_thread(gcal.apply_change, text)
        await channel.send(result)
    else:
        await channel.send("dropped.")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    text = message.content.replace(f"<@{client.user.id}>", "").strip()
    # content_type is None for some clients; treat that as not-an-image rather than
    # paying a vision call on a .zip
    images = [a.url for a in message.attachments
              if (a.content_type or "").startswith("image/")]
    # a picture with no caption is the normal case — the chatlog needs to show
    # something happened, or "ดูสิ" three messages later resolves to nothing
    said = (text + " [image]").strip() if images else text
    if said:
        # record everything so Tiwa has channel context; reply only when addressed
        history[message.channel.id].append(
            {"role": "user", "content": f"{message.author.display_name}: {said}"}
        )
    if message.guild is not None and client.user not in message.mentions:
        return
    if not said:
        return
    # voice-channel commands: exact phrases only, so normal chat never triggers them
    cmd = text.lower().strip(" .!?")
    if cmd in ("join", "join vc", "get in here", "เข้ามา", "เข้าห้อง"):
        result = await voice.join(message.author)
        if result.startswith("joined"):
            voice_channel[message.guild.id] = message.channel
            result += " — " + voice.listen(message.guild, _heard, client.loop)
        await message.channel.send(result)
        return
    if cmd in ("leave", "leave vc", "get out", "ออกไป", "ออกห้อง"):
        await message.channel.send(await _hang_up(message.guild))
        return

    author = message.author.display_name
    async with locks[message.channel.id]:
        async with message.channel.typing():
            try:
                reply = await pipeline.respond(db, list(history[message.channel.id]),
                                               author, text, images,
                                               on_late=_later(message.channel))
            except Exception as e:
                # She goes MUTE otherwise. discord.py logs the traceback to the
                # console and the channel shows nothing at all — someone typed at
                # her and she just did not answer. That happened live on an expired
                # key: every turn 401'd, every turn silent, and nothing on screen
                # said why. Tools already take this stance ("search failed: …" is
                # returned as text, never raised); the turn itself did not.
                await _apologise(message.channel, e)
                return
        # Queued actions must run whatever she says. An empty reply used to
        # `return` here and silently swallow the song she had already queued —
        # you asked for Bad Apple and nothing happened.
        if reply:
            history[message.channel.id].append({"role": "assistant", "content": reply})
            # post-turn memory write, off the reply path; prior lines let "he/she" resolve
            ctx = memory.history_context(list(history[message.channel.id]))
            asyncio.create_task(
                # `said`, not `text`: extraction of a caption-less image turn used
                # to be handed an empty string
                asyncio.to_thread(memory.extract, db, author, said, reply, ctx)
            )
            for i in range(0, len(reply), 2000):
                await message.channel.send(reply[i : i + 2000])
        else:
            print("[bot] empty reply — running queued actions anyway")
        if tools.PENDING_JOIN:  # she decided to come into voice this turn
            tools.PENDING_JOIN = False
            if voice.wants_now(text):  # "join us later tonight" is a plan, not an ask
                result = await voice.join(message.author)
                if result.startswith("joined"):
                    voice_channel[message.guild.id] = message.channel
                    voice.listen(message.guild, _heard, client.loop)
                else:
                    await message.channel.send(result)
        await _flush_music(message.channel, message.author)
        await _flush_calendar_queue(message.channel)
        await _flush_leave(message.channel, text)  # last: hanging up stops the music


if __name__ == "__main__":  # guarded so tests can import the DJ logic
    client.run(os.environ["DISCORD_TOKEN"])
