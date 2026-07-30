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
        print("\n| query | youtube found |")
        print("|---|---|")
        for _, tool, query, _ in rows:
            if tool == "play_music" and query:
                try:
                    hit = await asyncio.to_thread(music.find, query)
                    print(f"| {query} | {hit['title'][:60]} ({hit['duration']}s) |")
                except Exception as e:
                    print(f"| {query} | FAILED {type(e).__name__} |")
    assert bad == 0, f"{bad} cases wrong"


asyncio.run(main())
