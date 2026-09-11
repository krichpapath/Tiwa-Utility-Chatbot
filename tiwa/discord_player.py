"""Discord music execution: ordered requests, playback callbacks and queue edits.

Search and decoding live in music.py. One Player owns the existing single deck.
"""

import asyncio
from functools import partial
import discord
from . import memory, music, tools, voice


class Player:
    def __init__(self, db, client, voice_channels, on_heard):
        self.db = db
        self.client = client
        self.voice_channels = voice_channels
        self.on_heard = on_heard
        self.generation = 0
        self.lock = asyncio.Lock()
        self.tasks = set()

    async def _start(self, channel, hit):
        """Play `hit` now, and chain into the queue when it ends."""
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
        self.generation += 1
        mine = self.generation
        if vc.is_playing():
            vc.stop()
        loop = self.client.loop

        def after(error):
            if error:
                print(f"[music] playback error: {error!r}")
            if mine != self.generation:
                return  # replaced on purpose — the track that replaced us owns the deck
            # a finished song pulls the next one; scheduled onto the bot's loop
            # because `after` runs on discord's audio thread
            asyncio.run_coroutine_threadsafe(self.advance_after(channel, mine), loop)

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
        memory.log(self.db, "music", f"playing {hit['title']}")
        print(f"[music] playing: {hit['title']}")
        await channel.send(f"▶ **{hit['title']}**")

        return True

    async def advance_after(self, channel, generation=None):
        """Advance to the queued song, if any."""
        async with self.lock:
            if generation is not None and generation != self.generation:
                return
            music.NOW["title"] = None
            await self._advance(channel)

    async def _advance(self, channel):
        while music.QUEUE:
            if await self._start(channel, music.QUEUE.pop(0)):
                return

    async def flush(self, channel, author=None):
        # Keep accepted searches alive beyond a voice callback's 90-second deadline.
        # Other speakers can submit their requests while the deck worker searches.
        task = asyncio.create_task(self._run(channel, author))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        await asyncio.wait({task}, timeout=1)

    async def _run(self, channel, author=None):
        async with self.lock:
            try:
                await self._flush_locked(channel, author)
            except Exception as error:
                print(f"[music] request failed: {type(error).__name__}: {error}")
                try:
                    await channel.send(
                        "Music request failed; the next queued request can still run."
                    )
                except Exception:
                    pass  # disconnected text channel must not strand the deck lock

    async def _flush_locked(self, channel, author=None):
        """Drain everything she asked the DJ to do this turn. Runs after the reply,
        so music never blocks her talking."""
        jobs = list(tools.DJ)
        tools.DJ.clear()
        if tools.PENDING_MUSIC is not None:  # play/stop from the older tools
            jobs.insert(0, ("stop" if tools.PENDING_MUSIC == "" else "play", tools.PENDING_MUSIC))
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
            self.voice_channels[channel.guild.id] = channel
            await voice.listen(
                channel.guild, partial(self.on_heard, channel=channel), self.client.loop
            )
            vc = channel.guild.voice_client
        if vc is None:
            await channel.send("i'm not in a voice channel")
            return

        for action, arg in jobs:
            if action == "stop":
                music.QUEUE.clear()
                music.NOW["title"] = None
                # stop means the deck is done, so take the ticket away from the
                # outgoing track's callback. Without this it still schedules a
                # self.advance_after(), which runs at the NEXT await — by which time a later job
                # in this same flush may have queued something, and the stale
                # callback pops it and starts playing over the stop.
                self.generation += 1
                vc.stop()
            elif action in ("skip", "remove"):
                target = arg.get("target", "") if isinstance(arg, dict) else arg
                count = arg.get("count", 1) if isinstance(arg, dict) else 1
                if not isinstance(target, str) or type(count) is not int or not 1 <= count <= 50:
                    await channel.send("Invalid queue edit; no songs changed.")
                    continue
                current = music.NOW["title"] if action == "skip" else None
                titles = ([current] if current else []) + [h["title"] for h in music.QUEUE]
                indices = (
                    music.queue_targets(target, titles)[:count]
                    if target
                    else list(range(min(count, len(titles))))
                )
                if not indices:
                    await channel.send(
                        "No unique matching song found; queue unchanged."
                        if target
                        else "No songs to " + action + "."
                    )
                    continue
                offset = int(bool(current))
                removed = [titles[i] for i in indices]
                music.QUEUE[:] = [h for i, h in enumerate(music.QUEUE) if i + offset not in indices]
                if current and 0 in indices:
                    self.generation += 1
                    vc.stop()
                    music.NOW["title"] = None
                    await self._advance(channel)
                await channel.send(
                    ("Skipped: " if action == "skip" else "Removed from queue: ")
                    + ", ".join(removed)[:1800]
                )
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
                    await channel.send(
                        f"Added {len(hits)} songs (up to 50 per request):\n{titles}"[:1950]
                    )
                elif hits and music.NOW["title"]:
                    await channel.send(f"Queued **{hits[0]['title']}** (#{len(music.QUEUE)})")
                if not music.NOW["title"]:
                    await self._advance(channel)

    async def leave(self, guild) -> str:
        async with self.lock:
            return await self._leave_locked(guild)

    async def _leave_locked(self, guild) -> str:
        """Leave the call. Disconnecting kills playback, so the deck goes with it."""
        if guild is None:
            return "voice commands need a server voice channel"
        self.generation += 1
        self.voice_channels.pop(guild.id, None)
        music.QUEUE.clear()
        music.NOW["title"] = None
        return await voice.leave(guild)

    async def flush_leave(self, channel, text):
        """She asked to leave. Runs last, after the reply and the music."""
        if not tools.PENDING_LEAVE:
            return
        tools.PENDING_LEAVE = False
        if not voice.wants_now(text):
            return  # "ออกไปทีหลังนะ" is a plan, not an ask — same veto as joining
        await channel.send(await self.leave(channel.guild))
