"""Mini Tiwa registry. Every mini: fn(db, task: str) -> dict of facts.

Same shape as tools.py, and for the same reason: this IS the framework. A mini is
a function, a description, and the list of facts it is allowed to return.

The difference from a tool is what the caller gets back. A tool returns a string
that lands in her brief; a mini returns FACTS, and code — not the mini — decides
what she is told about them. See SWARM.md rule 2.

Main Tiwa sees the descriptions here and nothing else. That is the whole point:
DJ Tiwa can own nine tools inside itself and Main's list stays four long.

    py -X utf8 -m tiwa.minis
"""
import asyncio
import datetime
import json

from . import llm, memory
from .memory import MODEL, TIWA

MINIS = {}  # name -> {"description": str, "fields": frozenset, "fn": callable}


def mini(description: str, fields: tuple):
    """Register a mini. `fields` is every fact key it may return — nothing else
    survives validation.

    Declaring the fields is the guarantee, not a prose detector. You cannot
    reliably spot "tell them it's playing" at runtime, but you CAN make adding a
    field a visible edit someone reviews. A mini that starts smuggling voice has
    to say so in its own signature first.
    """

    def reg(fn):
        MINIS[fn.__name__] = {
            "description": description,
            "fields": frozenset(fields),
            "fn": fn,
        }
        return fn

    return reg


def _is_fact(v) -> bool:
    """A fact is a scalar or a flat list of them. No nested objects: structure is
    where prose hides, and Main Tiwa has no use for a tree."""
    if v is None or isinstance(v, (str, int, float, bool)):
        return True
    return isinstance(v, list) and all(
        x is None or isinstance(x, (str, int, float, bool)) for x in v
    )


def clean(name: str, out) -> dict:
    """Keep only declared, fact-shaped keys. Anything else is dropped, not raised.

    A mini that returns junk must degrade to "no result" — which she already
    knows how to handle by asking — never to a crash that eats the turn. Same
    stance as tools: they must never raise.
    """
    spec = MINIS.get(name)
    if spec is None or not isinstance(out, dict):
        return {}
    return {k: v for k, v in out.items() if k in spec["fields"] and _is_fact(v)}


def run(db, name: str, task: str) -> dict:
    """Call one mini. Sync on purpose — callers wrap it in asyncio.to_thread, same
    as llm.chat. Returns {} for an unknown mini, a crash, or an unusable return."""
    spec = MINIS.get(name)
    if spec is None:
        memory.log(db, "mini", f"{name}({task!r}) -> unknown mini")  # models invent names
        return {}
    try:
        out = clean(name, spec["fn"](db, task))
    except Exception as e:
        memory.log(db, "mini", f"{name}({task!r}) -> failed: {type(e).__name__}: {e}")
        return {}
    memory.log(db, "mini", f"{name}({task!r}) -> {out}")
    return out


# ---------------------------------------------------------------- dispatch

_SYSTEM = f"""You decide which of {TIWA}'s minis should handle a message. You are NOT the reply and you never write words she says.
Answer in json.

Minis available:
{{minis}}

MOST MESSAGES NEED NO MINI. Ordinary chat, opinions, jokes, questions she can answer herself, someone insulting her — all of those are an empty dispatch list. Roughly three messages in five need nothing at all. An empty list is the normal answer, not a failure.

Dispatch a mini only when the message asks for something a mini actually does. Requests in Thai count exactly the same as English ones.

Give the mini the GOAL in one short phrase, not the user's sentence and not a plan. "their favourite song", "cancel Friday's dentist", "who won the match last night". The mini works out the rest — you do not tell it how.

One entry per distinct job. Two jobs in one message means two entries.

If you cannot tell what they want, dispatch nothing and write the one thing you would have to ask in "ask". A guess that acts is worse than a question."""


