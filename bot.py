"""Tiwa Discord bot — entrypoint. The brain lives in tiwa/."""
import asyncio
import faulthandler
import logging
import os
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from functools import partial

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
client = discord.Client(intents=intents, allowed_mentions=discord.AllowedMentions.none())

HOME = os.environ.get("TIWA_HOME_CHANNEL")  # channel id for unprompted messages; unset = quiet
proposal_users = {}  # proposal id -> requesting Discord account
pending_confirms = {}  # confirm-message id -> plain-language calendar request
OWNER_ID = os.environ.get("TIWA_OWNER_ID")  # defaults to Discord application owner at login


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
        if activated and await _calendar_answer(ch, user_id, text):
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
            await _flush_music(ch)
            await _flush_calendar_queue(ch, user_id)
            await _flush_leave(ch, asked)  # she can be told to leave out loud too


_deck_gen = 0  # bumped by every deliberate play; see `after` below
_music_lock = asyncio.Lock()  # ponytail: one lock for the existing single deck
_music_tasks = set()


async def _start(channel, hit):
    """Play `hit` now, and chain into the queue when it ends."""
    global _deck_gen
    vc = channel.guild.voice_client
    if vc is None:
        return False
    if hit.get("deferred"):
        try:
            resolved = await asyncio.to_thread(music.find, music.watch_url(hit["id"]))
            if resolved["id"] != hit["id"]:
                raise LookupError("resolved video identity changed")
            hit = {**resolved, "query": music.watch_url(hit["id"])}
        except Exception as error:
            await channel.send(f"Skipped unavailable **{hit['title']}**: {error}")
            if music._rate_limited(error):
                music.QUEUE.clear()
            return False

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
        # a finished song pulls the next one; scheduled onto the bot's loop
        # because `after` runs on discord's audio thread
        asyncio.run_coroutine_threadsafe(_next(channel, mine), loop)

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
        return False
    music.NOW.update(title=hit["title"], query=hit.get("query", ""))
    memory.log(db, "music", f"playing {hit['title']}")
    print(f"[music] playing: {hit['title']}")
    await channel.send(f"▶ **{hit['title']}**")

    return True


async def _next(channel, generation=None):
    """Advance to the queued song, if any."""
    async with _music_lock:
        if generation is not None and generation != _deck_gen:
            return
        music.NOW["title"] = None
        await _advance(channel)


async def _advance(channel):
    while music.QUEUE:
        if await _start(channel, music.QUEUE.pop(0)):
            return


async def _flush_music(channel, author=None):
    # Keep accepted searches alive beyond a voice callback's 90-second deadline.
    # Other speakers can submit their requests while the deck worker searches.
    task = asyncio.create_task(_run_music(channel, author))
    _music_tasks.add(task)
    task.add_done_callback(_music_tasks.discard)
    await asyncio.wait({task}, timeout=1)


async def _run_music(channel, author=None):
    async with _music_lock:
        try:
            await _flush_music_locked(channel, author)
        except Exception as error:
            print(f"[music] request failed: {type(error).__name__}: {error}")
            try:
                await channel.send("Music request failed; the next queued request can still run.")
            except Exception:
                pass  # disconnected text channel must not strand the deck lock


async def _flush_music_locked(channel, author=None):
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
    if channel.guild is None:
        await channel.send("music needs a server voice channel — ask me there")
        return

    vc = channel.guild.voice_client
    if vc is None and author is not None:
        # asking for music IS asking her to come in — no need to say join first
        result = await voice.join(author)
        if not result.startswith("joined"):
            await channel.send(result)
            return
        voice_channel[channel.guild.id] = channel
        await voice.listen(channel.guild, partial(_heard, channel=channel), client.loop)
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
        elif action in ("skip", "remove"):
            target = arg.get("target", "") if isinstance(arg, dict) else arg
            count = arg.get("count", 1) if isinstance(arg, dict) else 1
            if not isinstance(target, str) or type(count) is not int or not 1 <= count <= 50:
                await channel.send("Invalid queue edit; no songs changed.")
                continue
            current = music.NOW["title"] if action == "skip" else None
            titles = ([current] if current else []) + [h["title"] for h in music.QUEUE]
            indices = music.queue_targets(target, titles)[:count] if target else list(range(min(count, len(titles))))
            if not indices:
                await channel.send("No unique matching song found; queue unchanged." if target else "No songs to " + action + ".")
                continue
            offset = int(bool(current))
            removed = [titles[i] for i in indices]
            music.QUEUE[:] = [h for i, h in enumerate(music.QUEUE) if i + offset not in indices]
            if current and 0 in indices:
                _deck_gen += 1
                vc.stop()
                music.NOW["title"] = None
                await _advance(channel)
            await channel.send(("Skipped: " if action == "skip" else "Removed from queue: ") + ", ".join(removed)[:1800])
        elif action in ("queue", "play"):
            try:
                hits = await asyncio.to_thread(music.find_many, arg)
            except Exception as error:
                await channel.send(f"couldn't find that: {error}")
                continue  # a failed step must not discard later commands
            for hit in hits:
                hit.setdefault("query", arg)
                if music.NOW["title"] and hit.get("id"):
                    hit["deferred"] = True
                music.QUEUE.append(hit)
            if len(hits) > 1:
                titles = "\n".join(f"• {h['title']}" for h in hits)
                await channel.send(f"Added {len(hits)} songs (up to 50 per request):\n{titles}"[:1950])
            elif hits and music.NOW["title"]:
                await channel.send(f"Queued **{hits[0]['title']}** (#{len(music.QUEUE)})")
            if not music.NOW["title"]:
                await _advance(channel)


