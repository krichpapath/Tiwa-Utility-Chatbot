"""Calendar text approval guards; no live writes."""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
from tiwa import gcal


async def main():
    ch = Obj(id=123, send=AsyncMock())
    msg = Obj(channel=ch, author=Obj(id=42), reference=None)
    plan = dict(action="add", title="QA", start="2026-09-17T13:00:00")

    def pending(mid=1, expires=None):
        bot.calendar.pending[mid] = (plan, ch.id, expires or time.monotonic() + 600)

    with (
        patch.object(bot.calendar, "owner_id", "42"),
        patch.object(gcal, "apply_change", return_value="added: QA") as apply,
    ):
        bot.calendar.pending.clear()
        pending()
        msg.author.id = 99
        await bot.calendar.text(msg, "confirm")
        apply.assert_not_called()
        assert 1 in bot.calendar.pending
        msg.author.id = 42
        await bot.calendar.text(msg, "Confirm สิ")
        apply.assert_called_once_with(plan)
        await bot.calendar.text(msg, "confirm")
        assert apply.call_count == 1
        pending()
        await bot.calendar.text(msg, "cancel")
        assert not bot.calendar.pending
        pending(expires=time.monotonic() - 1)
        await bot.calendar.text(msg, "confirm")
        assert apply.call_count == 1
        pending()
        pending(2)
        await bot.calendar.text(msg, "confirm")
        assert len(bot.calendar.pending) == 2
        msg.reference = Obj(message_id=2)
        await bot.calendar.text(msg, "confirm")
        assert 1 in bot.calendar.pending and 2 not in bot.calendar.pending
        assert apply.call_count == 2
        pending(3)
        bot.calendar.pending.pop(1, None)
        await bot._heard("Owner display name", "confirm", channel=ch, activated=True, user_id=99)
        assert 3 in bot.calendar.pending and apply.call_count == 2
        await bot._heard(
            "Owner display name", "เหตุที่ว่าคอนเฟิร์ม", channel=ch, activated=True, user_id=42
        )
        assert 3 not in bot.calendar.pending and apply.call_count == 3
        pending()
        apply.side_effect = RuntimeError("offline")
        msg.reference = Obj(message_id=1)
        await bot.calendar.text(msg, "confirm")
        assert "failed" in ch.send.call_args.args[0]
    bot.calendar.pending.clear()
    from tiwa import tools

    tools.new_turn()
    ch.send.return_value = Obj(id=555)
    with (
        patch.object(bot.calendar, "owner_id", "999"),
        patch.object(gcal, "prepare_change", return_value=gcal.validate_change(plan)),
        patch.object(gcal, "apply_change", return_value="added: QA") as apply,
    ):
        tools.PENDING_CALENDAR.extend(["request", "same request"])
        await bot.calendar.propose(ch, 42)
        assert len(bot.calendar.pending) == 1
        await bot._heard("Owner", "ให้ที่วาคอนเฟิร์ม", channel=ch, activated=True, user_id=42)
        apply.assert_called_once()
        assert not bot.calendar.pending
        await bot._heard("Owner", "เหตุที่ว่าคอนเฟิร์ม", channel=ch, activated=True, user_id=42)
        apply.assert_called_once()
    with (
        patch.dict("os.environ", {"TIWA_CALENDAR_ID": "test-calendar"}),
        patch.object(gcal, "_service") as service,
    ):
        ev = service.return_value.events.return_value
        ev.list.return_value.execute.return_value = {"items": []}
        gcal.upcoming()
        assert ev.list.call_args.kwargs["calendarId"] == "test-calendar"
        ev.insert.return_value.execute.return_value = {"summary": "QA"}
        gcal.apply_change(plan)
        assert ev.insert.call_args.kwargs["calendarId"] == "test-calendar"
    print(
        "PASS owner, text/Thai, cancel, expiry, duplicates, multiple proposals, error reporting, custom calendar reads/writes"
    )


for phrase in (
    "Confirm สิ",
    "ยืนยันเลยครับ",
    "confirm please",
    "คอนเฟิร์ม",
    "เหตุที่ว่าคอนเฟิร์ม",
    "ให้ที่วาคอนเฟิร์ม",
    "Hey Tiwa, confirm",
    "Hey Tiwa ยืนยัน",
):
    assert bot.calendar.decision(phrase) is True
for phrase in ("cancel please", "ยกเลิกเลย"):
    assert bot.calendar.decision(phrase) is False
for phrase in (
    "do not confirm",
    "confirm tomorrow",
    "confirm?",
    "ยืนยันไหม",
    "เหตุที่ว่าไม่ต้องคอนเฟิร์ม",
    "Hey Tiwa confirm tomorrow",
):
    assert bot.calendar.decision(phrase) is None
asyncio.run(main())
from tiwa.listening import FOLLOWUPS
from groupvoicebench import fixture, pcm

ears = fixture()
FOLLOWUPS.clear()
FOLLOWUPS[42] = time.monotonic() + 60
clips = {}
for tick in range(32):
    for uid in (42, 99):
        out = ears.process(uid, "same name", pcm(300 if tick < 21 else 0), tick * 0.08)
        if out:
            clips[uid] = out
assert set(clips) == {42}
assert 42 not in FOLLOWUPS
print("PASS wake-free next utterance only for requesting account")
