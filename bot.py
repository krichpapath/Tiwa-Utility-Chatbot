"""Tiwa Discord bot — entrypoint. The brain lives in tiwa/."""
import asyncio
import faulthandler
import logging
import os
from collections import defaultdict, deque
from datetime import datetime
from functools import partial

import discord
from discord.ext import tasks

from tiwa import memory, music, pipeline, tools, voice  # tiwa.llm loads .env

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
client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())

HOME = os.environ.get("TIWA_HOME_CHANNEL")  # channel id for unprompted messages; unset = quiet


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
        # llm._openrouter_chat passes the provider's own words through — "User not
        # found." (revoked key) reads nothing like "Insufficient credits", and
        # that difference is the entire diagnosis. Repeat it verbatim.
        why = f"{str(e).split(' for url')[0][:90]} — OPENROUTER_API_KEY is wrong, " \
              "expired, or shadowed by a system environment variable"
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


async def _heard(name: str, text: str, channel=None, activated=False, error=None, user_id=None):
    """Activated commands arrive after the local acoustic wake check.

    Non-activated legacy test callbacks retain their existing wake check.
    """
    channels = [channel] if channel is not None else list(voice_channel.values())
    if channel is None and len(channels) > 1:
        return  # an unscoped transcript must never leak across servers
    for ch in channels:
        if error:
            await ch.send(f"⚠️ {error}")
            continue
        if activated and await calendar.answer(ch, user_id, text):
            continue
        history[ch.id].append({"role": "user", "content": f"{name}: {text}"})
        memory.log(db, "voice", f"{name}: {text}")
        asked = text if activated else voice.wake(text)
        await ch.send(f"🎙 **{name}:** {text}" + ("" if asked is None else "  ← for me"))
        if activated and os.getenv("TIWA_VOICE_REPLY", "0") != "1":
            continue  # transcription acceptance mode; no persona, tools, or TTS
        if asked is None:
            continue  # heard, logged, not answered. No tokens spent.
        async with locks[ch.id]:
            try:
                reply = await pipeline.respond(db, list(history[ch.id]), name, asked,
                                               on_late=_later(ch))
            except Exception as e:
                await _apologise(ch, e)
                continue
            if tools.PENDING_CALENDAR:
                reply = ""  # the real proposal is the only confirmation prompt
            if reply:
                history[ch.id].append({"role": "assistant", "content": reply})
                ctx = memory.history_context(list(history[ch.id]))
                asyncio.create_task(
                    asyncio.to_thread(memory.extract, db, name, asked, reply, ctx)
                )
                await ch.send(reply[:2000])
                try:
                    await voice.say(ch.guild, reply)
                except Exception as e:
                    memory.log(db, "error", f"speech failed: {type(e).__name__}")
                    await ch.send("⚠️ speech unavailable; text and queued actions still work")
            await player.flush(ch)
            await calendar.propose(ch, user_id)
            await player.flush_leave(ch, asked)  # she can be told to leave out loud too


from tiwa.discord_player import Player
from tiwa.discord_calendar import Calendar

player = Player(db, client, voice_channel, _heard)
calendar = Calendar(db, history, os.environ.get("TIWA_OWNER_ID"))


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
        recovered = await voice.recover_decryption(ch.guild)
        if recovered:
            memory.log(db, "voice", recovered)
            try:
                await ch.send(recovered)
            except Exception:
                pass  # keep the watchdog alive if the text channel is unavailable
        vc = ch.guild.voice_client
        if vc is None:
            voice_channel.pop(gid, None)
        elif not vc.is_listening():
            # log the restart, not the attempt: a line saying "restarting" when
            # nothing restarted is what buried the log in the first place
            if await voice.listen(ch.guild, partial(_heard, channel=ch), client.loop) == "listening":
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
        await calendar.propose(channel)  # idle turns may queue calendar changes too
    except Exception as e:
        # swallowed on purpose: the next tick retries in 30 minutes. Nobody is
        # waiting on an unprompted message, so a loud channel post would be noise.
        memory.log(db, "error", f"heartbeat failed: {type(e).__name__}: {str(e)[:200]}")
        print(f"[bot] heartbeat failed, loop continues: {type(e).__name__}: {e}")


@client.event
async def on_ready():
    if not calendar.owner_id:
        calendar.owner_id = str((await client.application_info()).owner.id)
    print(f"logged in as {client.user}")
    print(f"[music] {music.ready()}")
    if HOME and not idle_turn.is_running():
        idle_turn.start()
    if not keep_listening.is_running():
        keep_listening.start()


@client.event
async def on_raw_reaction_add(payload):
    if payload.message_id not in calendar.pending or str(payload.emoji) not in ("✅", "❌"):
        return
    _, channel_id, _ = calendar.pending[payload.message_id]
    if payload.channel_id != channel_id or str(payload.user_id) != str(calendar.requesters.get(payload.message_id, calendar.owner_id)):
        return
    channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
    await calendar.confirm(payload.message_id, payload.user_id, channel, str(payload.emoji) == "✅")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    text = message.content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if await calendar.text(message, text):
        return
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
    if cmd in ("status", "ping"):
        vc = message.guild.voice_client if message.guild else None
        sink = getattr(vc, "sink", None)
        task = getattr(sink, "task", None)
        commands = getattr(sink, "commands", None)
        state = ("off" if not vc or not vc.is_listening() else
                 "worker stopped" if task and task.done() else "listening")
        await message.channel.send(
            f"Online. Voice: {state}. Waiting commands: {commands.qsize() if commands is not None else 0}. "
            f"{getattr(sink, 'last_outcome', 'No voice request yet')}. No model call used.")
        return
    if cmd in ("join", "join vc", "get in here", "เข้ามา", "เข้าห้อง"):
        result = await voice.join(message.author)
        if result.startswith("joined"):
            voice_channel[message.guild.id] = message.channel
            result += " — " + await voice.listen(message.guild, partial(_heard, channel=message.channel), client.loop)
        await message.channel.send(result)
        return
    if cmd in ("leave", "leave vc", "get out", "ออกไป", "ออกห้อง"):
        await message.channel.send(await player.leave(message.guild))
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
        if tools.PENDING_CALENDAR:
            reply = ""
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
                    await voice.listen(message.guild, partial(_heard, channel=message.channel), client.loop)
                else:
                    await message.channel.send(result)
        await player.flush(message.channel, message.author)
        await calendar.propose(message.channel, message.author.id)
        await player.flush_leave(message.channel, text)  # last: hanging up stops the music


if __name__ == "__main__":  # guarded so tests can import the DJ logic
    client.run(os.environ["DISCORD_TOKEN"])