async def _hang_up(guild) -> str:
    async with _music_lock:
        return await _hang_up_locked(guild)


async def _hang_up_locked(guild) -> str:
    """Leave the call. Disconnecting kills playback, so the deck goes with it."""
    if guild is None:
        return "voice commands need a server voice channel"
    global _deck_gen
    _deck_gen += 1
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


async def _flush_calendar_queue(channel, user_id=None):
    """calendar_write only queues; every write is gated behind Krich's ✅ here."""
    while tools.PENDING_CALENDAR:
        text = tools.PENDING_CALENDAR.pop(0)
        if not OWNER_ID:
            await channel.send("calendar writes unavailable: configure TIWA_OWNER_ID or reconnect the bot")
            continue
        try:
            plan = await asyncio.to_thread(gcal.prepare_change, text)
        except Exception as e:
            await channel.send(f"calendar proposal failed: {type(e).__name__}; provide title, date and time")
            continue
        now = time.monotonic()
        for mid, (_, _, expires) in list(pending_confirms.items()):
            if expires <= now:
                pending_confirms.pop(mid, None)
        duplicate = next((mid for mid, (old, cid, _) in pending_confirms.items()
                          if cid == channel.id and old == plan and proposal_users.get(mid, OWNER_ID) == (user_id or OWNER_ID)), None)
        if duplicate is not None:
            await channel.send("นัดนี้รอยืนยันอยู่แล้ว — ตอบ ยืนยัน ได้เลย")
            if user_id is not None:
                from tiwa.listening import FOLLOWUPS
                FOLLOWUPS[user_id] = time.monotonic() + 60
            continue
        m = await channel.send(f"📅 **{gcal.describe_change(plan)}**\nให้บันทึกตามนี้เลยไหม? ตอบได้ตามปกติ (รอฟังคำตอบ 1 นาที)")
        # One latest proposal per requester in this channel.
        for mid, (_, cid, _) in list(pending_confirms.items()):
            if cid == channel.id and proposal_users.get(mid, OWNER_ID) == (user_id or OWNER_ID):
                pending_confirms.pop(mid, None)
                proposal_users.pop(mid, None)
        pending_confirms[m.id] = (plan, channel.id, now + 600)
        proposal_users[m.id] = user_id or OWNER_ID
        if user_id is not None:
            from tiwa.listening import FOLLOWUPS
            FOLLOWUPS[user_id] = time.monotonic() + 60



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
        await _flush_calendar_queue(channel)  # idle turns may queue calendar changes too
    except Exception as e:
        # swallowed on purpose: the next tick retries in 30 minutes. Nobody is
        # waiting on an unprompted message, so a loud channel post would be noise.
        memory.log(db, "error", f"heartbeat failed: {type(e).__name__}: {str(e)[:200]}")
        print(f"[bot] heartbeat failed, loop continues: {type(e).__name__}: {e}")


@client.event
async def on_ready():
    global OWNER_ID
    if not OWNER_ID:
        OWNER_ID = str((await client.application_info()).owner.id)
    print(f"logged in as {client.user}")
    print(f"[music] {music.ready()}")
    if HOME and not idle_turn.is_running():
        idle_turn.start()
    if not keep_listening.is_running():
        keep_listening.start()


async def _confirm_calendar(message_id, user_id, channel, approve):
    pending = pending_confirms.get(message_id)
    if pending is None:
        await channel.send("No pending calendar proposal; it may already be handled or the bot restarted. Ask again.")
        return
    plan, channel_id, expires = pending
    if channel.id != channel_id:
        return
    if str(user_id) != str(proposal_users.get(message_id, OWNER_ID)):
        await channel.send("Only the person who requested this appointment can confirm it.")
        return
    del pending_confirms[message_id]  # claim before awaiting: no duplicate writes
    proposal_users.pop(message_id, None)
    from tiwa.listening import FOLLOWUPS
    FOLLOWUPS.pop(user_id, None)
    if time.monotonic() >= expires:
        await channel.send("calendar proposal expired; ask again")
        return
    if not approve:
        await channel.send("Calendar proposal dropped.")
        return
    try:
        result = await asyncio.to_thread(gcal.apply_change, plan)
    except Exception as e:
        result = f"calendar change failed: {type(e).__name__}; check calendar before retrying"
    memory.log(db, "calendar", result)
    history[channel.id].append({"role": "assistant", "content": result})
    await channel.send(result)


