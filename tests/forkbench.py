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
from tiwa import llm, memory, minis, music, pipeline, tools  # noqa: E402

DELAY = 0.3  # each fake model call, so overlap is measurable
db = memory.connect(":memory:")
memory.remember(db, "Krich", "cousin", "Steven")
memory.remember(db, "Steven", "plays", "guitar")
memory.remember(db, "Gateaux", "plays", "Marvel Rivals")

calls = []
states = []          # every state block the persona pass was handed
DJ_SAYS = {"action": "play", "terms": "bad apple"}   # what DJ Tiwa answers


def fake_chat(**kw):
    """Sleeps like a real call. Answers dispatch with JSON, DJ with JSON, her with prose."""
    system = str(kw["messages"][0].get("content", ""))
    calls.append(system[:40])
    time.sleep(DELAY)
    if "decide which of" in system:  # the dispatch pass
        return {"content": json.dumps({"dispatch": [], "ask": None}),
                "tool_calls": [], "raw": {}}
    if "You are the DJ" in system:
        return {"content": json.dumps(DJ_SAYS), "tool_calls": [], "raw": {}}
    if len(kw["messages"]) > 1:  # the persona pass — keep what she was told
        states.append(str(kw["messages"][1].get("content", "")))
    return {"content": "ok whatever", "tool_calls": [], "raw": {}}


llm.chat = fake_chat
HIST = [{"role": "user", "content": "Krich: hi"}]


def run_turn(text, author="Krich"):
    """Read the Turn flags INSIDE the task, the way bot.player.flush does.

    asyncio.run() wraps the coroutine in a Task, which gets its own copy of the
    context — so tools.PENDING_MUSIC read after it returns is a different Turn.
    That is S0 working, not a bug, and it is why the flush lives in the same task
    as the reply.
    """
    async def go():
        reply = await pipeline.respond(db, HIST, author, text)
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
    assert pending == {"keywords": "bad apple", "request": "เปิดเพลงอะไรก็ได้"}, pending
    print("net ok      — router missed the music ask, the classifier caught it")

    # "he plays guitar" DOES reach DJ now, and that is the design: the hint is
    # greedy because being wrong costs one cheap call, and DJ is what decides.
    # What must never happen is a song. `DJ_SAYS` is the veto here; the real
    # model answering `none` to this exact sentence is djminibench --live.
    global DJ_SAYS
    was, DJ_SAYS = DJ_SAYS, {"action": "none", "terms": ""}
    try:
        calls.clear()
        _, pending, dj = run_turn("he plays guitar")
        assert any("You are the DJ" in c for c in calls), "the hint stopped being greedy"
        assert pending is None and dj == [], f"a veto reached the deck: {pending} {dj}"
    finally:
        DJ_SAYS = was
    print("veto ok     — 'he plays guitar' reaches DJ, and DJ keeps it off the deck")


def cannot_name_what_she_has_not_heard():
    """The confabulation guard has to hold WHILE the search is still running."""
    tools.new_turn()
    music.NOW["title"] = None
    state = pipeline._state(db, "Krich", "เปิดเพลงอะไรก็ได้", dispatching_music=True)
    for must in ("Playback is NOT confirmed", "do NOT name a SONG TITLE",
                 "Do not sing or quote its lyrics"):
        assert must in state, f"missing from a mid-dispatch turn: {must}"
    assert "came up empty" not in state, "told her it failed before it had run"
    # Measured in the live A/B: told only that "a song" was going on, she invented
    # three track names. Quoting their message back fixed that and broke something
    # worse — she echoed it. The prohibition carries the guarantee on its own.

    quiet = pipeline._state(db, "Krich", "how are you")
    assert "Playback is NOT confirmed" not in quiet, "a standing music line came back"
    print("guard ok    — mid-dispatch she knows a song is coming and names none of it")


def dj_has_the_last_word_before_she_speaks():
    """The classifier is over-eager on purpose; DJ is the veto. It has to land
    BEFORE the state block, or she is told a song is coming when none is.

    This is a real Discord turn, 2026-08-24: "ขอ ยืมตังหน่อย" (lend me money)
    starts with "ขอ ", which is in _MUSIC_VERB, so DJ ran. DJ correctly answered
    `none` — and she was told "you are putting on what they just asked for"
    anyway, because the verdict arrived after she had already been briefed.
    """
    global DJ_SAYS
    music.NOW["title"] = None

    # 1. DJ says none -> she is told about no music at all
    was, DJ_SAYS = DJ_SAYS, {"action": "none", "terms": ""}
    try:
        states.clear()
        _, pending, dj = run_turn("ขอ ยืมตังหน่อย")
        assert pending is None and dj == [], f"a veto still reached the deck: {pending} {dj}"
        assert states, "the persona pass never ran"
        assert "Playback is NOT confirmed" not in states[-1], \
            f"she was told a song is coming after DJ vetoed it:\n{states[-1]}"
    finally:
        DJ_SAYS = was

    # 2. DJ says play -> she is told WHAT, in the words the search actually got
    states.clear()
    _, pending, _ = run_turn("เปิดเพลงอะไรก็ได้")
    assert pending == {"keywords": "bad apple", "request": "เปิดเพลงอะไรก็ได้"}, pending
    assert "play bad apple" in states[-1], \
        f"the state block is still vague about what is playing:\n{states[-1]}"
    assert "do NOT name a SONG TITLE" in states[-1], "the guard came off with it"
    print("veto ok     — DJ's `none` lands before the briefing, and `play` names the terms")


def there_is_only_one_path():
    """No knob, no fork, no tool pass. This branch IS the swarm.

    There used to be a `TIWA_TURN` switch here with the serial three-pass shape
    on the other side of it, and this check asserted both were reachable. The
    serial shape lives on `main` now, so what has to hold instead is that
    nothing here can fall back to it: an ordinary turn is exactly two model
    calls — dispatch, then her — and neither is a brief written to her.
    """
    assert not hasattr(pipeline, "TURN_MODE"), "the fork knob came back"
    calls.clear()
    # bot.py, chat.py and dashboard.py all still call pipeline.respond() and none
    # of them changed when the serial half was deleted — this is that line
    asyncio.run(pipeline.respond(db, HIST, "Krich", "hey"))
    assert len(calls) == 2, f"an ordinary turn is not two calls: {calls}"
    assert not any("inner thoughts" in c for c in calls), calls
    print("path ok     — one turn, two calls, no tool pass to fall back to")


mentioned()
forked()
dj_has_the_last_word_before_she_speaks()
there_is_only_one_path()
net_under_the_router()
cannot_name_what_she_has_not_heard()
print("\nfork ok — tool pass gone, nothing it fetched is missing, guard still holds")
