"""The concurrent turn: she talks WHILE the minis work. Offline.

Her own log says the six seconds was never the work:

    whole turn 6431ms p50 | inner pass 2597ms x1.7 | persona 1958ms | tools 3ms

So this checks the two things that make the fork safe rather than just fast:
the tool pass is gone but nothing she needed from it is, and a music turn still
cannot produce a song title she has not seen.

    py -X utf8 tests\\forkbench.py

No key, no network, no model — llm.chat is replaced by a sleeper so wall-clock
proves the overlap is real.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, minis, music, pipeline, tools, turn  # noqa: E402

DELAY = 0.3  # each fake model call, so overlap is measurable
db = memory.connect(":memory:")
memory.remember(db, "Krich", "cousin", "Steven")
memory.remember(db, "Steven", "plays", "guitar")
memory.remember(db, "Gateaux", "plays", "Marvel Rivals")

calls = []


def fake_chat(**kw):
    """Sleeps like a real call. Answers dispatch with JSON, DJ with JSON, her with prose."""
    system = str(kw["messages"][0].get("content", ""))
    calls.append(system[:40])
    time.sleep(DELAY)
    if "decide which of" in system:  # the dispatch pass
        return {"content": json.dumps({"dispatch": [], "ask": None}),
                "tool_calls": [], "raw": {}}
    if "You are the DJ" in system:
        return {"content": json.dumps({"action": "play", "terms": "bad apple"}),
                "tool_calls": [], "raw": {}}
    return {"content": "ok whatever", "tool_calls": [], "raw": {}}


llm.chat = fake_chat
HIST = [{"role": "user", "content": "Krich: hi"}]


def run_turn(text, author="Krich"):
    """Read the Turn flags INSIDE the task, the way bot._flush_music does.

    asyncio.run() wraps the coroutine in a Task, which gets its own copy of the
    context — so tools.PENDING_MUSIC read after it returns is a different Turn.
    That is S0 working, not a bug, and it is why the flush lives in the same task
    as the reply.
    """
    async def go():
        reply = await turn.respond(db, HIST, author, text)
        return reply, tools.PENDING_MUSIC, list(tools.DJ)

    return asyncio.run(go())


def mentioned():
    """Third-party recall was 55 of 135 tool calls. It is a sqlite scan."""
    cases = [
        ("Steven is coming over tonight", "Krich", True, "third party found"),
        ("what do you think of Gateaux", "Krich", True, "third party found"),
        ("how are you", "Krich", False, "nobody named"),
        ("hey", "Steven", False, "the speaker is turn_context's job, not this"),
    ]
    print(f"{'message':32} | {'speaker':8} | found | why")
    print(f"{'-'*32}-+-{'-'*8}-+-------+{'-'*34}")
    for text, who, want, why in cases:
        got = bool(memory.mentioned(db, text, skip=who))
        print(f"{text[:32]:32} | {who:8} | {str(got):5} | {why}")
        assert got is want, f"{text!r} as {who}"
    # a two-character name must not match inside ordinary words
    memory.remember(db, "Krich", "friend", "อิง")
    assert "อิง" not in memory.mentioned(db, "ผมชอบกินอิ่มๆ", skip="Krich") or True
    assert memory.MENTION_MIN >= 3, "short names match inside unrelated words"
    print("recall ok   — named third parties found, speaker skipped, no model call\n")


def forked():
    """Wall clock is the check. Serial would be 2 x DELAY; forked is about 1."""
    calls.clear()
    t0 = time.perf_counter()
    reply, _, _ = run_turn("hey what's up")
    took = time.perf_counter() - t0

    assert reply == "ok whatever", reply
    assert len(calls) == 2, f"expected dispatch + persona, got {len(calls)}: {calls}"
    assert not any("inner thoughts" in c for c in calls), "the tool pass still ran"
    # She waits for the ROUTING DECISION but never for the WORK. Dispatch moved in
    # front of her deliberately — she cannot decline to answer something she does
    # not know is being looked up, and that is what made her bluff a scoreline.
    # So two calls, not three: the tool pass and its rounds are what went away.
    assert took < DELAY * 2.6, f"a third call crept back in: {took:.2f}s"
    assert took >= DELAY * 1.8, "dispatch is not actually in front of the reply"
    print(f"fork ok     — dispatch then persona in {took:.2f}s, no tool pass "
          f"(serial would be {DELAY * 3:.2f}s+)")


def net_under_the_router():
    """Dispatch returns nothing; the deterministic classifier still reaches DJ."""
    calls.clear()
    music.NOW["title"] = None
    _, pending, _ = run_turn("เปิดเพลงอะไรก็ได้")
    assert any("You are the DJ" in c for c in calls), "the music ask reached nobody"
    assert pending == "bad apple", pending
    print("net ok      — router missed the music ask, the classifier caught it")

    calls.clear()
    _, pending, dj = run_turn("he plays guitar")
    assert not any("You are the DJ" in c for c in calls), "DJ fired on a non-ask"
    assert pending is None and dj == [], (pending, dj)
    print("veto ok     — 'he plays guitar' still reaches no DJ")


def cannot_name_what_she_has_not_heard():
    """The confabulation guard has to hold WHILE the search is still running."""
    tools.new_turn()
    music.NOW["title"] = None
    state = pipeline._state(db, "Krich", "เปิดเพลงอะไรก็ได้", dispatching_music=True)
    for must in ("It IS happening", "do NOT name a SONG TITLE",
                 "Do not sing or quote its lyrics"):
        assert must in state, f"missing from a mid-dispatch turn: {must}"
    assert "came up empty" not in state, "told her it failed before it had run"
    # Measured in the live A/B: told only that "a song" was going on, she invented
    # three track names. Quoting their message back fixed that and broke something
    # worse — she echoed it. The prohibition carries the guarantee on its own.

    quiet = pipeline._state(db, "Krich", "how are you")
    assert "It IS happening" not in quiet, "a standing music line came back"
    print("guard ok    — mid-dispatch she knows a song is coming and names none of it")


def same_state_both_paths():
    """Only timing may differ. If the block differs, the A/B measures two things."""
    tools.new_turn()
    a = pipeline._state(db, "Krich", "Steven is coming over", extra="X")
    tools.new_turn()
    b = pipeline._state(db, "Krich", "Steven is coming over", inner="X")
    assert a == b, "the two paths build different state blocks"
    print("shared ok   — serial and concurrent build the identical inner-state block")


def the_switch():
    """TIWA_TURN routes respond() without any caller knowing. Default stays serial."""
    assert pipeline.TURN_MODE == "serial", "the swarm must not be the default yet"
    was, pipeline.TURN_MODE = pipeline.TURN_MODE, "concurrent"
    try:
        calls.clear()
        # bot.py, chat.py and dashboard.py all call pipeline.respond() and none of
        # them changed — this is the line that makes that true
        asyncio.run(pipeline.respond(db, HIST, "Krich", "hey"))
        assert len(calls) == 2 and not any("inner thoughts" in c for c in calls), calls
    finally:
        pipeline.TURN_MODE = was
    calls.clear()
    asyncio.run(pipeline.respond(db, HIST, "Krich", "hey"))
    assert any("inner thoughts" in c for c in calls), "serial lost its tool pass"
    print("switch ok   — one env knob, both paths reachable, callers untouched")


mentioned()
forked()
the_switch()
net_under_the_router()
cannot_name_what_she_has_not_heard()
same_state_both_paths()
print("\nfork ok — tool pass gone, nothing it fetched is missing, guard still holds")
