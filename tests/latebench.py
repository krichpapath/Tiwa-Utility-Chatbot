"""A mini that finishes after she spoke. Offline.

forkbench used equal delays for every call, which hid the thing that matters:
what she waits for is not the persona call, it is the slowest thing awaited
beside it. So the delays here are the measured ones —

    dispatch 2.6s | persona 2.0s | a mini 1.5s | a slow search 4.3s (p95)

— scaled down 10x, and the checks are about WHO waits for WHAT.

    py -X utf8 tests\\latebench.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, minis, music, pipeline, tools  # noqa: E402

DISPATCH, PERSONA, MINI, SLOW = 0.26, 0.20, 0.15, 0.43
db = memory.connect(":memory:")
route = {"dispatch": [], "ask": None}
late = []


def fake_chat(**kw):
    system = str(kw["messages"][0].get("content", ""))
    if "decide which of" in system:
        time.sleep(DISPATCH)
        return {"content": json.dumps(route), "tool_calls": [], "raw": {}}
    if "You are the DJ" in system:
        time.sleep(MINI)
        return {"content": json.dumps({"action": "play", "terms": "bad apple"}),
                "tool_calls": [], "raw": {}}
    time.sleep(PERSONA)
    return {"content": "ok whatever", "tool_calls": [], "raw": {}}


llm.chat = fake_chat


@minis.mini("looks things up on the web", ("answer",))
def search(db, task):
    time.sleep(SLOW)  # the p95 tool, the one that must never block her
    return {"answer": f"the answer to {task}"}


HIST = [{"role": "user", "content": "Krich: hi"}]


async def on_late(line):
    late.append(line)


async def turn_of(text, author="Krich", handler=True):
    """Returns (reply, seconds, PENDING_MUSIC) — flags read inside the task, the
    way bot.player.flush does."""
    t0 = time.perf_counter()
    reply = await pipeline.respond(db, HIST, author, text,
                               on_late=on_late if handler else None)
    return reply, time.perf_counter() - t0, tools.PENDING_MUSIC


def scenario(fn):
    """One event loop per scenario, held open past the reply.

    asyncio.run() cancels whatever is still pending when its coroutine returns,
    which would kill every late task the moment she finished speaking. bot.py runs
    on one long-lived loop, so this is the shape production actually has.
    """
    def go():
        async def wrapper():
            try:
                await fn()
            finally:
                pipeline._cancel_pending("Krich")  # no task outlives its scenario
        asyncio.run(wrapper())
    return go


async def settle(extra=0.2):
    await asyncio.sleep(SLOW + PERSONA + extra)

@scenario
async def music_does_not_wait_for_the_router():
    """The classifier already knows. Making the song wait for a model to agree is
    the round trip this whole design exists to delete."""
    route.update(dispatch=[], ask=None)
    music.NOW["title"] = None
    late.clear()
    reply, took, pending = await turn_of("เปิดเพลงอะไรก็ได้")
    assert reply == "ok whatever"
    assert pending == {"keywords": "bad apple", "request": "เปิดเพลงอะไรก็ได้"}, pending
    # DJ starts at t=0, BEFORE dispatch, because the classifier already said this
    # is a music ask for free. So the song overlaps the routing call instead of
    # queueing behind it: all three serially would be DISPATCH + MINI + PERSONA.
    ceiling = DISPATCH + MINI + PERSONA
    assert took < ceiling, f"the song waited for the router: {took:.2f}s"
    print(f"music ok    — song queued in {took:.2f}s, not {ceiling:.2f}s; DJ "
          f"overlapped the router")


@scenario
async def search_never_blocks_her():
    """A p95 search is 4.3s. She has to be talking long before that."""
    route.update(dispatch=[{"mini": "search", "task": "who won last night"}], ask=None)
    late.clear()
    reply, took, _ = await turn_of("who won the match last night")
    assert reply == "ok whatever"
    assert took < DISPATCH + SLOW, f"she waited for the search: {took:.2f}s"
    print(f"search ok   — she replied in {took:.2f}s, the {SLOW:.2f}s search kept running")

    await settle()
    assert late, "the late result never arrived"
    assert late[0] == "ok whatever", late
    print(f"late ok     — it came back as a follow-up message: {late[0]!r}")
    # Rule 1: the raw facts must not reach the channel — she says it.
    assert not any("the answer to" in line for line in late), \
        f"a mini's facts reached the channel verbatim: {late}"
    print("voice ok    — the facts went through her, not around her")


@scenario
async def timeout_is_not_silence():
    """Past the deadline she says nothing rather than something invented."""
    was, pipeline.LATE_TIMEOUT = pipeline.LATE_TIMEOUT, 0.05
    route.update(dispatch=[{"mini": "search", "task": "slow one"}], ask=None)
    late.clear()
    try:
        await turn_of("what's the score")
        await settle()
        assert not late, f"a timed-out search still spoke: {late}"
    finally:
        pipeline.LATE_TIMEOUT = was
    rows = [t for _, k, t, _ in memory.read_log(db, 20) if k == "mini"]
    assert any("timed out" in r for r in rows), f"timeout was not logged: {rows[:3]}"
    print("timeout ok  — nothing said, and the log says why")


@scenario
async def speech_cancels_actions_do_not():
    """They spoke again. A stale search dies; a queued song still plays."""
    route.update(dispatch=[{"mini": "search", "task": "first question"}], ask=None)
    late.clear()
    await turn_of("who won last night")            # leaves a search running
    route.update(dispatch=[], ask=None)
    music.NOW["title"] = None
    _, _, pending = await turn_of("เปิดเพลงอะไรก็ได้")  # moved on, and asked for a song
    await settle()
    assert not late, f"a stale search spoke after they changed the subject: {late}"
    assert pending == {"keywords": "bad apple", "request": "เปิดเพลงอะไรก็ได้"}, "cancelling speech killed the song too"
    rows = [t for _, k, t, _ in memory.read_log(db, 20) if k == "mini"]
    assert any("cancelled" in r for r in rows), rows[:3]
    print("cancel ok   — stale search dropped, the song they just asked for played")


@scenario
async def no_handler_is_not_a_crash():
    """chat.py and the dashboard cannot receive a second message."""
    route.update(dispatch=[{"mini": "search", "task": "x"}], ask=None)
    late.clear()
    reply, _, _ = await turn_of("who won last night", handler=False)
    assert reply == "ok whatever"
    await settle()
    assert not late
    print("nohandler ok— logged and dropped where nothing can receive it")


music_does_not_wait_for_the_router()
search_never_blocks_her()
timeout_is_not_silence()
speech_cancels_actions_do_not()
no_handler_is_not_a_crash()
print("\nlate ok — actions land before she speaks, speech lands after, neither is silent")
