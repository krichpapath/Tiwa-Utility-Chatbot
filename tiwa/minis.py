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

These four are the measured over-firing, and Thai examples are given because the model does not generalise to them from English ones:
- An AGREEMENT or a one-word reply is not a request. "ใช่", "ช่ายๆๆ", "อือ", "1", "ok", "yes" — dispatch NOTHING. Whatever she offered, she will handle in her reply.
- A QUESTION ABOUT SOMETHING SHE ALREADY DID is not a request to do it again. "ข้ามคิวทำไม" (why did you skip), "ชื่อเพลง" / "เพลงนี้ชื่ออะไร" (what is this one called), "เปิดไปกี่เพลงแล้ว" — she knows what is on without looking. Dispatch NOTHING.
- SOMEONE STATING A FACT is not asking for anything. "hsr มาจาก honkai star rail", "Maa Yan ชื่อ อิง", "มันเขียนผิด" — that is them talking. Dispatch NOTHING.
- A CORRECTION OR A COMPLAINT about what is playing is only a music job if they asked for a different track. "เพลงห่วย" alone is an opinion; "เพลงห่วย เปลี่ยนที" is a job.

Dispatch a mini only when the message asks for something a mini actually does. Requests in Thai count exactly the same as English ones.

Give the mini the GOAL in one short phrase, not the user's sentence and not a plan. "their favourite song", "cancel Friday's dentist", "who won the match last night". The mini works out the rest — you do not tell it how.

One entry per distinct job. Two jobs in one message means two entries.

If you cannot tell what they want, dispatch nothing and write the one thing you would have to ask in "ask". A guess that ACTS is worse than a question.

BUT A LOOKUP IS NOT AN ACT. `search` changes nothing, costs nothing you can't undo, and she can always say "turns out it was X" a second later. So NEVER answer a question about the world with "ask" — send it to search and let the result settle it. Measured live: "ใครชนะบอลเมื่อคืน" (who won the football last night) got "which match do you mean?" and dispatched nothing, so she told them she knows nothing about football. Searching ผลบอลเมื่อคืน would have answered it.
- A vague question is still a searchable one. Search the obvious reading.
- Not knowing which team, which film, which patch, which year is a reason to SEARCH, not to ask.
- Save "ask" for the things that ACT and cannot be taken back: a calendar write on an ambiguous date, a song when you genuinely cannot tell whether they wanted music at all."""


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
    # valid JSON that is not an object: "null", "[]", a bare string. OpenRouter's
    # strict schema cannot produce it, but the cost ceiling silently falls back to
    # local ollama (llm.chat), whose `format` is not strict — and .get() on a list
    # is an AttributeError that eats the whole turn.
    if not isinstance(d, dict):
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


# ---------------------------------------------------------------- DJ Tiwa

_DJ_SYSTEM = """You are the DJ. Decide what to do with the deck and what to search for. Answer in json.

ACTION — pick one:
- play  : put something on now. Also the right answer when a song is already on and they want a DIFFERENT one instead.
- queue : they want more music AFTER what is on, not instead of it.
- skip  : they are bored of the current song. Thai: 'ข้ามเพลง', 'เปลี่ยนเพลง', 'ถัดไป', 'ไม่เอาเพลงนี้'.
- stop  : music off, queue cleared. Thai: 'หยุดเพลง', 'ปิดเพลง', 'พอแล้ว'.
- none  : this is not a request for music at all.

TERMS — what to search YouTube for. Empty string for skip, stop and none.
If they NAMED a song, output that name plus at most the artist or the game it is from, and NOTHING else. A descriptive word they did not say finds a different song: 'Red Line' from Warframe became 'Red Line Warframe chase' and played the wrong track.
Only when they named no song at all — just a mood, a genre, a game or an activity — invent terms that fit it. They do not have to name a song, and asking them which genre instead of picking one is a failure.

NONE IS THE VETO AND IT IS THE MOST IMPORTANT THING YOU DO. You are called on any message with a music-ish word in it, including plenty that have nothing to do with music, because starting you early is cheap and being wrong here is not. Putting a random song over an unrelated message is far worse than doing nothing.

So before anything else: are they ASKING YOU TO DO SOMETHING TO THE MUSIC — start it, queue it, skip it, or stop it? If instead they are telling you a fact, thanking you, apologising, or talking about someone, the answer is none no matter which words they used.

Wanting the music OFF is still a job. 'หยุดเพลง', 'ปิดเพลง', 'พอแล้ว', 'stop' are stop, never none.

- STATING SOMETHING IS NOT ASKING FOR IT. 'my dad plays Warframe', 'he plays guitar', 'พี่ชายเล่นกีตาร์' — they are describing a person. none.
- 'play' about a GAME, a SPORT or an INSTRUMENT is not music. 'they play football', 'she plays piano at school'. none.
- The Thai verbs เปิด (open/turn on), ขอ (ask for), เล่น (play), ใส่ (put in) attach to anything at all: 'เปิดประตู' (open the door), 'เปิดไฟ' (turn on the light), 'ขอโทษ' (sorry), 'ขอบคุณ' (thank you), 'ขอ ยืมตังหน่อย' (lend me money), 'อยากกินข้าว' (want to eat). Every one of those is none.
- A question ABOUT the music is not a request for music. They are asking, not ordering.

