"""She is asked for music WITHOUT being given a song name — "pick something".

The failing case from real use: "อยากได้เพลงเล่น Marvel rival เลือกให้หน่อย มันๆ"
-> she answered "what genre?" and played nothing, three turns in a row.

    py -X utf8 tests\\pickbench.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, pipeline, tools  # noqa: E402

# (message, should she start a search?)
CASES = [
    ("อยากได้เพลงเล่น Marvel rival เลือกให้หน่อย มันๆ", True),
    ("ขอเพลงฟังตอนทำงานหน่อย", True),
    ("เปิดเพลงอะไรก็ได้", True),
    ("มีเพลงแนะนำมั้ย เปิดให้ฟังหน่อย", True),
    ("play something chill for studying", True),
    ("put on whatever you like", True),
    # controls: talking ABOUT music is not asking for music
    ("เพลงนี้ชื่ออะไร", False),
    ("มึงชอบฟังเพลงแนวไหน", False),
]


async def main():
    db = memory.connect(":memory:")
    # She must already know the asker. On an empty memory the inner pass spends
    # the turn on "who is this stranger" and never reaches the request — that
    # alone cost 5 of 6 asks in one measured run.
    memory.remember(db, "Tycoon", "name is", "กาโตว์")
    memory.remember(db, "Krich", "friend", "Tycoon")
    good = 0
    print("| asked | searched for | she said |")
    print("|---|---|---|")
    for text, want in CASES:
        tools.DJ.clear()
        tools.PENDING_MUSIC = None
        reply = await pipeline.respond(
            db, [{"role": "user", "content": f"Tycoon: {text}"}], "Tycoon", text)
        queued = ([tools.PENDING_MUSIC] if tools.PENDING_MUSIC else []) + \
                 [a for _, a in tools.DJ]
        ok = bool(queued) == want
        good += ok
        mark = "" if ok else "  <- WRONG"
        print(f"| {text} | {queued or 'nothing'}{mark} | {reply[:70]} |")
    print(f"\n{good}/{len(CASES)} correct")
    assert good == len(CASES), "music recommendations or non-music veto regressed"
    tools.PENDING_MUSIC = None
    tools.DJ.clear()


asyncio.run(main())
