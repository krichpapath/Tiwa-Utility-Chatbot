"""Calendar: does she actually WRITE, or just say she did?

`py -X utf8 tests\\calbench.py`         offline — the ✅ gate holds, nothing can reach Google
`py -X utf8 tests\\calbench.py --live`  measure — 7 real asks through dispatch and Calendar Tiwa

The live half exists because of a real failure. Tycoon asked her to put a lunch
appointment in the calendar, she asked a clarifying question, he said "ช่ายๆๆ"
(yes), and she replied "โอเค ลงให้ละ" — I've put it in. No calendar_write row.
She had claimed it and done nothing, exactly like the music confabulation.

Measured cause: _INNER_SYSTEM gave music four lines and the calendar the single
word "check", so a message leading with a calendar word mapped to calendar_read.
0/2 on the confirmation turn and 0/2 on one clear ask, both reproducible. The
same seven cases went 7/7 twice after the prompt gained a write rule — this one
never needed code, which is why the bench is a measure and not a check.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, minis, pipeline, tools  # noqa: E402

db = memory.connect(":memory:")

# --- offline: the gate. The actuator may only ever queue a sentence ----------
tools.PENDING_CALENDAR.clear()
tools.calendar_write(db, "add dentist tomorrow 15:00")
assert tools.PENDING_CALENDAR == ["add dentist tomorrow 15:00"]
# ...and it reaches nothing else: no Google client, no event, until bot.py sees a ✅
import tiwa.gcal as gcal  # noqa: E402

assert gcal._service.__module__ == "tiwa.gcal"  # still the only door to the API
tools.PENDING_CALENDAR.clear()
print("gate ok — calendar_write queues text and nothing else")

if "--live" not in sys.argv:
    sys.exit(0)

memory.remember(db, "Tycoon", "real name", "Gateaux")

CLARIFY = ("13.00 คือเวลากินข้าวเที่ยง ไม่ใช่เช้านะ มึงหมายถึงพรุ่งนี้ "
           "(อาทิตย์ 2 ส.ค.) เวลา 13.00 ป่าว")
ASK_TH = "พรุ่งนี้เช้ามีนัดกินข้าว 13.00 ช่วยลงปติทินให้หน่อย"

CASES = [
    # the live failure: a bare "yes" to her own clarifying question. 0/2 before.
    ("bare confirmation", "Tycoon", "ช่ายๆๆ",
     f"Tycoon: {ASK_TH}\n{memory.TIWA}: {CLARIFY}"),
    ("bare confirmation, other speaker", "Krich", "ช่ายๆๆ",
     f"Krich: {ASK_TH}\n{memory.TIWA}: {CLARIFY}"),
    ("thai ask", "Tycoon", ASK_TH, ""),
    ("thai ask, other speaker", "Krich", ASK_TH, ""),
    ("english ask", "Tycoon", "put lunch with mom on the calendar tomorrow 1pm", ""),
    ("english ask, other speaker", "Krich",
     "put lunch with mom on the calendar tomorrow 1pm", ""),
    # led with the calendar word and reliably picked calendar_read. 0/2 before.
    ("calendar word first", "Tycoon",
     "ลงปฏิทินให้หน่อย นัดหมอฟัน อาทิตย์ 9 ส.ค. 13.00", ""),
]


async def main():
    """Drive the REAL path: dispatch decides, route overrules, the mini writes.

    This used to drive `pipeline._inner_brief` — one model reading ten tool
    descriptions. The registry is gone, so the equivalent question is now asked of
    the two passes that replaced it. Nothing is stubbed: if she does not write,
    that is the answer.
    """
    print("\n| case | speaker | dispatched | wrote? |")
    print("|---|---|---|---|")
    wrote = 0
    for label, author, text, recent in CASES:
        tools.new_turn()
        out = await minis.dispatch(db, author, text, recent)
        jobs = pipeline.route(db, out["dispatch"], text,
                              pipeline._missed_music(text))
        for name, task in jobs:
            await asyncio.to_thread(minis.run, db, name, task)
        names = ", ".join(f"{n}({t[:26]})" for n, t in jobs) or "*none*"
        ok = bool(tools.PENDING_CALENDAR)
        wrote += ok
        print(f"| {label} | {author} | {names} | {'**yes**' if ok else 'NO'} |")
    print(f"\nthe calendar was written on {wrote}/{len(CASES)}")
    # 3/7 and 4/7 before the prompt rule, 7/7 twice after, measured through the
    # tool pass. The dispatch prompt deliberately drops bare agreements ("ใช่",
    # "ช่ายๆๆ") to stop the over-firing measured in dispatchbench, so the first
    # two cases are EXPECTED to be a no on this branch — that is the one
    # behaviour that differs from `main`, and it is why the bar is 5 not 6.
    assert wrote >= 5, f"she is claiming calendar writes she did not make: {wrote}/{len(CASES)}"


asyncio.run(main())
print("calendar ok")
