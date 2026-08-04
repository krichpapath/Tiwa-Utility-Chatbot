"""Calendar: does she actually WRITE, or just say she did?

`py -X utf8 tests\\calbench.py`         offline — the ✅ gate holds, nothing can reach Google
`py -X utf8 tests\\calbench.py --live`  measure — 7 real asks through the tool pass

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
from tiwa import memory, pipeline, tools  # noqa: E402

db = memory.connect(":memory:")

# --- offline: the gate. A tool may only ever queue a sentence ----------------
tools.PENDING_CALENDAR.clear()
out = tools.TOOLS["calendar_write"]["fn"](db, "add dentist tomorrow 15:00")
assert "confirm" in out, out
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

called = []
_real = {n: t["fn"] for n, t in tools.TOOLS.items()}
for _name in tools.TOOLS:
    def _spy(db, arg, _n=_name):
        called.append((_n, arg))
        return _real[_n](db, arg)

    tools.TOOLS[_name]["fn"] = _spy


async def main():
    print("\n| case | speaker | tools called | wrote? |")
    print("|---|---|---|---|")
    wrote = 0
    for label, author, text, recent in CASES:
        called.clear()
        tools.PENDING_CALENDAR.clear()
        await pipeline._inner_brief(db, author, text, recent)
        names = ", ".join(f"{n}({a[:26]})" for n, a in called) or "*none*"
        ok = bool(tools.PENDING_CALENDAR)
        wrote += ok
        print(f"| {label} | {author} | {names} | {'**yes**' if ok else 'NO'} |")
    print(f"\ncalendar_write fired on {wrote}/{len(CASES)}")
    # 3/7 and 4/7 before the prompt rule, 7/7 twice after. Anything under 6 is a
    # regression in _INNER_SYSTEM, not noise.
    assert wrote >= 6, f"she is claiming calendar writes she did not make: {wrote}/{len(CASES)}"


asyncio.run(main())
print("calendar ok")