def _calendar_decision(text):
    # Whole utterance only. STT may retain a mangled wake prefix even after local activation.
    text = re.sub(
        r"^\s*(?:(?:hey|เฮ้ย?)\s*[,!]?\s*(?:tiwa|ทิวา|ที่ว่า|ที่วา)|เหตุที่ว่า|ให้ที่ว่า|ให้ที่วา|ที่ว่า|ทิวา)[\s,!.:—-]*",
        "", text, flags=re.IGNORECASE)
    match = re.fullmatch(
        r"\s*(?:please\s+)?(confirm|cancel|ยืนยัน|คอนเฟิร์ม|ยกเลิก)"
        r"(?:\s|สิ|เลย|นะ|ครับ|ค่ะ|คะ|จ้า|จ้ะ|ได้เลย|please|it|that|[.!])*", text.casefold())
    if not match:
        return None
    return match[1] in ("confirm", "ยืนยัน", "คอนเฟิร์ม")


async def _calendar_followup(channel, user_id, decision, mid=None):
    if mid is None:
        matches = [key for key, (_, cid, expires) in pending_confirms.items()
                   if cid == channel.id and expires > time.monotonic()
                   and str(proposal_users.get(key, OWNER_ID)) == str(user_id)]
        if len(matches) != 1:
            await channel.send("No single pending calendar proposal. Ask again if it expired or Tiwa restarted; if several are pending, reply to the one you want with confirm or cancel.")
            return
        mid = matches[0]
    await _confirm_calendar(mid, user_id, channel, decision)


async def _calendar_answer(channel, user_id, text, mid=None):
    explicit = _calendar_decision(text)
    candidates = [key for key, (_, cid, expires) in pending_confirms.items()
                  if cid == channel.id and expires > time.monotonic()
                  and str(proposal_users.get(key, OWNER_ID)) == str(user_id)]
    if mid is None and len(candidates) == 1:
        mid = candidates[0]
    if mid not in candidates:
        if explicit is not None:
            await _calendar_followup(channel, user_id, explicit, mid)
            return True
        return False
    pending = pending_confirms[mid]
    if explicit is not None:
        await _confirm_calendar(mid, user_id, channel, explicit)
        return True
    try:
        decision = await asyncio.wait_for(asyncio.to_thread(gcal.confirmation_reply, pending[0], text), 15)
    except Exception:
        await channel.send("ยังไม่ได้บันทึกนะ ฟังคำตอบไม่ชัด ต้องการให้บันทึกนัดนี้ไหม?")
        from tiwa.listening import FOLLOWUPS
        FOLLOWUPS[user_id] = time.monotonic() + 60
        return True
    # A delayed answer must never approve a replacement proposal.
    if pending_confirms.get(mid) is not pending:
        return True
    if decision in ("approve", "decline"):
        await _confirm_calendar(mid, user_id, channel, decision == "approve")
        return True
    if decision == "unrelated":
        return False
    if decision == "revise":
        try:
            plan = await asyncio.to_thread(gcal.prepare_change,
                f"Revise this UNSAVED proposal, preserving its action and all details except the requested correction. "
                f"Proposal: {pending[0]}\nRequester correction: {text}")
            if pending_confirms.get(mid) is not pending:
                return True
            pending_confirms[mid] = (plan, channel.id, time.monotonic() + 600)
            await channel.send(f"📅 {gcal.describe_change(plan)}\nตามนี้ ให้บันทึกเลยไหม?")
        except Exception:
            await channel.send("ยังไม่ได้บันทึกนะ ต้องการเปลี่ยนรายละเอียดไหน?")
    else:
        await channel.send(f"ยังไม่ได้บันทึกนะ — {gcal.describe_change(pending[0])}\nให้บันทึกตามนี้ไหม?")
    from tiwa.listening import FOLLOWUPS
    FOLLOWUPS[user_id] = time.monotonic() + 60
    return True


async def _calendar_text(message, text):
    ref = getattr(message, "reference", None)
    return await _calendar_answer(message.channel, message.author.id, text,
                                  getattr(ref, "message_id", None))


@client.event
async def on_raw_reaction_add(payload):
    if payload.message_id not in pending_confirms or str(payload.emoji) not in ("✅", "❌"):
        return
    if str(payload.user_id) != str(OWNER_ID):
        return
    channel = client.get_channel(payload.channel_id) or await client.fetch_channel(payload.channel_id)
    await _confirm_calendar(payload.message_id, payload.user_id, channel, str(payload.emoji) == "✅")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    text = message.content.replace(f"<@{client.user.id}>", "").replace(f"<@!{client.user.id}>", "").strip()
    if await _calendar_text(message, text):
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
        await _flush_music(message.channel, message.author)
        await _flush_calendar_queue(message.channel, message.author.id)
        await _flush_leave(message.channel, text)  # last: hanging up stops the music


if __name__ == "__main__":  # guarded so tests can import the DJ logic
    client.run(os.environ["DISCORD_TOKEN"])
