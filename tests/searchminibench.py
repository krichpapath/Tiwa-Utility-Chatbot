"""Search Tiwa: keyword planner, evidence reviewer, and error responses. Offline.

`searchbench` measures the real index against the real web. This measures the
layer above it — that the sentence never reaches the search engine, that the year
is right, and that a failed search comes back as nothing rather than as an error
message she would read out loud.

    py -X utf8 tests\\searchminibench.py
"""
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, minis, tools  # noqa: E402

db = memory.connect(":memory:")
asked = {}
keywords = {"out": ""}
result = {"out": "Some Match 3-1 [thairath.co.th]: they won"}


def fake_chat(**kw):
    if kw.get("fmt") == minis._SEARCH_REVIEW_FORMAT:
        failed = result["out"].startswith(("search failed", "every result"))
        return {"content": json.dumps({"enough": not failed, "answer": result["out"] if not failed else "",
                                       "sources": [1] if not failed else [], "query": ""})}
    asked.update(kw)
    return {"content": keywords["out"], "tool_calls": [], "raw": {}}


def fake_search(db, arg):
    asked["query"] = arg
    return result["out"]


llm.chat = fake_chat
tools.web_search = fake_search


def contract():
    spec = minis.MINIS["search"]
    assert spec["fields"] == frozenset({"query", "found"})
    assert "not for people she should already remember" in spec["description"].lower()
    print("contract ok — search declares 2 facts and tells Main what it is not for")


def keywords_not_sentences():
    """What reaches the index is the model's keywords, never the user's sentence.

    The rules themselves cannot be checked offline — they live in the prompt and
    only a real model obeys them. What IS checkable: that the prompt still carries
    them, and that whatever the model answers is parsed rather than pasted.
    """
    system = minis._SEARCH_SYSTEM
    for rule in ("มึงรู้ไหมว่า", "ผลบอลเมื่อคืน", "iPhone review", "Never the sentence"):
        assert rule in system, f"the measured rule {rule!r} did not move here"

    cases = [
        ("ผลบอลเมื่อคืน", "ผลบอลเมื่อคืน", "plain answer"),
        ("'iPhone review'", "iPhone review", "quotes stripped"),
        ("<think>hmm</think>\nJujutsu Kaisen new season\nthat should do it",
         "Jujutsu Kaisen new season", "first real line, <think> gone"),
        ("x" * 200, "x" * 80, "capped at 80 chars"),
    ]
    print(f"\n{'model answered':46} | query sent to the index")
    print(f"{'-'*46}-+-{'-'*30}")
    for said, want, why in cases:
        keywords["out"] = said
        out = minis.run(db, "search", "มึงรู้ไหมว่าใครชนะบอลเมื่อคืน")
        print(f"{said[:44]!r:46} | {out['query'][:28]!r} ({why})")
        assert out["query"] == want, out
        assert asked["query"] == want, "the query sent differs from the query returned"
        assert "มึงรู้ไหมว่า" not in asked["query"], "the sentence reached the index"
    print("\nkeywords ok — rules moved here whole; the sentence never reaches the index")


def the_year_is_now():
    """She was searching 'ราคา RTX 5090 2025' in July 2026 — a wrong year is a
    wrong page back, and a model dates itself from its training data."""
    keywords["out"] = "x"
    minis.run(db, "search", "how much is a 5090")
    prompt = asked["messages"][1]["content"]
    assert f"{datetime.datetime.now():%Y-%m-%d}" in prompt, prompt
    assert "year" in asked["messages"][0]["content"].lower()
    print("year ok     — today's date reaches the keyword call")


def empty_model_falls_back():
    """A blank keyword line must not search the empty string."""
    keywords["out"] = "\n  \n"
    out = minis.run(db, "search", "who won the match last night")
    assert out["query"] == "who won the match last night", out
    print("fallback ok — a blank keyword line falls back to the task, not to ''")


def a_failure_is_not_an_answer():
    """web_search never raises, so a flake arrives as text. Text is not an answer."""
    keywords["out"] = "ผลบอลเมื่อคืน"
    for failure in ("search failed: ConnectError",
                    "every result was one you already saw this turn"):
        result["out"] = failure
        out = minis.run(db, "search", "who won")
        assert "Could not establish" in out["found"], out
        assert failure not in out["found"], out
    result["out"] = "Some Match 3-1 [thairath.co.th]: they won"
    out = minis.run(db, "search", "who won")
    assert "Some Match" in out["found"], out
    print("failure ok  — unavailable evidence is reported without exposing raw errors")


def read_only():
    """The coercion guarantee: no mini may reach a write path."""
    src = (Path(__file__).parents[1] / "tiwa" / "minis.py").read_text(encoding="utf-8")
    for writer in ("memory.remember(", "memory.reflect(", "store_extraction",
                   "memory.wipe(", "delete_relation"):
        assert writer not in src, f"a mini can reach {writer} — offline reflection"\
                                  " is meant to be the only thing that changes a belief"
    print("readonly ok — no mini in the registry can reach a memory write")


contract()
keywords_not_sentences()
the_year_is_now()
empty_model_falls_back()
a_failure_is_not_an_answer()
read_only()
print("\nsearch ok — keywords not sentences, right year, a flake stays honest")
