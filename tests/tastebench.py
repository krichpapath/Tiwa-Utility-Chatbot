"""The two memory passes no single turn can do. Offline, with a `--live` half.

Both exist because ADR-030 judges a fact from ONE exchange, and two real things
cannot be seen that way: a taste shown by repetition, and a person who never
says anything about themselves at all.

[ADR-030] stopped a music request becoming a preference, and it was right — one
ask is evidence of nothing. But it left a real gap: somebody who asks for the
same artist every day IS telling her something, and no per-turn judgment can see
it, because each turn on its own looks exactly like the junk that was removed.

So it is counted where the evidence already lives — the activity log. This checks
the counting (deterministic, offline) and, with `--live`, whether the model can
tell a pattern from a playlist.

    py -X utf8 tests\\tastebench.py
    py -X utf8 tests\\tastebench.py --live

The offline half is where the guards are. Read those first.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, pipeline  # noqa: E402

TIWA = memory.TIWA
answer = {}
calls = []


def fake_chat(**kw):
    calls.append(str(kw["messages"][-1].get("content", "")))
    return {"content": json.dumps(answer), "tool_calls": [], "raw": {}}


def dj_row(db, terms, action="play"):
    memory.log(
        db,
        "mini",
        "dj('whatever') -> "
        + repr({"action": action, "terms": terms, "playing": None, "queued": 0}),
    )


def turn_row(db, who):
    memory.log(db, "turn", f"{who}: something -> ok")


def seeded(pairs):
    """pairs = [(author, terms) | (author, terms, action)]"""
    db = memory.connect(":memory:")
    for p in pairs:
        who, terms, action = (*p, "play")[:3]
        if terms is not None:
            dj_row(db, terms, action)
        turn_row(db, who)
    return db


# ---------------------------------------------------------------- the mining


def mining():
    """The `mini` row carries the terms, the `turn` row after it carries the
    author. Pairing them is the whole mechanism, and it is the part that can be
    wrong silently."""
    db = seeded(
        [
            ("Tycoon", "Mili"),
            ("Tycoon", "Mili"),
            ("Krich", "Bad Apple"),
            ("Tycoon", "hero Mili"),
            ("Krich", None),  # a turn with no music
        ]
    )
    got = memory.unsettled_asks(db)
    assert got == {"Tycoon": ["Mili", "Mili", "hero Mili"], "Krich": ["Bad Apple"]}, got

    # a skip or a stop is an opinion about what is ON, never a request for
    # something — counting them would make "skip" itself look like a taste
    db = seeded(
        [
            ("Tycoon", "x", "skip"),
            ("Tycoon", "y", "stop"),
            ("Tycoon", "", "none"),
            ("Tycoon", "Mili", "queue"),
        ]
    )
    assert memory.unsettled_asks(db) == {"Tycoon": ["Mili"]}, memory.unsettled_asks(db)

    # a raw link is never a taste, same rule the extractor has for entities
    db = seeded([("Tycoon", "https://youtu.be/Qdo3-hoAdzE"), ("Tycoon", "Mili")])
    assert memory.unsettled_asks(db) == {"Tycoon": ["Mili"]}

    # ...and nothing she asked for herself counts as somebody's taste
    db = seeded([(TIWA, "Mili"), ("Krich", "Bad Apple")])
    assert TIWA not in memory.unsettled_asks(db)
    print(
        "mine ok     — terms paired to the right author; skips, links and her own asks all ignored"
    )


def the_watermark():
    """Without one the same asks are re-counted every heartbeat, forever, and she
    rewrites the same fact 28 times a day."""
    db = seeded([("Tycoon", "Mili")] * 3)
    assert len(memory.unsettled_asks(db)["Tycoon"]) == 3
    memory.log(db, "taste", "Tycoon: 3 asks -> likes Mili — every day")
    assert memory.unsettled_asks(db) == {}, "settled asks were counted again"
    dj_row(db, "Kasane Teto")
    turn_row(db, "Tycoon")
    assert memory.unsettled_asks(db) == {"Tycoon": ["Kasane Teto"]}
    print("mark ok     — a taste row settles what came before it, nothing after")


# ---------------------------------------------------------------- the guards


def below_the_bar_costs_nothing():
    """Two asks is a coincidence. It must not even reach a model."""
    calls.clear()
    db = seeded([("Tycoon", "Mili"), ("Tycoon", "Mili")])
    asyncio.run(pipeline._tastes(db))
    assert not calls, f"a model was called for 2 asks: {calls}"
    assert not list(db.execute("SELECT 1 FROM relations")), "a fact was written"
    assert memory.TASTE_MIN >= 3, "twice is a coincidence"
    print("bar ok      — under TASTE_MIN there is no call and no row")


def she_cannot_be_repeated_into():
    """The coercion guarantee, at the one door that could open it. A user must
    not be able to give her a taste by asking for the same thing enough times."""
    calls.clear()
    answer.clear()
    answer.update(object="BLACKPINK", note="asked for it constantly")
    db = seeded([(TIWA, "BLACKPINK")] * 5)
    asyncio.run(pipeline._tastes(db))
    facts = list(
        db.execute(
            "SELECT s.name, rel, d.name FROM relations r "
            "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst"
        )
    )
    assert not facts, f"repetition wrote a belief about her: {facts}"
    print("coercion ok — her own asks reach no model and write no fact about her")


def a_pattern_must_say_what_recurs():
    """The note IS the evidence here. A conclusion that cannot name what repeated
    did not find a pattern, it guessed one."""
    db = seeded([("Tycoon", "Mili")] * 4)
    for bad in (
        {"object": "", "note": "they like stuff"},
        {"object": "Mili", "note": ""},
        {"object": "Tycoon", "note": "likes himself"},
    ):
        answer.clear()
        answer.update(bad)
        db2 = seeded([("Tycoon", "Mili")] * 4)
        asyncio.run(pipeline._tastes(db2))
        assert not list(db2.execute("SELECT 1 FROM relations")), f"{bad} was written"
        rows = [t for (t,) in db2.execute("SELECT text FROM log WHERE kind='taste'")]
        assert rows and "no pattern" in rows[-1], rows

    answer.clear()
    answer.update(object="Mili", note="asked for them four times this week")
    asyncio.run(pipeline._tastes(db))
    got = list(
        db.execute(
            "SELECT s.name, rel, d.name, r.note FROM relations r "
            "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst"
        )
    )
    assert got == [("Tycoon", "often requests", "Mili", "asked for them four times this week")], got
    print(
        "note ok     — empty object, empty note and self-reference all refused; "
        "a named pattern lands with its evidence"
    )


def a_failure_is_not_silent():
    """A dead provider must cost the pass, never the heartbeat around it."""

    def boom(**kw):
        raise RuntimeError("502 upstream")

    was, llm.chat = llm.chat, boom
    try:
        db = seeded([("Tycoon", "Mili")] * 4)
        asyncio.run(pipeline._tastes(db))  # must not raise
        rows = [t for (t,) in db.execute("SELECT text FROM log WHERE kind='taste'")]
        assert rows and "failed" in rows[-1], rows
    finally:
        llm.chat = was
    print("outage ok   — the pass logs its own failure and the heartbeat survives")


def live():
    """Can it tell a pattern from a playlist? The only part a model decides."""
    importlib = __import__("importlib")
    importlib.reload(llm)
    importlib.reload(pipeline)

    CASES = [
        ("a real pattern", ["Mili", "hero Mili", "Mili limbus", "Bad Apple"], True),
        ("spelling varies", ["Charlie Kirk", "Charie Kirk", "We Are Charlie Kirk"], True),
        ("one franchise, three tracks", ["Spiderman theme", "Blade theme", "Iron Man theme"], True),
        (
            "a scatter — the normal answer",
            ["Bad Apple", "Rick Roll", "ลูกทุ่ง", "Fortnite theme"],
            False,
        ),
        ("twice is not a pattern", ["Mili", "Mili", "Bad Apple", "Rick Roll"], False),
    ]
    print(f"\n{'asks':52} | wrote")
    print(f"{'-' * 52}-+------")
    ok = 0
    for label, terms, want in CASES:
        db = seeded([("Tycoon", t) for t in terms])
        asyncio.run(pipeline._tastes(db))
        got = list(
            db.execute("SELECT d.name, r.note FROM relations r JOIN entities d ON d.id=r.dst")
        )
        hit = bool(got) is want
        ok += hit
        shown = f"{got[0][0]} — {got[0][1]}"[:40] if got else "(nothing)"
        print(
            f"{(label + ': ' + ', '.join(terms))[:52]:52} | {shown}{'' if hit else '   <-- WRONG'}"
        )
    print(f"\npattern: {ok}/{len(CASES)}")
    assert ok >= len(CASES) - 1, f"it cannot tell a pattern from a playlist: {ok}"
    print("live ok     — a repeat becomes a taste, a scatter stays a playlist")


def she_notices_when_she_knows_nothing():
    """The other half, and the one nothing else can reach.

    Counting repetition needs something to count. Krich has talked to her for a
    month and the graph holds nothing about him, because his turns are music
    requests and he never says anything about himself. No pattern, nothing
    stated — the only way in is for her to ASK.

    And she could not, because nothing told her there was a gap:
    `turn_context()` is silent when it knows nothing, so a stranger and an old
    friend hand her the same empty string. Exactly the empty-deck bug in
    `_doing()`, one layer up.
    """
    db = memory.connect(":memory:")
    assert memory.should_ask(db, "Krich"), "a total stranger did not earn a nudge"
    assert not memory.should_ask(db, TIWA), "she was told to interview herself"
    assert not memory.should_ask(db, ""), "a blank name earned a nudge"

    # ...and someone she already knows is left alone
    for rel, obj in (("plays", "Warframe"), ("real name", "Gateaux"), ("friend of", "Nara")):
        memory.remember(db, "Tycoon", rel, obj, "")
    assert not memory.should_ask(db, "Tycoon"), "she was nudged about someone she knows"
    assert memory.KNOW_LITTLE >= 3, "two facts is still a stranger"
    print("gap ok      — a stranger earns a nudge, a friend does not, and never herself")


def she_asks_rather_than_interviews():
    """A question every turn is an interview, which the persona rules forbid by
    name. The cooldown counts THEIR turns, so a busy channel cannot burn down a
    quiet person's timer."""
    db = memory.connect(":memory:")
    assert memory.should_ask(db, "Krich")
    memory.log(db, "ask", "Krich: knows too little — she asked")
    assert not memory.should_ask(db, "Krich"), "she asked twice in a row"

    for _ in range(memory.ASK_EVERY - 1):
        memory.log(db, "turn", "Krich: something -> ok")
    assert not memory.should_ask(db, "Krich"), "the cooldown ended early"
    memory.log(db, "turn", "Krich: something -> ok")
    assert memory.should_ask(db, "Krich"), "the cooldown never ended"

    # somebody else talking does not count as Krich getting older
    db2 = memory.connect(":memory:")
    memory.log(db2, "ask", "Krich: knows too little — she asked")
    for _ in range(memory.ASK_EVERY * 2):
        memory.log(db2, "turn", "Tycoon: something -> ok")
    assert not memory.should_ask(db2, "Krich"), "another person's turns burned Krich's cooldown"
    print("rate ok     — one question every ASK_EVERY of THEIR turns, nobody else's")


