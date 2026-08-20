"""DJ Tiwa: one call picks the action AND the search terms. Offline.

`djbench` owns the deck — what _flush_music does with a job list, and the keyword
safety net under the router. This owns the layer above it: given what they said
and what is on the deck, which of play/queue/skip/stop, and searched for what.

The cases are the ones _TERMS_SYSTEM was measured on, plus the play-vs-queue
choice that used to cost Main Tiwa four competing tools.

    py -X utf8 tests\\djminibench.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, minis, music, tools  # noqa: E402

db = memory.connect(":memory:")
sent = {}
reply = {}


def fake_chat(**kw):
    sent.update(kw)
    return {"content": json.dumps(reply), "tool_calls": [], "raw": {}}


llm.chat = fake_chat


def contract():
    """DJ is registered, declares only facts, and its description is what Main reads."""
    spec = minis.MINIS["dj"]
    assert spec["fields"] == frozenset({"action", "terms", "playing", "queued"})
    for word in ("เปิดเพลง", "ข้ามเพลง", "หยุดเพลง"):
        assert word in spec["description"], f"{word} missing — the model does not"\
                                            " generalise from English examples"
    minis._schema()  # dj must be a legal enum member
    assert "json" in minis._DJ_SYSTEM.lower(), "DeepSeek returns empty without it"
    print("contract ok — dj declares 4 facts, lists Thai triggers, says 'json'")


def deck_reaches_the_decision():
    """The deck is READ, never asked for. That is the tool call this mini removes."""
    reply.update(action="queue", terms="Mili")
    music.NOW["title"] = "Bad Apple (video)"
    music.QUEUE.append({"title": "Rick Roll (video)"})
    tools.new_turn()
    minis.run(db, "dj", "one more after this")
    prompt = sent["messages"][1]["content"]
    assert "Bad Apple (video)" in prompt and "1 song(s) queued" in prompt, prompt

    music.QUEUE.clear()
    music.NOW["title"] = None
    tools.new_turn()
    minis.run(db, "dj", "something chill")
    assert "Nothing is playing" in sent["messages"][1]["content"]
    print("deck ok     — what is on reaches the decision as a fact, not a tool call")


def actions():
    """Each action writes to the Turn, so bot._flush_music drains it unchanged."""
    cases = [
        # action, terms          -> PENDING_MUSIC,  DJ list
        ("play", "bad apple", "bad apple", []),
        ("queue", "Mili", None, [("queue", "Mili")]),
        ("skip", "", None, [("skip", "")]),
        ("stop", "", "", []),
        ("none", "", None, []),
        # a play with no terms would search the empty string and put on a random
        # song — the same failure _force_music's NONE veto exists to prevent
        ("play", "", None, []),
    ]
    print(f"\n{'action':7} | {'terms':12} | {'PENDING_MUSIC':14} | DJ")
    print(f"{'-'*7}-+-{'-'*12}-+-{'-'*14}-+-{'-'*24}")
    for action, terms, want_music, want_dj in cases:
        reply.update(action=action, terms=terms)
        tools.new_turn()
        out = minis.run(db, "dj", "...")
        print(f"{action:7} | {terms or '—':12} | {str(tools.PENDING_MUSIC):14} | {tools.DJ}")
        assert tools.PENDING_MUSIC == want_music, f"{action}: {tools.PENDING_MUSIC!r}"
        assert tools.DJ == want_dj, f"{action}: {tools.DJ}"
        assert set(out) <= minis.MINIS["dj"]["fields"], out
    print("\nactions ok  — every action lands on the Turn, empty terms vetoes the play")


def facts_not_prohibitions():
    """play_music returns a paragraph of 'do NOT name the artist'. That is a
    prohibition, it belongs to pipeline._doing(), and rule 2 keeps it out of here."""
    reply.update(action="play", terms="ATLAS The Score")
    music.NOW["title"] = None
    tools.new_turn()
    out = minis.run(db, "dj", "ATLAS")
    assert out == {"action": "play", "terms": "ATLAS The Score",
                   "playing": None, "queued": 0}, out
    blob = " ".join(str(v) for v in out.values())
    for leak in ("do NOT", "You have NOT seen", "say you are putting it on"):
        assert leak not in blob, f"tool prose leaked into the mini's facts: {leak}"
    print("facts ok    — 4 scalars out, none of play_music's instruction paragraph")


def degrades():
    """A model that returns junk must leave the deck alone, not raise."""
    for bad in ({}, {"action": "boogie", "terms": "x"}, {"terms": "no action"}):
        reply.clear()
        reply.update(bad)
        tools.new_turn()
        out = minis.run(db, "dj", "play something")
        assert tools.PENDING_MUSIC is None and tools.DJ == [], f"{bad} touched the deck"
        assert set(out) <= minis.MINIS["dj"]["fields"], out
    print("degrade ok  — a junk decision leaves the deck untouched and never raises")


contract()
deck_reaches_the_decision()
actions()
facts_not_prohibitions()
degrades()