def _schema() -> dict:
    # every property required + additionalProperties false: OpenRouter sends this
    # as a STRICT json_schema and rejects anything looser (see gcal._EVENT_FORMAT).
    # "mini" is an enum of what is actually registered — the tool pass has to cope
    # with invented names, this pass simply cannot produce one.
    return {
        "type": "object",
        "properties": {
            "dispatch": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "mini": {"type": "string", "enum": sorted(MINIS)},
                        "task": {"type": "string"},
                    },
                    "required": ["mini", "task"],
                    "additionalProperties": False,
                },
            },
            "ask": {"type": ["string", "null"]},
        },
        "required": ["dispatch", "ask"],
        "additionalProperties": False,
    }


def parse(content: str) -> dict:
    """Model output -> {"dispatch": [(name, task)], "ask": str}. Never raises.

    Unknown minis are dropped rather than passed on: run() would drop them anyway,
    but dropping here keeps the count honest for dispatchbench.
    """
    try:
        d = json.loads(content or "{}")
    except json.JSONDecodeError:
        return {"dispatch": [], "ask": ""}
    jobs = [
        (j["mini"], str(j.get("task") or ""))
        for j in (d.get("dispatch") or [])
        if isinstance(j, dict) and j.get("mini") in MINIS
    ]
    return {"dispatch": jobs, "ask": str(d.get("ask") or "")}


async def dispatch(db, author: str, text: str, recent: str = "") -> dict:
    """Which minis, if any. One low-temp schema-constrained call.

    Deliberately NOT wired into pipeline.respond() yet — S1 ships the contract, S2
    moves music onto it. Nothing calls this in production today.
    """
    if not MINIS:
        return {"dispatch": [], "ask": ""}
    now = datetime.datetime.now()
    lines = "\n".join(f"- {n}: {m['description']}" for n, m in sorted(MINIS.items()))
    prefix = f"earlier lines (context only):\n{recent}\n\n" if recent else ""
    resp = await asyncio.to_thread(
        llm.chat,
        model=llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM.format(minis=lines)},
            {"role": "user",
             "content": f"today is {now:%Y-%m-%d}\n{prefix}{author}: {text}"},
        ],
        fmt=_schema(),
        options={"temperature": 0, "num_ctx": 2048},
    )
    return parse(resp["content"])


if __name__ == "__main__":  # runnable check: the contract, offline
    db = memory.connect(":memory:")

    @mini("test mini", ("title", "count", "tags"))
    def demo(db, task):
        return {"title": task, "count": 1, "tags": ["a"], "note": "tell them it worked"}

    @mini("always breaks", ("x",))
    def boom(db, task):
        raise RuntimeError("network")

    # undeclared keys are dropped — this is rule 2, and it is the only automatic
    # part of it. `note` would have been voice bleed reaching her verbatim.
    assert run(db, "demo", "lofi") == {"title": "lofi", "count": 1, "tags": ["a"]}
    assert clean("demo", {"title": {"nested": 1}}) == {}, "nested object survived"
    assert clean("demo", "just a sentence") == {}, "prose survived"
    assert clean("demo", None) == {}
    assert clean("nope", {"title": "x"}) == {}, "unregistered mini survived"
    assert run(db, "boom", "x") == {}, "a crashing mini must degrade, not raise"
    assert run(db, "ghost", "x") == {}, "invented mini name must be a no-op"

    assert _schema()["properties"]["dispatch"]["items"]["properties"]["mini"]["enum"] \
        == ["boom", "demo"]
    assert parse('{"dispatch":[{"mini":"demo","task":"lofi"}],"ask":null}') == {
        "dispatch": [("demo", "lofi")], "ask": ""}
    assert parse('{"dispatch":[{"mini":"ghost","task":"x"}],"ask":"which one?"}') == {
        "dispatch": [], "ask": "which one?"}
    assert parse("not json at all") == {"dispatch": [], "ask": ""}
    assert parse('{"dispatch":[],"ask":null}') == {"dispatch": [], "ask": ""}

    MINIS.clear()
    assert asyncio.run(dispatch(db, "Krich", "hi")) == {"dispatch": [], "ask": ""}, \
        "empty registry must not make a model call"
    print("minis ok: contract holds, undeclared fields dropped, junk degrades to {}")