Only when they actually want to hear something does anything else apply."""

_DJ_FORMAT = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["play", "queue", "skip", "stop", "none"]},
        "terms": {"type": "string"},
    },
    "required": ["action", "terms"],
    "additionalProperties": False,
}


@mini(
    "plays, queues, skips or stops music. Give it what they want to hear — a song, "
    "an artist, a mood, a game, or 'whatever you like'. It reads the deck and picks "
    "the search terms itself, so you never decide between play and queue. Thai asks "
    "count the same: เปิดเพลง, ขอเพลง, อยากฟัง, ใส่คิว, ข้ามเพลง, หยุดเพลง.",
    ("action", "terms", "playing", "queued"),
)
def dj(db, task: str) -> dict:
    """One call decides the action AND the search terms; the deck is read, not asked.

    This is `_TERMS_SYSTEM` plus the play/queue/skip/stop choice that used to cost
    Main Tiwa four competing tools. She now sees one mini instead, and the deck
    state reaches the decision as a FACT rather than as a tool call she had to
    remember to make.
    """
    from . import music, tools  # lazy: music pulls in av, tools imports memory

    now = music.NOW["title"]
    deck = (f"Playing right now: {now}. {len(music.QUEUE)} song(s) queued."
            if now else "Nothing is playing and the queue is empty.")
    resp = llm.chat(
        model=llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[{"role": "system", "content": _DJ_SYSTEM},
                  {"role": "user", "content": f"{deck}\n\nThey want: {task}"}],
        fmt=_DJ_FORMAT,
        options={"temperature": 0, "num_ctx": 1024},
    )
    try:
        out = json.loads(resp["content"] or "{}")
        action, terms = out["action"], (out.get("terms") or "").strip()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return {}  # run() logs it; she asks instead of guessing

    # The tool functions still do the acting, so they still write to the Turn and
    # bot._flush_music still drains it unchanged. What changed is who decides.
    if action in ("play", "queue") and not terms:
        action = "none"  # a play with no terms would search the empty string
    if action == "play":
        tools.play_music(db, terms)
    elif action == "queue":
        tools.queue_music(db, terms)
    elif action == "skip":
        tools.skip_music(db, "")
    elif action == "stop":
        tools.stop_music(db, "")

    # facts only. play_music's return value is a paragraph of "do NOT name the
    # artist" — that is a prohibition, it belongs to pipeline._doing(), and it is
    # exactly what rule 2 exists to keep out of a mini's return.
    return {"action": action, "terms": terms, "playing": now,
            "queued": len(music.QUEUE)}


# ---------------------------------------------------------------- Search Tiwa

_SEARCH_SYSTEM = """Turn this into web search KEYWORDS. Output ONLY the keywords, nothing else.

Never the sentence they typed. Strip 'what is', 'do you know', 'มึงรู้ไหมว่า', 'อยากรู้ว่า'. Keep names and numbers.

Add the year for anything current — a model dates itself from its training data, and a wrong year is a wrong page back. Today's date is given below; use that year.

Search in the language the answer lives in. A Thai question about Thai football wants Thai keywords; a question about a game or a film usually wants English ones.

  'มึงรู้ไหมว่าใครชนะบอลเมื่อคืน' -> ผลบอลเมื่อคืน
  "what's that new gojo thing everyone's on about" -> Jujutsu Kaisen new season
  'is the new iphone any good' -> iPhone review
  'เห็นเขาบอกว่าร้านนี้ดี จริงไหม' -> รีวิว ร้าน[ชื่อร้าน]"""


@mini(
    "looks something up on the web. Give it the question — a score, a price, some "
    "news, a game or show or person she does not recognise. It picks the keywords "
    "itself. Not for people she should already remember.",
    ("query", "found"),
)
def search(db, task: str) -> dict:
    """Keywords, then one search. Read-only, and never on the path to her mouth.

    The keyword rules were measured on the `web_search` tool description and move
    here whole. What changes is that they are the ONLY thing in this prompt —
    they used to be one of ten tool descriptions competing for attention, and
    tools.md already records that every added tool costs the others.

    ponytail: one hop. Multi-hop was the argument for Search being a real agent
    rather than a function, but it is 8 calls in 135 and nothing has missed yet.
    Add the second hop when a real question needs one, not before.
    """
    from . import pipeline, tools  # lazy: neither imports this module

    now = datetime.datetime.now()
    resp = llm.chat(
        model=llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[{"role": "system", "content": _SEARCH_SYSTEM},
                  {"role": "user", "content": f"today is {now:%Y-%m-%d}\n{task}"}],
        options={"temperature": 0, "num_ctx": 1024},
    )
    # _terms() already does this job for the music retry: _clean() to drop the
    # <think> and <tool_call> the 8B leaks as text, then the first real line,
    # unquoted, capped. Reimplementing it here got the <think> case wrong.
    query = pipeline._terms(resp["content"]) or task[:80]
    hits = tools.web_search(db, query)
    if hits.startswith("search failed") or hits.startswith("every result"):
        # tools never raise, so a network flake arrives as text. It is not an
        # answer, and handing it to her as one is how she quotes an error at
        # someone. Empty found -> _late() says nothing at all.
        memory.log(db, "mini", f"search({query!r}) -> {hits[:80]}")
        return {"query": query, "found": ""}
    return {"query": query, "found": hits[:800]}


# ---------------------------------------------------------------- Calendar Tiwa

_CAL_SYSTEM = """You handle Krich's calendar. Answer in json.

