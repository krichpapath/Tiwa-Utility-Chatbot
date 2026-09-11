"""Two turns at once must not read each other's flags.

`PENDING_MUSIC`, `DJ`, `PENDING_CALENDAR`, `PENDING_JOIN` and `PENDING_LEAVE`
were module globals. `locks[channel.id]` in bot.py serializes replies WITHIN a
channel, so nothing caught it — but two channels, or a text turn and a voice
turn, run as separate asyncio Tasks and shared all five. docs/concepts/tools.md
called it: "the day she's in two calls at once, this is the thing that breaks."

They live on a contextvars.ContextVar now (tiwa/tools.py). This drives the real
path: tools run inside asyncio.to_thread, so the fix only works if the context
survives the thread hop.

    py -X utf8 tests\\turnbench.py

No Discord, no network, no model.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, tools  # noqa: E402

db = memory.connect(":memory:")


async def turn(song: str, pause: float):
    """One turn, shaped like pipeline.respond(): new_turn, then tools in threads."""
    tools.new_turn()
    await asyncio.to_thread(tools.play_music, db, song)
    await asyncio.sleep(pause)  # the other turn runs its tools in here
    await asyncio.to_thread(tools.queue_music, db, f"{song} pt2")
    await asyncio.to_thread(tools.calendar_write, db, f"{song} listening party")
    await asyncio.sleep(pause)
    # read through the OLD module names — bot.py, chat.py, dashboard.py and seven
    # benches still say tools.PENDING_MUSIC, and all of them had to keep working
    return {
        "music": tools.PENDING_MUSIC,
        "dj": list(tools.DJ),
        "calendar": list(tools.PENDING_CALENDAR),
    }


async def concurrent():
    """The bug, driven directly. Different pauses force the interleave."""
    a, b = await asyncio.gather(turn("lofi", 0.02), turn("metal", 0.01))

    print(f"{'turn':6} | {'PENDING_MUSIC':14} | {'DJ':22} | PENDING_CALENDAR")
    print(f"{'-'*6}-+-{'-'*14}-+-{'-'*22}-+-{'-'*24}")
    for name, r in (("lofi", a), ("metal", b)):
        print(f"{name:6} | {str(r['music']):14} | {str(r['dj']):22} | {r['calendar']}")

    assert a["music"] == "lofi", f"lofi turn saw {a['music']!r}"
    assert b["music"] == "metal", f"metal turn saw {b['music']!r}"
    assert a["dj"] == [("queue", "lofi pt2")], a["dj"]
    assert b["dj"] == [("queue", "metal pt2")], b["dj"]
    assert a["calendar"] == ["lofi listening party"], a["calendar"]
    assert b["calendar"] == ["metal listening party"], b["calendar"]
    print("\nconcurrent ok — neither turn saw the other's music, DJ list or calendar")


async def thread_hop():
    """A tool mutating from inside to_thread must be visible to the caller.

    asyncio.to_thread COPIES the context, so mutating the Turn object propagates
    but a _TURN.set() inside the thread would not. That is why new_turn() belongs
    in respond()/idle() on the event loop, never inside a tool.
    """
    tools.new_turn()
    # play_music, not join_voice: under TIWA_VOICE=dj the voice tools are inert
    # by design, and a tool that sets nothing proves nothing about the thread hop.
    await asyncio.to_thread(tools.play_music, db, "bad apple")
    assert tools.PENDING_MUSIC == "bad apple", "flag set in a worker thread never came back"
    print("thread ok  — a flag set inside to_thread reaches the caller")


def sequential():
    """The heartbeat is ONE long-lived task, so its context outlives a tick."""
    tools.new_turn()
    tools.play_music(db, "first")
    tools.current().PENDING_LEAVE = True  # set directly: the tool is inert under
    #                                       TIWA_VOICE=dj, and this bench is about
    #                                       the Turn's isolation, not about voice
    assert tools.PENDING_MUSIC == "first"
    assert tools.PENDING_LEAVE is True

    tools.new_turn()
    assert tools.PENDING_MUSIC is None, "last turn's song leaked into the next one"
    assert tools.PENDING_LEAVE is False, "last turn's leave flag leaked"
    assert tools.DJ == [] and tools.PENDING_CALENDAR == []
    print("reset ok   — a new turn starts empty in the same task")


def writeable():
    """bot.player.flush assigns `tools.PENDING_MUSIC = None` to drain the deck.

    A plain module __getattr__ would let that assignment create a real global
    that shadows the shim forever — silently, and only on the flush path. The
    module's __setattr__ is what stops it.
    """
    tools.new_turn()
    tools.play_music(db, "bad apple")
    tools.PENDING_MUSIC = None
    assert tools.PENDING_MUSIC is None
    assert "PENDING_MUSIC" not in vars(sys.modules["tiwa.tools"]), \
        "assignment leaked a real module global — the shim is bypassed from here on"
    print("write ok   — the old names stay writable without shadowing the shim")


sequential()
writeable()
asyncio.run(thread_hop())
asyncio.run(concurrent())
