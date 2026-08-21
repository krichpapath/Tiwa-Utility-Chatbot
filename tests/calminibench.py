"""Calendar Tiwa: the judgment half. Offline.

Half of calendar was already right and does NOT move here — `gcal._EVENT_FORMAT`
parses the date (Thai titles, relative dates, Buddhist years all measured fine),
and the ✅ gate is the write. This is only the part that did not exist: which
Tuesday did they mean, does it clash, is it worth mentioning.

    py -X utf8 tests\\calminibench.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import gcal, llm, memory, minis, tools, turn  # noqa: E402

db = memory.connect(":memory:")
answer = {}
WEEK = "2026-08-25T15:00 — dentist\n2026-08-27T10:00 — standup"


def fake_chat(**kw):
    system = str(kw["messages"][0].get("content", ""))
    if "Krich's calendar" in system:
        return {"content": json.dumps(answer), "tool_calls": [], "raw": {}}
    return {"content": "ok whatever", "tool_calls": [], "raw": {}}


llm.chat = fake_chat
gcal.upcoming = lambda days=7: WEEK


def contract():
    spec = minis.MINIS["calendar"]
    assert spec["fields"] == frozenset({"action", "events", "queued", "ask", "clash"})
    assert "json" in minis._CAL_SYSTEM.lower(), "DeepSeek returns empty without it"
    assert "not a butler" in minis._CAL_SYSTEM, "she reads the diary at people without it"
    # every property required + no extras, or OpenRouter rejects the strict schema
    props = sorted(minis._CAL_FORMAT["properties"])
    assert sorted(minis._CAL_FORMAT["required"]) == props
    assert minis._CAL_FORMAT["additionalProperties"] is False
    print("contract ok — 5 facts, strict schema, and told she is not a butler")


def the_week_is_read_not_asked():
    answer.clear()
    answer.update(action="read", request="", ask="", clash="", mention=False)
    out = minis.run(db, "calendar", "what have I got on")
    assert WEEK in out["events"], out
    print("read ok     — the week reaches her without a tool call")


def a_write_only_queues():
    """The ✅ gate is the write. This mini decides WHAT to propose, never that it happens."""
    tools.new_turn()
    answer.clear()
    answer.update(action="write", request="add dentist Tuesday 15:00", ask="",
                  clash="", mention=False)
    out = minis.run(db, "calendar", "put dentist on tuesday")
    assert tools.PENDING_CALENDAR == ["add dentist Tuesday 15:00"], tools.PENDING_CALENDAR
    assert out["queued"] == "add dentist Tuesday 15:00"
    # nothing in this mini may touch the calendar itself
    src = (Path(__file__).parents[1] / "tiwa" / "minis.py").read_text(encoding="utf-8")
    for reach in ("gcal.apply_change", "_service(", "events().insert"):
        assert reach not in src, f"a mini can write to the calendar directly: {reach}"
    print("write ok    — queued for ✅ only; no mini can reach the calendar API")


def ambiguity_asks_and_changes_nothing():
    """A guess that lands in someone's calendar is worse than a question."""
    tools.new_turn()
    answer.clear()
    answer.update(action="write", request="add lunch next tuesday",
                  ask="this tuesday or the one after?", clash="", mention=False)
    out = minis.run(db, "calendar", "lunch next tuesday")
    assert out["action"] == "none", "it asked AND acted in the same breath"
    assert out["ask"] == "this tuesday or the one after?"
    assert tools.PENDING_CALENDAR == [], f"an ambiguous date queued anyway: {tools.PENDING_CALENDAR}"
    print("ask ok      — ambiguous date asks and queues nothing")


def a_clash_is_pointed_out_not_blocked():
    """Krich decides. She points."""
    tools.new_turn()
    answer.clear()
    answer.update(action="write", request="add gym Tuesday 15:00", ask="",
                  clash="dentist is already at 15:00 Tuesday", mention=False)
    out = minis.run(db, "calendar", "gym tuesday 3pm")
    assert out["clash"].startswith("dentist")
    assert tools.PENDING_CALENDAR == ["add gym Tuesday 15:00"], "a clash blocked the write"
    print("clash ok    — flagged, and still queued for him to decide")


def junk_degrades():
    tools.new_turn()
    for bad in ({}, {"action": "maybe"}, {"request": "add x"}):
        answer.clear()
        answer.update(bad)
        out = minis.run(db, "calendar", "put dentist on tuesday")
        assert set(out) <= minis.MINIS["calendar"]["fields"], out
        assert tools.PENDING_CALENDAR == [], f"{bad} queued something"
    print("degrade ok  — a junk decision queues nothing and never raises")


def she_speaks_first():
    """The ordering bug latebench could not see: its fake search was slower than
    its fake persona, so a FAST mini beating her out the door never happened."""
    order = []

    async def slow_say(db, hist, state):
        # _voice() calls say() too, to put the follow-up in her voice — that is
        # rule 1 working, not a second reply. Only the main call is timed.
        if "just this second found out" in state:
            return "oh — which tuesday?"
        await asyncio.sleep(0.25)
        order.append("reply")
        return "ok whatever"

    async def on_late(line):
        order.append("follow-up")

    answer.clear()
    answer.update(action="none", request="", ask="which tuesday did you mean?",
                  clash="", mention=False)

    async def go():
        was, turn.pipeline.say = turn.pipeline.say, slow_say
        try:
            import tiwa.minis as m
            route = {"dispatch": [{"mini": "calendar", "task": "lunch tuesday"}],
                     "ask": None}
            real_dispatch = m.dispatch

            async def fake_dispatch(*a, **k):
                return m.parse(json.dumps(route))

            m.dispatch = fake_dispatch
            try:
                await turn.respond(db, [{"role": "user", "content": "Krich: hi"}],
                                   "Krich", "lunch tuesday", on_late=on_late)
                await asyncio.sleep(0.4)
            finally:
                m.dispatch = real_dispatch
        finally:
            turn.pipeline.say = was
            turn._cancel_pending("Krich")

    asyncio.run(go())
    assert order == ["reply", "follow-up"], f"she answered before she spoke: {order}"
    print("order ok    — the reply always lands before the follow-up")


contract()
the_week_is_read_not_asked()
a_write_only_queues()
ambiguity_asks_and_changes_nothing()
a_clash_is_pointed_out_not_blocked()
junk_degrades()
she_speaks_first()
print("\ncalendar ok — judgment only; the parse and the ✅ gate never moved")
