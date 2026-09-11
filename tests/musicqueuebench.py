"""Offline acceptance: ordered plans, artist batches, playlists and concurrent listeners."""
import asyncio
import json
import sys
import time
import threading
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import llm, memory, minis, music, tools
import bot


def step(action, terms="", count=1):
    return dict(action=action, terms=terms, count=count, request=terms, selection="mood")


def plans():
    db = memory.connect(":memory:")
    for steps in ([step("play", "TheFatRat songs", 5)],
                  [step("stop"), step("play", "Unity TheFatRat"), step("queue", "Monody TheFatRat")]):
        tools.new_turn()
        with patch.object(llm, "chat", return_value={"content": json.dumps({"steps": steps})}):
            result = minis.dj(db, "test original")
        assert result["action"] == "play"
        assert [a for a, _ in tools.DJ] == [s["action"] for s in steps]
        assert [q["count"] for a, q in tools.DJ if a == "play"] == [s["count"] for s in steps if s["action"] == "play"]
    for bad in (None, [step("stop"), step("play", "x", True)],
                [step("stop"), step("play", "x", 51)], [step("play", "")], [None]):
        tools.new_turn()
        with patch.object(llm, "chat", return_value={"content": json.dumps({"steps": bad})}):
            assert minis.dj(db, "test") == {}
        assert not tools.DJ and tools.PENDING_MUSIC is None
    print("PASS ordered multi-step plans; malformed plans cause no partial action")
    tools.new_turn()
    edits = [step("remove", "Monody"), step("remove", "Unity"), step("skip", "", 3)]
    with patch.object(llm, "chat", return_value={"content": json.dumps({"steps": edits})}):
        minis.dj(db, "remove Monody and Unity then skip three")
    assert tools.DJ == [("remove", {"target": "Monody", "count": 1}),
                        ("remove", {"target": "Unity", "count": 1}),
                        ("skip", {"target": "", "count": 3})]


def batches():
    assert music._song_key("TheFatRat - Monody (feat. Laura Brehm)") == music._song_key("TheFatRat - Monody (Audio) ft. Laura Brehm")
    entries = [dict(id=f"video{i:06d}", title=f"TheFatRat Song {i}", duration=180) for i in range(3)]
    with patch.object(music, "_music_queries", return_value=["TheFatRat songs"]), \
         patch.object(music, "_search", return_value=entries), \
         patch.object(llm, "chat", return_value={"content": json.dumps(dict(indices=[2, 0, 2], query=""))}) as chat, \
         patch.object(music, "find", side_effect=AssertionError("must resolve at playback")):
        hits = music.find_many(dict(request="TheFatRat playlist", keywords="TheFatRat songs", count=5))
        assert [h["id"] for h in hits] == [entries[2]["id"], entries[0]["id"]]
        assert all(h["deferred"] for h in hits)
        assert chat.call_args.kwargs["fmt"]["required"] == ["indices", "query"]
    class YDL:
        def __init__(self, opts):
            assert not opts["noplaylist"] and opts["playlistend"] == 50
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def extract_info(self, *args, **kwargs):
            return {"entries": [None, *entries, entries[0],
                                dict(id="toolong0000", duration=10000),
                                dict(id="livestream1", is_live=True)]}
    with patch("yt_dlp.YoutubeDL", YDL):
        hits = music.find_many("https://www.youtube.com/playlist?list=PLfixture")
    assert [h["id"] for h in hits] == [h["id"] for h in entries]
    print("PASS batch evidence selection, deduplication, playlist order and lazy URLs")


