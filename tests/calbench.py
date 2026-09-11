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
import os
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

if os.environ.get("TIWA_QA_FIXTURES") == "1":
    gcal.upcoming = lambda days=7: "calendar empty for the next 7 days"

assert gcal._service.__module__ == "tiwa.gcal"  # still the only door to the API
tools.PENDING_CALENDAR.clear()
print("gate ok — calendar_write queues text and nothing else")

if "--live" not in sys.argv:
    sys.exit(0)

memory.remember(db, "Tycoon", "real name", "Gateaux")

CLARIFY = "13.00 คือเวลากินข้าวเที่ยง ไม่ใช่เช้านะ มึงหมายถึงพรุ่งนี้ (อาทิตย์ 2 ส.ค.) เวลา 13.00 ป่าว"
ASK_TH = "พรุ่งนี้เช้ามีนัดกินข้าว 13.00 ช่วยลงปติทินให้หน่อย"

CASES = [
    # the live failure: a bare "yes" to her own clarifying question. 0/2 before.
    ("bare confirmation", "Tycoon", "ช่ายๆๆ", f"Tycoon: {ASK_TH}\n{memory.TIWA}: {CLARIFY}"),
    (
        "bare confirmation, other speaker",
        "Krich",
        "ช่ายๆๆ",
        f"Krich: {ASK_TH}\n{memory.TIWA}: {CLARIFY}",
    ),
    ("thai ask", "Tycoon", ASK_TH, ""),
    ("thai ask, other speaker", "Krich", ASK_TH, ""),
    ("english ask", "Tycoon", "put lunch with mom on the calendar tomorrow 1pm", ""),
    ("english ask, other speaker", "Krich", "put lunch with mom on the calendar tomorrow 1pm", ""),
    # led with the calendar word and reliably picked calendar_read. 0/2 before.
    ("calendar word first", "Tycoon", "ลงปฏิทินให้หน่อย นัดหมอฟัน อาทิตย์ 9 ส.ค. 13.00", ""),
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
    handled = 0
    for label, author, text, recent in CASES:
        tools.new_turn()
        out = await minis.dispatch(db, author, text, recent)
        print(f"  routing: {out}")
        jobs = pipeline.route(db, out["dispatch"], text)
        results = [await asyncio.to_thread(minis.run, db, name, task) for name, task in jobs]
        names = ", ".join(f"{n}({t[:26]})" for n, t in jobs) or "*none*"
        ok = bool(tools.PENDING_CALENDAR)
        wrote += ok
        # Contradictory morning/13:00 or an already-past August date should clarify.
        clarified = any(r.get("ask") for r in results)
        handled += ok or clarified
        if label.startswith("english ask"):
            assert ok, "tomorrow 1pm is explicit: must queue a proposal"
        print(f"| {label} | {author} | {names} | {'**yes**' if ok else 'NO'} |")
    print(f"\ncalendar proposals queued on {wrote}/{len(CASES)}; no Google writes")
    # Historical ambiguous/past-date requests may clarify. Explicit tomorrow/1pm
    # requests must queue; those are asserted above rather than hidden in a score.
    assert handled >= 5, f"calendar requests silently dropped: {handled}/{len(CASES)} handled"
    print(f"{handled}/{len(CASES)} queued or explicitly clarified")


asyncio.run(main())
print("calendar ok")
