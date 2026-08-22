"""Typed music requests, mostly Thai — does she call play_music with a query
that actually finds the song?

    py -X utf8 tests\\musicbench.py           # tool choice only
    py -X utf8 tests\\musicbench.py --search  # also hit YouTube for real
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, music, pipeline, tools  # noqa: E402

# what you type, which tool should fire, what the query must be about
CASES = [
    ("เปิดเพลงRick rollให้หน่อย", "play_music", "rick"),
    ("เปิดเพลงหน่อย", "play_music", None),
    ("เปิดเพลง Bodyslam ให้ที", "play_music", "bodyslam"),
    ("อยากฟังเพลงลูกทุ่ง", "play_music", None),
    ("play some lofi to study to", "play_music", "lofi"),
    ("put on Never Gonna Give You Up", "play_music", "never gonna"),
    ("หยุดเพลง", "stop_music", None),
    ("stop the music", "stop_music", None),
    ("เพลงนี้ชื่ออะไร", None, None),          # asking, not requesting
    ("มึงชอบเพลงแนวไหน", None, None),          # small talk about music
]


async def main():
    db = memory.connect(":memory:")
    rows, bad = [], 0
    for text, want_tool, want_in in CASES:
        tools.PENDING_MUSIC = None
        seen = []
        for name in ("play_music", "stop_music"):
            real = tools.TOOLS[name]["fn"]
            tools.TOOLS[name]["fn"] = (
                lambda db, arg, _n=name, _r=real: (seen.append((_n, arg)), _r(db, arg))[1]
            )
        await pipeline._inner_brief(db, "Krich", text)
        got_tool = seen[0][0] if seen else None
        query = seen[0][1] if seen else ""
        ok = got_tool == want_tool and (
            not want_in or want_in in query.lower())
        bad += not ok
        rows.append((text, got_tool, query, ok))
    print("| you type | tool called | query | ok |")
    print("|---|---|---|---|")
    for text, tool, query, ok in rows:
        print(f"| {text} | {tool or '—'} | {query or '—'} | {'yes' if ok else '**NO**'} |")
    print(f"\n{len(CASES) - bad}/{len(CASES)} correct")

    if "--search" in sys.argv:
        # a song, not a 3-hour mix. YouTube's top hit for a mood query is always
        # a mix, which outlives the conversation and starves the queue.
        print("\n| query | youtube found | mins | song? |")
        print("|---|---|---|---|")
        live = long = 0
        for _, tool, query, _ in rows:
            if tool != "play_music" or not query:
                continue
            try:
                hit = await asyncio.to_thread(music.find, query)
            except Exception as e:
                print(f"| {query} | FAILED {type(e).__name__} | — | **NO** |")
                live += 1
                continue
            secs = hit["duration"]
            live += secs == 0
            long += secs > music.MAX_TRACK_S
            verdict = ("**LIVESTREAM**" if not secs
                       else "mix" if secs > music.MAX_TRACK_S else "yes")
            print(f"| {query} | {hit['title'][:48]} | {secs / 60:.0f} | {verdict} |")
        if long:
            print(f"\n{long} query had no song on YouTube and got a mix that at least "
                  "ends. Judge whether the query itself was mix-shaped.")

        # the repeat: same words twice must not hand back the same video
        first = await asyncio.to_thread(music.find, "เพลงมันๆ")
        second = await asyncio.to_thread(music.find, "เพลงมันๆ")
        print(f"\nsame query twice: {first['title'][:40]} -> {second['title'][:40]}")
        assert first["id"] != second["id"], "same video twice for one query"

        # the F1 path re-resolves the track it is ALREADY playing. It goes by
        # video id, never by re-running the search, or it lands on a new song.
        again = await asyncio.to_thread(music.find, music.watch_url(first["id"]))
        assert again["id"] == first["id"], "re-resolve swapped the song mid-play"
        print("re-resolve returned the original video, not a new one")
        # a livestream never ends, so the queue behind it never plays. That one is
        # a hard failure; a long mix is only a disappointment.
        assert live == 0, f"{live} queries returned a livestream"
    assert bad == 0, f"{bad} cases wrong"


def dead_candidate():
    """One unavailable video must not lose the live results behind it. Offline.

    Live log 2026-08-23: "[youtube] 5Z8N9TTvKeQ: This video is not available"
    walked straight out of find() and the whole music request died, while three
    good candidates sat untried. extract_info() was the only unguarded call in
    the loop — it was written to survive bad metadata, not a dead video.
    """
    import types

    calls = []

    class FakeYDL:
        def __init__(self, opts):
            self.flat = opts.get("extract_flat")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, target, download=False):
            if self.flat:  # the search page: five results, all plausible
                return {"entries": [
                    {"id": f"vid{i}", "url": f"http://y/vid{i}", "duration": 200,
                     "title": f"song {i}"} for i in range(5)]}
            calls.append(target)
            if target.endswith(("vid0", "vid1")):        # dead: deleted, private
                raise RuntimeError("ERROR: [youtube] vid: This video is not available")
            # _hit() requires "url" — the resolved stream url, not the page
            return {"id": "vid2", "title": "the live one", "duration": 200,
                    "url": "http://stream/vid2"}

    fake = types.SimpleNamespace(YoutubeDL=FakeYDL, utils=types.SimpleNamespace())
    real = sys.modules.get("yt_dlp")
    sys.modules["yt_dlp"] = fake
    try:
        hit = music.find("something with two dead results")
        assert hit["id"] == "vid2", hit
        assert len(calls) == 3, f"stopped early: tried {calls}"
        print(f"dead-candidate ok — 2 unavailable, skipped, landed on {hit['title']!r}")

        # ...and when EVERY candidate is dead it must raise LookupError, which
        # bot._find already turns into "couldn't find that" in her voice — not
        # crash the turn and not return None for `hit["id"]` to choke on.
        class AllDead(FakeYDL):
            def extract_info(self, target, download=False):
                if self.flat:
                    return FakeYDL.extract_info(self, target, download)
                raise RuntimeError("ERROR: [youtube] gone: This video is not available")

        fake.YoutubeDL = AllDead
        try:
            music.find("everything is dead")
            raise AssertionError("all-dead search returned instead of raising")
        except LookupError as e:
            assert "unavailable" in str(e), e
        print("all-dead ok    — raises LookupError, which bot._find already reports")
    finally:
        if real is not None:
            sys.modules["yt_dlp"] = real
        else:
            del sys.modules["yt_dlp"]


dead_candidate()
asyncio.run(main())
