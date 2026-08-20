"""The dispatch layer, offline. No key, no network, no model.

`py -X utf8 -m tiwa.minis` covers the registry contract. This covers the call
around it — the two ways a schema-constrained pass fails silently in this repo:

  - OpenRouter rejects a strict json_schema whose properties are not ALL
    required, or that allows extras (gcal._EVENT_FORMAT carries the same note).
  - DeepSeek returns EMPTY CONTENT unless the prompt literally contains the word
    "json". llm.chat asserts it, but only on a live call — and a live call needs
    a key. Rewriting _SYSTEM without the word would ship a dispatch pass that
    quietly dispatches nothing, forever.

    py -X utf8 tests\\minibench.py
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, minis  # noqa: E402

db = memory.connect(":memory:")
sent = {}


def fake_chat(**kw):
    """Stand in for the model. Records what dispatch() built, returns canned JSON."""
    sent.update(kw)
    return {"content": sent.pop("_reply", "{}"), "tool_calls": [], "raw": {}}


llm.chat = fake_chat
minis.MINIS.clear()  # fixtures only — the real dj has its own bench and its own model


@minis.mini("plays and queues music. give it the song, mood or 'their favourite'.",
            ("playing", "artist"))
def fakedj(db, task):
    return {"playing": task, "artist": "unknown"}


@minis.mini("reads and changes Krich's calendar.", ("events",))
def fakecal(db, task):
    return {"events": []}


def strict(node, where="root"):
    """Every object in the schema must be strict-mode legal, at every depth."""
    if not isinstance(node, dict):
        return
    if node.get("type") == "object":
        props = sorted(node.get("properties", {}))
        assert sorted(node.get("required", [])) == props, f"{where}: required != properties"
        assert node.get("additionalProperties") is False, f"{where}: extras allowed"
    for k, v in node.items():
        if isinstance(v, dict):
            strict(v, f"{where}.{k}")


def schema():
    s = minis._schema()
    strict(s)
    assert s["properties"]["dispatch"]["items"]["properties"]["mini"]["enum"] == \
        ["fakecal", "fakedj"], "enum must be the live registry, sorted"
    print("schema ok   — strict-mode legal at every depth, mini names are an enum")


def prompt_says_json():
    sent["_reply"] = '{"dispatch":[],"ask":null}'
    asyncio.run(minis.dispatch(db, "Krich", "hello"))
    system = sent["messages"][0]["content"]
    assert "json" in system.lower(), "DeepSeek returns empty content without the word"
    assert sent["options"]["temperature"] == 0, "dispatch must not be creative"
    for name in ("fakedj", "fakecal"):
        assert f"- {name}:" in system, f"{name} missing from the prompt"
    assert minis.MINIS["fakedj"]["description"] in system
    print("prompt ok   — says 'json', temp 0, and lists every registered mini")


def routing():
    cases = [
        ("nothing", '{"dispatch":[],"ask":null}', [], ""),
        ("one job", '{"dispatch":[{"mini":"fakedj","task":"their favourite song"}],"ask":null}',
         [("fakedj", "their favourite song")], ""),
        ("two jobs", '{"dispatch":[{"mini":"fakedj","task":"lofi"},'
                     '{"mini":"fakecal","task":"cancel friday"}],"ask":null}',
         [("fakedj", "lofi"), ("fakecal", "cancel friday")], ""),
        ("ambiguous", '{"dispatch":[],"ask":"which friday did they mean"}',
         [], "which friday did they mean"),
        ("invented mini", '{"dispatch":[{"mini":"lights","task":"off"}],"ask":null}',
         [], ""),
        ("junk", "sorry, I can't help with that", [], ""),
        # valid JSON, wrong shape. OpenRouter's strict schema cannot produce
        # these; the cost ceiling falling back to local ollama can, and .get()
        # on a list raised AttributeError straight out of the turn.
        ("json array", "[1, 2]", [], ""),
        ("json null", "null", [], ""),
        ("json string", '"just a sentence"', [], ""),
        ("empty content", "", [], ""),
    ]
    print(f"\n{'case':14} | {'dispatch':40} | ask")
    print(f"{'-'*14}-+-{'-'*40}-+-{'-'*30}")
    for name, reply, want_jobs, want_ask in cases:
        sent["_reply"] = reply
        got = asyncio.run(minis.dispatch(db, "Krich", "..."))
        print(f"{name:14} | {str(got['dispatch']):40} | {got['ask']!r}")
        assert got["dispatch"] == want_jobs, f"{name}: {got['dispatch']}"
        assert got["ask"] == want_ask, f"{name}: {got['ask']!r}"
    print("\nrouting ok  — empty is normal, two jobs survive, invented names vanish")


def facts_only():
    """The end-to-end path a dispatched job takes: run() drops what wasn't declared."""
    @minis.mini("leaks", ("title",))
    def leaky(db, task):
        return {"title": "ATLAS", "say": "tell them you're putting it on 😎"}

    assert minis.run(db, "leaky", "x") == {"title": "ATLAS"}, "voice bleed got through"
    print("facts ok    — an undeclared 'say' field never reaches her")


schema()
prompt_says_json()
routing()
facts_only()
