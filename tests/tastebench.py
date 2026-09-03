"""Repetition as evidence: the taste pass. Offline, with a `--live` half.

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
    memory.log(db, "mini", "dj('whatever') -> "
               + repr({"action": action, "terms": terms,
                       "playing": None, "queued": 0}))


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
    db = seeded([
        ("Tycoon", "Mili"), ("Tycoon", "Mili"), ("Krich", "Bad Apple"),
        ("Tycoon", "hero Mili"), ("Krich", None),          # a turn with no music
    ])
    got = memory.unsettled_asks(db)
    assert got == {"Tycoon": ["Mili", "Mili", "hero Mili"], "Krich": ["Bad Apple"]}, got

    # a skip or a stop is an opinion about what is ON, never a request for
    # something — counting them would make "skip" itself look like a taste
    db = seeded([("Tycoon", "x", "skip"), ("Tycoon", "y", "stop"),
                 ("Tycoon", "", "none"), ("Tycoon", "Mili", "queue")])
    assert memory.unsettled_asks(db) == {"Tycoon": ["Mili"]}, memory.unsettled_asks(db)

    # a raw link is never a taste, same rule the extractor has for entities
    db = seeded([("Tycoon", "https://youtu.be/Qdo3-hoAdzE"), ("Tycoon", "Mili")])
    assert memory.unsettled_asks(db) == {"Tycoon": ["Mili"]}

    # ...and nothing she asked for herself counts as somebody's taste
    db = seeded([(TIWA, "Mili"), ("Krich", "Bad Apple")])
    assert TIWA not in memory.unsettled_asks(db)
    print("mine ok     — terms paired to the right author; skips, links and her "
          "own asks all ignored")


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
    facts = list(db.execute("SELECT s.name, rel, d.name FROM relations r "
                            "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst"))
    assert not facts, f"repetition wrote a belief about her: {facts}"
    print("coercion ok — her own asks reach no model and write no fact about her")


def a_pattern_must_say_what_recurs():
    """The note IS the evidence here. A conclusion that cannot name what repeated
    did not find a pattern, it guessed one."""
    db = seeded([("Tycoon", "Mili")] * 4)
    for bad in ({"object": "", "note": "they like stuff"},
                {"object": "Mili", "note": ""},
                {"object": "Tycoon", "note": "likes himself"}):
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
    got = list(db.execute("SELECT s.name, rel, d.name, r.note FROM relations r "
                          "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst"))
    assert got == [("Tycoon", "likes", "Mili",
                    "asked for them four times this week")], got
    print("note ok     — empty object, empty note and self-reference all refused; "
          "a named pattern lands with its evidence")


def a_failure_is_not_silent():
    """A dead provider must cost the pass, never the heartbeat around it."""
    def boom(**kw):
        raise RuntimeError("502 upstream")

    was, llm.chat = llm.chat, boom
    try:
        db = seeded([("Tycoon", "Mili")] * 4)
        asyncio.run(pipeline._tastes(db))   # must not raise
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
        ("a real pattern",
         ["Mili", "hero Mili", "Mili limbus", "Bad Apple"], True),
        ("spelling varies",
         ["Charlie Kirk", "Charie Kirk", "We Are Charlie Kirk"], True),
        ("one franchise, three tracks",
         ["Spiderman theme", "Blade theme", "Iron Man theme"], True),
        ("a scatter — the normal answer",
         ["Bad Apple", "Rick Roll", "ลูกทุ่ง", "Fortnite theme"], False),
        ("twice is not a pattern",
         ["Mili", "Mili", "Bad Apple", "Rick Roll"], False),
    ]
    print(f"\n{'asks':52} | wrote")
    print(f"{'-'*52}-+------")
    ok = 0
    for label, terms, want in CASES:
        db = seeded([("Tycoon", t) for t in terms])
        asyncio.run(pipeline._tastes(db))
        got = list(db.execute("SELECT d.name, r.note FROM relations r "
                              "JOIN entities d ON d.id=r.dst"))
        hit = bool(got) is want
        ok += hit
        shown = f"{got[0][0]} — {got[0][1]}"[:40] if got else "(nothing)"
        print(f"{(label + ': ' + ', '.join(terms))[:52]:52} | {shown}"
              f"{'' if hit else '   <-- WRONG'}")
    print(f"\npattern: {ok}/{len(CASES)}")
    assert ok >= len(CASES) - 1, f"it cannot tell a pattern from a playlist: {ok}"
    print("live ok     — a repeat becomes a taste, a scatter stays a playlist")


llm.chat = fake_chat
mining()
the_watermark()
below_the_bar_costs_nothing()
she_cannot_be_repeated_into()
a_pattern_must_say_what_recurs()
a_failure_is_not_silent()
if "--live" in sys.argv:
    live()
else:
    print("\nlive      — SKIPPED. `--live` asks a real model to tell a pattern "
          "from a playlist.")
print("\ntaste ok — repetition is counted, and only a named pattern earns a row")