You are given the next 7 days. Decide ONE action:
- read  : they asked what is on. The events are already below; you add nothing.
- write : they want something added or cancelled. Put the WHOLE change in one plain sentence, e.g. "add dentist Tuesday 15:00" or "cancel Friday's meeting". Krich still has to confirm it, so a write is never the risky choice.
- none  : the message is not about the calendar.

ASK instead of guessing when the date is genuinely ambiguous. "next Tuesday" the week after this one, or the Tuesday coming? A day with no date when two of them are in range? Write the question in "ask", leave action as none, and change nothing. A guess that lands in someone's calendar is worse than a question.
A time they did state is not ambiguous. Do not ask for confirmation of something they already said.

CLASH: if the new thing overlaps something already on the calendar, say which one in "clash". Still do the write — Krich decides, you point it out.

MENTION: worth bringing up unprompted only if it is genuinely soon and they seem not to know. Most turns this is false. She is not a butler and does not read the diary at people."""

_CAL_FORMAT = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["read", "write", "none"]},
        "request": {"type": "string"},
        "ask": {"type": "string"},
        "clash": {"type": "string"},
        "mention": {"type": "boolean"},
    },
    "required": ["action", "request", "ask", "clash", "mention"],
    "additionalProperties": False,
}


@mini(
    "reads and changes Krich's calendar. Give it what they said about a plan — "
    "'put dentist on Tuesday', 'what have I got on', 'cancel Friday'. It reads the "
    "week itself, spots clashes, and asks when a date is ambiguous instead of "
    "guessing. Krich still confirms every change with a reaction.",
    ("action", "events", "queued", "ask", "clash"),
)
def calendar(db, task: str) -> dict:
    """The judgment half. The mechanics below it were already right.

    Parsing a date into an event is `gcal._EVENT_FORMAT`, and it handles Thai
    titles, relative dates and Buddhist years already. The write itself is the ✅
    gate. Neither moves here, and neither is ever an agent's call — this decides
    WHAT to propose, never that it happens.
    """
    from . import gcal, tools  # lazy: gcal pulls in the google client

    week = gcal.upcoming()
    # Google is unreachable — an expired refresh token, usually. gcal never
    # raises, so this arrives as text, and `events` reaches her voice verbatim
    # through VOICE_FIELDS: without this she reads out
    # "calendar unavailable: ('invalid_grant: Bad Request', {...})". Same guard
    # `search` already has, and found the same way — by the thing actually
    # breaking. Do not queue a write either: the ✅ would only fail later, and
    # asking someone to confirm something that cannot happen is its own bluff.
    if week.startswith("calendar unavailable"):
        memory.log(db, "mini", f"calendar({task!r}) -> {week[:90]}")
        return {"action": "none", "queued": "", "ask": "", "clash": "",
                "events": "you cannot reach the calendar at all right now — say so,"
                          " and say nothing about what is or is not on it"}
    now = datetime.datetime.now()
    resp = llm.chat(
        model=llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[{"role": "system", "content": _CAL_SYSTEM},
                  {"role": "user",
                   "content": f"today is {now:%A %Y-%m-%d}\n\nnext 7 days:\n{week}"
                              f"\n\nThey said: {task}"}],
        fmt=_CAL_FORMAT,
        options={"temperature": 0, "num_ctx": 2048},
    )
    try:
        out = json.loads(resp["content"] or "{}")
        action = out["action"]
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return {}
    ask, clash = (out.get("ask") or "").strip(), (out.get("clash") or "").strip()
    request = (out.get("request") or "").strip()

    if ask:
        action = "none"  # asking and acting in the same breath is the guess it replaces
    if action == "write" and request:
        tools.calendar_write(db, request)  # queues only; Krich's ✅ is the write
    return {"action": action, "events": week[:600] if action == "read" else "",
            "queued": request if action == "write" else "", "ask": ask, "clash": clash}


if __name__ == "__main__":  # runnable check: the contract, offline
    db = memory.connect(":memory:")
    MINIS.clear()  # the real registry is not the fixture; dj has its own bench

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
