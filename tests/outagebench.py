"""A dead model must not make her mute. Offline.

Live failure, 2026-08-23: an expired key meant every turn raised 401 inside
`pipeline.respond()`. discord.py logged the traceback to the console and the
CHANNEL SHOWED NOTHING — someone typed at her and she simply did not answer.
Tools have taken the opposite stance since July ("search failed: …" comes back as
text, never raised); the turn itself never did.

Three call sites, three different silences:

  on_message  -> traceback in the console, nothing in the channel
  _heard      -> nothing anywhere; nobody watches a voice console
  idle_turn   -> WORSE: tasks.loop stops the loop after one exception unless an
                 error handler exists, so a single outage made her silent until
                 the process restarted. docs/open-questions.md carried this as
                 the one open bug since July.

    py -X utf8 tests\\outagebench.py

No Discord, no network, no model.
"""
import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
import httpx  # noqa: E402

from tiwa import memory  # noqa: E402

import bot  # noqa: E402  (imports discord, does not connect)

bot.db = memory.connect(":memory:")
sent = []


class FakeChannel:
    id = 1
    guild = types.SimpleNamespace(id=1, voice_client=None)

    async def send(self, text):
        sent.append(text)

    def typing(self):
        class _N:
            async def __aenter__(s): return s
            async def __aexit__(s, *a): return False
        return _N()


def a_401() -> Exception:
    """The real one: httpx.HTTPStatusError, whose str() carries the code."""
    req = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    resp = httpx.Response(401, request=req)
    return httpx.HTTPStatusError("Client error '401 Unauthorized'",
                                 request=req, response=resp)


def she_says_why():
    """Not in her voice — the model is what failed, so there is nothing to write
    her line with, and faking one is the bluffing failure in a different hat."""
    ch = FakeChannel()
    for err, must in ((a_401(), "401"),
                      (httpx.ConnectError("nope"), "cannot reach"),
                      (ValueError("something else"), "ValueError")):
        sent.clear()
        asyncio.run(bot._apologise(ch, err))
        assert sent, f"{type(err).__name__} said nothing at all"
        assert must in sent[0], f"{sent[0]!r} does not explain {must}"
        print(f"  {type(err).__name__:18} -> {sent[0][:72]}")
    rows = [t for _, k, t, _ in memory.read_log(bot.db, 20) if k == "error"]
    assert len(rows) == 3, f"failures not logged: {rows}"
    print("apologise ok — every failure reaches the channel AND the log")


def the_heartbeat_survives():
    """The loop must still be running after a tick raises. That is the whole bug:
    one outage used to stop it for good."""
    # Pin the clock. idle_turn returns early outside 09:00-23:00, so at midnight
    # this test passes without ever reaching the guard — which is exactly what it
    # did on the first run.
    import datetime as _dt

    class Noon:
        min = _dt.datetime.min

        @staticmethod
        def now():
            return _dt.datetime(2026, 8, 23, 12, 0)

    was_dt, bot.datetime = bot.datetime, Noon
    bot.last_unprompted = _dt.datetime.min
    bot.HOME = "1"

    async def boom(db):
        raise a_401()

    was, bot.pipeline.idle = bot.pipeline.idle, boom
    try:
        for _ in range(3):  # three ticks of a real outage
            asyncio.run(bot.idle_turn.coro())
    finally:
        bot.pipeline.idle = was
        bot.datetime = was_dt

    rows = [t for _, k, t, _ in memory.read_log(bot.db, 20) if k == "error"]
    beats = [r for r in rows if "heartbeat" in r]
    assert len(beats) == 3, f"expected 3 survived ticks, logged {len(beats)}"
    print(f"heartbeat ok — 3 outages, 3 log rows, loop still alive")


def no_bare_respond_left():
    """A fourth call site added later would reintroduce the silence."""
    src = (Path(__file__).parents[1] / "bot.py").read_text(encoding="utf-8")
    calls = src.count("await pipeline.respond(")
    guarded = src.count("_apologise(")
    assert calls == 2, f"bot.py has {calls} respond() call sites, expected 2"
    # one definition + one use per call site
    assert guarded >= calls + 1, "a respond() call site has no _apologise() beside it"
    print(f"sites ok    — {calls} turn call sites, every one guarded")


she_says_why()
the_heartbeat_survives()
no_bare_respond_left()
print("\noutage ok — a dead provider costs her a turn, never her voice")
