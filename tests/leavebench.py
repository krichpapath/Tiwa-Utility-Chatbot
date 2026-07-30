"""She can walk out of a call, and does not confuse it with stopping the music.

Only the inner (tool) pass runs — that is where the decision lives, and it costs
half a turn.

    py -X utf8 tests\\leavebench.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, pipeline, tools, voice  # noqa: E402

# (message, should she leave?, should the music stop?)
CASES = [
    ("ออกไปได้แล้ว", True, False),
    ("ทิวาออกจากห้องเสียงเลย", True, False),
    ("get out of the vc", True, False),
    ("หยุดเพลง", False, True),          # music off, she STAYS
    ("stop the music", False, True),
    ("ปิดเพลงแล้วออกไปเลย", True, True),  # both, in one sentence
    ("มึงอยู่ไหน", False, False),          # just chat
]


async def main():
    db = memory.connect(":memory:")
    memory.remember(db, "Tycoon", "name is", "กาโตว์")
    good = 0
    print("| asked | left | music stopped | ok |")
    print("|---|---|---|---|")
    for text, want_leave, want_stop in CASES:
        tools.PENDING_LEAVE = False
        tools.PENDING_MUSIC = None
        tools.DJ.clear()
        await pipeline._inner_brief(db, "Tycoon", text)
        left = tools.PENDING_LEAVE
        stopped = tools.PENDING_MUSIC == ""
        ok = left == want_leave and stopped == want_stop
        good += ok
        print(f"| {text} | {left} | {stopped} | {'ok' if ok else 'WRONG'} |")

    # the future-tense veto, decided in code — a wrong leave drops her out of a
    # live call, so the tool never gets the last word
    print("\n| future-tense ask | acted on? |\n|---|---|")
    FUTURE = ("ออกไปตอนดึกนะ", "leave the call later tonight", "ออกไปทีหลัง",
              "ค่อยออกไปก็ได้", "ออกไปสักพัก")
    NOW = ("ออกไปได้แล้ว", "get out of the vc", "ทิวาออกไปเลย")
    for text in FUTURE + NOW:
        now = voice.wants_now(text)
        print(f"| {text} | {'yes' if now else 'no — vetoed'} |")
    # asserted, not just printed: this used to pass "ออกไปตอนดึกนะ" straight
    # through and hang up on a live call
    for text in FUTURE:
        assert not voice.wants_now(text), f"future ask not vetoed: {text}"
    for text in NOW:
        assert voice.wants_now(text), f"real ask vetoed: {text}"

    tools.PENDING_LEAVE = False
    tools.PENDING_MUSIC = None
    print(f"\n{good}/{len(CASES)} correct")


asyncio.run(main())