async def deck():
    bot.db = memory.connect(":memory:")
    bot.player.db = bot.calendar.db = bot.db
    bot.client.loop = asyncio.get_running_loop()
    bot.player.lock = asyncio.Lock()
    music.NOW["title"] = None
    music.QUEUE.clear()
    sent, played = [], []
    class VC:
        after = None
        playing = False
        def is_playing(self): return self.playing
        def play(self, src, after):
            self.after, self.playing = after, True
            played.append(src.title)
        def stop(self):
            cb, self.after = self.after, None
            self.playing = False
            if cb: cb(None)
    vc = VC()
    async def send(text):
        sent.append(text)
        await asyncio.sleep(0)  # expose scheduling races
    channel = Obj(guild=Obj(id=1, voice_client=vc), send=send)
    def find_many(query):
        if query == "bad": raise LookupError("unavailable")
        time.sleep(0.01)
        names = ["Unity", "Monody", "Windfall"] if query == "artist" else [query]
        return [dict(title=n, url="fixture") for n in names]
    async def submit(*jobs):
        tools.new_turn().DJ.extend(jobs)
        await bot.player.flush(channel)
    with patch.object(music, "find_many", side_effect=find_many), \
         patch.object(music, "source_for", side_effect=lambda hit: Obj(title=hit["title"], cleanup=lambda: None)), \
         patch.object(music, "VOLUME", 1.0):
        await asyncio.gather(submit(("play", "artist")), submit(("play", "Judas")), submit(("play", "Miku")))
        assert played == ["Unity"], played
        assert [h["title"] for h in music.QUEUE] == ["Monody", "Windfall", "Judas", "Miku"]
        for expected in ("Monody", "Windfall", "Judas", "Miku"):
            vc.stop()
            await asyncio.sleep(0.03)
            assert music.NOW["title"] == expected
        print("PASS three concurrent listeners: no interruption/loss; FIFO auto-advance")
        old_callback = vc.after
        await submit(("stop", ""), ("play", "Replacement"), ("queue", "Next"))
        old_callback(None)
        await asyncio.sleep(0.03)
        assert music.NOW["title"] == "Replacement"
        assert [h["title"] for h in music.QUEUE] == ["Next"]
        await submit(("skip", ""))
        assert music.NOW["title"] == "Next"
        await submit(("stop", ""), ("play", "bad"), ("play", "Recovered"))
        assert music.NOW["title"] == "Recovered" and any("unavailable" in s for s in sent)
        print("PASS stop/play/queue order, skip, stale callback and failed search recovery")
        await submit(("stop", ""))
        music.QUEUE[:] = [dict(title="Deleted", id="deleted0000", deferred=True),
                          dict(title="Still available", id="available00", deferred=True)]
        with patch.object(music, "find", side_effect=[LookupError("deleted"),
                   dict(title="Still available", id="available00", url="fresh")]) as resolve:
            await bot.player.advance_after(channel)
            assert music.NOW["title"] == "Still available" and resolve.call_count == 2
        print("PASS unavailable queued track skipped; next track resolves fresh and plays")
        await submit(("stop", ""))
        music.QUEUE[:] = [dict(title="Blocked", id="blocked0000", deferred=True),
                          dict(title="Do not retry", id="retry000000", deferred=True)]
        with patch.object(music, "find", side_effect=LookupError("YouTube is rate-limiting this machine")) as resolve:
            await bot.player.advance_after(channel)
        assert resolve.call_count == 1 and not music.QUEUE and not music.NOW["title"]
        print("PASS playback rate limit clears pending songs without retry storm")
        started, release = threading.Event(), threading.Event()
        def slow(query):
            started.set()
            assert release.wait(5), "test search never released"
            return [dict(title=query, url="fixture")]
        with patch.object(music, "find_many", side_effect=slow):
            caller = asyncio.create_task(submit(("play", "Slow request")))
            while not started.is_set():
                await asyncio.sleep(.01)
            caller.cancel()  # simulate voice callback timeout during lookup
            try:
                await caller
            except asyncio.CancelledError:
                pass
            release.set()
            await asyncio.gather(*list(bot.player.tasks))
        assert music.NOW["title"] == "Slow request"
        await submit(("play", "Following speaker"))
        assert music.QUEUE[0]["title"] == "Following speaker"
        await submit(("stop", ""))
        print("PASS voice callback cancellation preserves accepted music and following requests")
        await submit(("play", "Current"), ("queue", "Monody"), ("queue", "Unity"),
                     ("queue", "Windfall"), ("queue", "Judas"))
        await submit(("remove", {"target": "Monody", "count": 1}),
                     ("remove", {"target": "Unity", "count": 1}))
        assert music.NOW["title"] == "Current"
        assert [h["title"] for h in music.QUEUE] == ["Windfall", "Judas"]
        await submit(("skip", {"target": "Judas", "count": 1}))
        assert music.NOW["title"] == "Current" and music.QUEUE[0]["title"] == "Windfall"
        await submit(("skip", {"target": "Missing song", "count": 1}))
        assert music.NOW["title"] == "Current" and len(music.QUEUE) == 1
        await submit(("skip", {"target": "Current", "count": 1}))
        assert music.NOW["title"] == "Windfall"
        await submit(("queue", "Unity"), ("queue", "Monody"), ("queue", "Judas"))
        before = len(played)
        await submit(("skip", {"target": "", "count": 3}))
        assert music.NOW["title"] == "Judas" and not music.QUEUE
        assert played[before:] == ["Judas"], "batch skip played unwanted intermediate songs"
        await submit(("queue", "A"), ("queue", "B"), ("queue", "C"))
        await submit(("remove", {"target": "", "count": 2}))
        assert music.NOW["title"] == "Judas" and music.QUEUE[0]["title"] == "C"
        await submit(("skip", ""))
        assert music.NOW["title"] == "C"
        await submit(("skip", ""))
        assert not music.NOW["title"] and not music.QUEUE
        assert music.queue_targets("Unity", ["TheFatRat - Unity (Official Audio)"]) == [0]
        assert music.queue_targets("Unity", ["Unity", "Unity"]) == [0, 1]
        assert not music.queue_targets("Love", ["Love Story", "Love is War"])
        assert not music.queue_targets("War", ["Warframe"])
        print("PASS multiple removals, named/current/queued skips, missing/ambiguous titles, counted and repeated skips")


if __name__ == "__main__":
    plans()
    batches()
    asyncio.run(deck())