def a_declined_nudge_costs_nothing():
    """The cooldown counts questions she ASKED, not nudges she was given.

    Measured: she ignores the nudge on a bare "hey" and on "skip", and she is
    right both times — "what do you do for work?" mid-skip is not a friend
    talking. Burning her one chance in eight turns on a turn that could never
    have worked is the bug this avoids.
    """
    for reply, want in [
        ("ว่าไงมึง มึงทำงานอะไรอยู่", True),
        ("เปิดให้ละ", False),
        ("skip what?", True),
        ("Yo.", False),
        ("อือ เบื่อเหมือนกัน", False),
        ("มึงเล่นเกมอะไรบ้าง", True),
    ]:
        assert pipeline._is_question(reply) is want, f"{reply!r} -> {want}"

    src = (Path(__file__).parents[1] / "tiwa" / "pipeline.py").read_text(encoding="utf-8")
    assert "if ask_about and _is_question(reply):" in src, (
        "the cooldown is burned on nudges again, not on questions"
    )
    print("decline ok  — a nudge she ignores does not spend her turn")


def the_nudge_names_what_to_ask():
    """It said "ask something real about themselves" first, and measured 3/5 —
    all three about the MOMENT: "มึงเบื่ออะไรล่ะ", "what's up?", "skip what?".
    That is the reflex filler the rules already forbid, with a question mark on
    it. Naming the shape is what fixed it, so the shape is asserted."""
    db = memory.connect(":memory:")
    on = pipeline._state(db, "Krich", "hey", ask_about=True)
    off = pipeline._state(db, "Krich", "hey")
    assert "Krich" in on and "know NOTHING" in on, on[-300:]
    for shape in ("มึงทำงานอะไร", "เล่นเกมอะไรอยู่", "who do you actually play with"):
        assert shape in on, f"the nudge stopped naming a question shape: {shape}"
    assert "what's up" in on, "it no longer says which questions are filler"
    assert "know NOTHING" not in off, "the nudge fires on a quiet turn"
    print("nudge ok    — names the shape of the question, and only when gated on")


llm.chat = fake_chat
she_notices_when_she_knows_nothing()
she_asks_rather_than_interviews()
a_declined_nudge_costs_nothing()
the_nudge_names_what_to_ask()
mining()
the_watermark()
below_the_bar_costs_nothing()
she_cannot_be_repeated_into()
a_pattern_must_say_what_recurs()
a_failure_is_not_silent()
if "--live" in sys.argv:
    live()
else:
    print("\nlive      — SKIPPED. `--live` asks a real model to tell a pattern from a playlist.")
print("\ntaste ok — repetition is counted, and only a named pattern earns a row")
