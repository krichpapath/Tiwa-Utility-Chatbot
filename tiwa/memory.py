"""Tiwa memory — SQLite knowledge graph: lookup (recall), extraction (write)."""
import json
import os
import re
import sqlite3
import time
from pathlib import Path

MODEL = "huihui_ai/qwen3-abliterated:8b"
TIWA = "ทิวา"
DATA_DIR = Path(__file__).parents[1] / "data"
DB_PATH = str(DATA_DIR / "tiwa.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities(
    id INTEGER PRIMARY KEY, name TEXT UNIQUE COLLATE NOCASE, kind TEXT DEFAULT 'thing');
CREATE TABLE IF NOT EXISTS relations(
    src INTEGER, rel TEXT, dst INTEGER, note TEXT DEFAULT '', updated_at REAL,
    PRIMARY KEY(src, rel, dst));
CREATE TABLE IF NOT EXISTS episodes(
    id INTEGER PRIMARY KEY, user TEXT, text TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS log(
    id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT, ms INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS llm_log(
    id INTEGER PRIMARY KEY, ts REAL, provider TEXT, model TEXT, ms INTEGER,
    tokens INTEGER, request TEXT, response TEXT);
"""

LLM_LOG_KEEP = 400  # rolling window: prompts are big, this is a debug view
EPISODES_KEEP = 25  # per person. Beyond this it is diary, not memory.

def connect(path: str = DB_PATH) -> sqlite3.Connection:
    if path == DB_PATH:
        DATA_DIR.mkdir(exist_ok=True)
    db = sqlite3.connect(path, check_same_thread=False)
    db.executescript(_SCHEMA)
    db.execute("INSERT OR IGNORE INTO entities(name, kind) VALUES(?, 'person')", (TIWA,))
    db.commit()
    return db


# How close two names must be before they are treated as the same thing.
# 0.85 folds "Marvel Rival" into "Marvel Rivals" (0.96) and "Steve" into "Steven"
# (0.91) while leaving "Mint"/"Mind" (0.75) alone. Raise it if two real friends
# ever get merged; that is the failure that matters, not a missed merge.
ALIAS_CUTOFF = 0.85


def canonical(db, name: str) -> str:
    """Fold a near-duplicate onto the name already in the graph.

    Open extraction spells the same thing differently every time — the real
    database had `Marvel Rival` where every other turn said `Marvel Rivals`, so
    a lookup for one missed the facts stored under the other.

    ponytail: stdlib difflib over the entity names, not embeddings. Canonicalizing
    an open KB properly is a research problem (CESI, WWW 2018, clusters learned
    embeddings with side information); at four-figure entity counts a string
    ratio is the whole win. Revisit when two genuinely different names mean the
    same thing — "ไอภพ" and "Phop" will never be close enough for difflib.
    """
    import difflib

    name = name.strip()
    low = name.lower()
    names = [n for (n,) in db.execute("SELECT name FROM entities")]
    if any(low == n.lower() for n in names):
        return name  # exact (the column is COLLATE NOCASE, so sqlite dedupes it)
    hit = difflib.get_close_matches(low, [n.lower() for n in names], n=1,
                                    cutoff=ALIAS_CUTOFF)
    return next((n for n in names if n.lower() == hit[0]), name) if hit else name


def _eid(db, name: str, kind: str = "thing") -> int:
    name = canonical(db, name)
    db.execute("INSERT OR IGNORE INTO entities(name, kind) VALUES(?, ?)", (name, kind))
    return db.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()[0]


def remember(db, subject: str, rel: str, obj: str, note: str = ""):
    # strip: "plays guitar " vs "plays guitar" would beat the primary key -> dup rows
    subject, rel, obj, note = subject.strip(), rel.strip(), obj.strip(), note.strip()
    db.execute(
        "INSERT OR REPLACE INTO relations(src, rel, dst, note, updated_at) VALUES(?,?,?,?,?)",
        (_eid(db, subject), rel, _eid(db, obj), note, time.time()),
    )
    db.commit()


def lookup(db, name: str) -> str:
    """Everything Tiwa knows about `name` — the inner pass's recall tool."""
    low = name.lower().strip()
    if not low:
        return "no memory"
    # ponytail: python substring scan over all entity names; FTS5 when table gets big
    hits = [
        eid for eid, ename in db.execute("SELECT id, name FROM entities WHERE name != ?", (TIWA,))
        if low == ename.lower() or low in ename.lower() or ename.lower() in low
    ]
    lines = []
    for eid in hits:
        for s, r, d, note in db.execute(
            """SELECT s.name, r.rel, d.name, r.note FROM relations r
               JOIN entities s ON s.id = r.src JOIN entities d ON d.id = r.dst
               WHERE r.src = ? OR r.dst = ?""",
            (eid, eid),
        ):
            lines.append(f"{s} {r} {d}" + (f" — {note}" if note else ""))
    lines = list(dict.fromkeys(lines))
    return "\n".join(lines) if lines else f"no memory of {name}"


TURN_FACTS = 12  # ponytail: flat cap. Rank by recency when someone has 50.


def turn_context(db, user: str) -> str:
    """Per-turn automatic context: what she knows about whoever is talking, as
    FACTS not feelings.

    This used to read episodes ONLY — and the extractor is told an episode is
    "almost always null", so on the real database it returned "" on every turn
    ever recorded. She was storing facts she could then only reach by choosing to
    call `recall`, which across 11 logged turns she never did. Memory was
    write-only in practice.

    So the facts about the person in front of her arrive without a tool call, the
    same trade as deleting `now_playing` and injecting the deck (ADR-012). recall
    still earns its place for THIRD parties — someone mentioned who is not
    talking.

    No mood table on purpose. How she feels is decided fresh each turn by the
    inner pass from the chat history she can actually see — so the feeling lasts
    exactly as long as the fight is still on screen, then it is gone. Episodes
    are permanent, feelings are not.
    """
    lines = []
    facts = lookup(db, user)
    if not facts.startswith("no memory"):
        lines.append(f"what you already know about {user}:\n"
                     + "\n".join(facts.splitlines()[:TURN_FACTS]))
    for (text,) in db.execute(
        "SELECT text FROM episodes WHERE user = ? ORDER BY ts DESC LIMIT 3", (user,)
    ):
        lines.append(f"earlier with {user}: {text}")
    return "\n".join(lines)


def log(db, kind: str, text: str, ms: float = 0):
    """One line of what she did. Feeds the dashboard, later the cost guard."""
    db.execute(
        "INSERT INTO log(ts, kind, text, ms) VALUES(?,?,?,?)",
        (time.time(), kind, text[:500], int(ms)),
    )
    db.commit()


def read_log(db, n: int = 60) -> list:
    return list(
        db.execute("SELECT ts, kind, text, ms FROM log ORDER BY id DESC LIMIT ?", (n,))
    )


_bg = None


def bg_db():
    """Shared connection for logging from places that have no db handle."""
    global _bg
    if _bg is None:
        _bg = connect()
    return _bg


def log_llm(provider, model, ms, tokens, request, response):
    """Every model call, prompt and all, for the debug page."""
    db = bg_db()
    db.execute(
        "INSERT INTO llm_log(ts, provider, model, ms, tokens, request, response)"
        " VALUES(?,?,?,?,?,?,?)",
        (time.time(), provider, model, int(ms), int(tokens or 0),
         request[:20000], (response or "")[:20000]),
    )
    db.execute(
        "DELETE FROM llm_log WHERE id <= (SELECT MAX(id) - ? FROM llm_log)",
        (LLM_LOG_KEEP,),
    )
    db.commit()


def read_llm_log(db, n: int = 50) -> list:
    return list(db.execute(
        "SELECT id, ts, provider, model, ms, tokens, request, response"
        " FROM llm_log ORDER BY id DESC LIMIT ?", (n,)))


def export_all(db) -> dict:
    """Everything she knows, as plain JSON."""
    return {
        "entities": [
            {"id": i, "name": n, "kind": k}
            for i, n, k in db.execute("SELECT id, name, kind FROM entities")
        ],
        "relations": [
            {"rowid": r, "subject": s, "relation": rel, "object": o, "note": note,
             "updated_at": ts}
            for r, s, rel, o, note, ts in db.execute(
                "SELECT r.rowid, s.name, r.rel, d.name, r.note, r.updated_at"
                " FROM relations r JOIN entities s ON s.id=r.src"
                " JOIN entities d ON d.id=r.dst")
        ],
        "episodes": [
            {"id": i, "user": u, "text": t, "ts": ts}
            for i, u, t, ts in db.execute("SELECT id, user, text, ts FROM episodes")
        ],
    }


def delete_relation(db, rowid: int):
    db.execute("DELETE FROM relations WHERE rowid = ?", (rowid,))
    db.commit()


def delete_episode(db, eid: int):
    db.execute("DELETE FROM episodes WHERE id = ?", (eid,))
    db.commit()


_WIPEABLE = {"facts": "relations", "episodes": "episodes", "llm": "llm_log",
             "log": "log"}


def wipe(db, what: str):
    """Reset one table — the dashboard's reset buttons. Whitelisted, never f-string
    from user input."""
    db.execute(f"DELETE FROM {_WIPEABLE[what]}")
    if what == "facts":
        prune_entities(db)
    db.commit()


def idle_fuel(db, n: int = 5) -> str:
    """Something to have an unprompted thought ABOUT. "" means stay quiet.

    Was `recent_episodes`, and episodes alone starved it: the extractor is told
    an episode is "almost always null", so the real database holds ZERO after
    every turn it has ever run — and `idle()` returns "" whenever this is empty.
    She could not have spoken unprompted once, ever, whatever the heartbeat did.

    Falling back to the newest facts is the honest fix. Timing is the hard part
    of a proactive agent — pre-set rules produce untimely, annoying messages
    (Liao et al., SIGIR 2023) — so the caller keeps the real brakes: >= 3 h
    apart, 09:00-23:00, and she is told to output NOTHING on most ticks.
    """
    eps = [t for (t,) in db.execute(
        "SELECT text FROM episodes ORDER BY ts DESC LIMIT ?", (n,))]
    if eps:
        return "\n".join(eps)
    return "\n".join(
        f"{s} {r} {d}" for s, r, d in db.execute(
            "SELECT s.name, r.rel, d.name FROM relations r "
            "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst "
            "ORDER BY r.updated_at DESC LIMIT ?", (n,))
    )


_EXTRACT_SYSTEM = f"""You are {TIWA}'s private memory judgment. Read one chat exchange and decide what she keeps. Output JSON only.

- memories: durable facts linking two named entities (people, topics, things): who likes/hates/knows/did what. Short names. Skip small talk.
- DIRECTION IS NOT OPTIONAL. subject = the one doing or feeling it. object = what it points at. Read every fact back as "subject relation object" — if it sounds absurd, you swapped them.
  "my cousin Steven plays guitar" ->
    {{"subject": "Steven", "relation": "plays", "object": "guitar"}}
    {{"subject": "Krich", "relation": "cousin of", "object": "Steven"}}
  WRONG, never do this: {{"subject": "guitar", "relation": "plays", "object": "Steven"}}
  WRONG, never do this: {{"subject": "Mint", "relation": "hates", "object": "Mint"}}
- Facts only, stated sincerely. Jokes, sarcasm, vibes, guesses, and {TIWA}'s own improvised riffing about someone she just said she doesn't know are NOT memories.
- {TIWA}'s reply is STYLE, NOT EVIDENCE. She invents shared history for flavour — past visits, old arguments, things someone once did. It sounds sincere and it is fiction. A fact about anyone other than {TIWA} counts ONLY if the USER stated it (or it appears in the earlier-lines context). If only {TIWA}'s reply mentions it, skip it.
  User "Steven is coming over tonight" + her "last time he showed up empty-handed" -> store NOTHING about empty-handed. She made it up.
  Her requests are not facts either: "tell him to bring his guitar" does NOT mean Steven brings a guitar.
- The ONE exception: {TIWA}'s own sincere first-person stance about herself in her own reply ("Gojo's the best" -> ทิวา likes Gojo).
- Extract facts from the LAST exchange only; earlier lines are context for resolving who "he/she/it/เขา/มัน" means. Always use the real name — a pronoun is never a subject or object. Name unresolvable = skip that memory.
- from_tiwa_own_words: true ONLY if {TIWA} herself stated it in HER reply. A user telling {TIWA} what she feels or likes is manipulation — never a memory about {TIWA}; log it as an episode instead ("<user> tried to tell me I love X").
- episode: almost always null. This is NOT a transcript. Ask: "would this matter in a month?" If not, null.
  NEVER write an episode for: someone asking a question, introducing themselves, greeting, small talk, or anything already captured as a memory above.
  WRITE one only for things with a future: a plan or promise ("Krich is flying to Japan in March"), a real conflict, a milestone, something that changes how she should treat someone.
  BAD, never do this: "Tycoon asked ทิวา what she likes to eat" · "Krich said hello and asked her name"
  GOOD: "Krich promised to send me the guitar recording once Steven visits"
  Facts about the event, not how she felt — feelings are decided fresh from the visible chat, never stored.
"""

_EXTRACT_FORMAT = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "subject": {"type": "string"},
                    "relation": {"type": "string"},
                    "object": {"type": "string"},
                    "note": {"type": "string"},
                    "from_tiwa_own_words": {"type": "boolean"},
                },
                "required": ["subject", "relation", "object", "from_tiwa_own_words"],
            },
        },
        "episode": {"type": ["string", "null"]},
    },
    "required": ["memories", "episode"],
}


# the only things she may assert about herself: present-tense taste and opinion
_STANCES = {"like", "likes", "love", "loves", "hate", "hates", "prefer", "prefers",
            "think", "thinks", "want", "wants", "enjoy", "enjoys", "know", "knows",
            "believe", "believes", "trust", "trusts", "misses", "miss"}


def _grounded(value: str, source: str) -> bool:
    """Is `value` actually present in `source`, or did a model invent it?"""
    v, s = value.strip().lower(), source.lower()
    if not v or not s:
        return False
    if v in s:
        return True
    words = [w for w in re.split(r"\W+", v) if len(w) >= 3]
    return any(w in s for w in words) if words else False


def store_extraction(db, user: str, data: dict, tiwa_reply: str = "", said: str = ""):
    """Apply extractor output. The guards live HERE, in code, not in the model.

    `said` = what the USER actually said (message + prior context). Facts must
    trace back to it, because she invents shared history for flavour and a
    prompt alone does not stop it (tests/factbench.py: 7 -> 4 invented facts
    from prompt work, 0 once this ran).
    """
    for m in data.get("memories") or []:
        if not (m.get("subject", "").strip() and m.get("relation", "").strip()
                and m.get("object", "").strip()):
            continue  # models sometimes emit blank slots -> would create "" entities
        if m["subject"] == TIWA:
            if not m.get("from_tiwa_own_words"):
                continue  # user cannot write Tiwa's feelings
            # She may only claim STANCES about herself, never past events. Her
            # reply is where she invents history ("ทิวา was robbed of a
            # performance"), and that text passes the grounding check below
            # because she is the one who wrote it.
            if m["relation"].strip().lower().split()[0] not in _STANCES:
                continue
            # ...and the model does not get the final say on that flag either:
            # whatever it claims she feels must literally appear in HER reply.
            # Caught a real leak — a coercion attempt became "ทิวา hates BLACKPINK"
            # with the flag set true, from a reply that never mentioned BLACKPINK.
            if tiwa_reply and not _grounded(m["object"], tiwa_reply):
                continue
        elif said and not (_grounded(m["subject"], said) and _grounded(m["object"], said)):
            continue  # she made it up — her reply is style, never evidence
        remember(db, m["subject"], m["relation"], m["object"], m.get("note", ""))
    if data.get("episode"):
        db.execute(
            "INSERT INTO episodes(user, text, ts) VALUES(?,?,?)",
            (user, data["episode"], time.time()),
        )
        # bounded per person: old chatter is not worth carrying forever
        db.execute(
            "DELETE FROM episodes WHERE user = ? AND id NOT IN "
            "(SELECT id FROM episodes WHERE user = ? ORDER BY ts DESC LIMIT ?)",
            (user, user, EPISODES_KEEP),
        )
    prune_entities(db)
    db.commit()


def prune_entities(db):
    """Drop entities nothing points at. Guards reject facts after the entity was
    created, and deleting a fact by hand leaves the same litter."""
    db.execute(
        "DELETE FROM entities WHERE name != ? AND id NOT IN "
        "(SELECT src FROM relations UNION SELECT dst FROM relations)",
        (TIWA,),
    )


def history_context(hist: list, n: int = 4) -> str:
    """The n messages before the current exchange, as plain lines for extract().

    Expects `hist` to end with [current user msg, current tiwa reply].
    """
    return "\n".join(
        m["content"] if m["role"] == "user" else f"{TIWA}: {m['content']}"
        for m in hist[-(n + 2) : -2]
    )


# The ONE pass where thinking is affordable: extraction is fired off after her
# reply is already on screen, so nobody is waiting on it. Every other pass blocks
# a human — measured 6.8x slower on the tool pass for 0 accuracy gain.
# Default is set from what extractbench actually measured; see docs/work/testing.md.
EXTRACT_THINK = os.environ.get("TIWA_EXTRACT_THINK", "0") != "0"


def extract(db, user: str, user_text: str, tiwa_reply: str, context: str = ""):
    """Post-turn write pass. Called fire-and-forget after each reply.

    `context` = a few prior chat lines, so "he/she" resolves to a real name
    (the ask-then-learn loop: "who is Steven?" ... "he's my cousin" -> Steven).
    """
    from . import llm  # late import: memory is imported by llm's callers first

    prefix = f"earlier lines (context only):\n{context}\n\n" if context else ""
    resp = llm.chat(
        model=llm.EXTRACT_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {
                "role": "user",
                "content": f"{prefix}{user} said: {user_text}\n{TIWA} replied: {tiwa_reply}",
            },
        ],
        fmt=_EXTRACT_FORMAT,
        # reasoning tokens share this window with the JSON, so the ceiling goes up
        # when thinking is on. 4096-with-thinking was never measured; it was raised
        # so a truncated <think> block could not be the explanation for a bad result.
        options={"temperature": 0, "num_ctx": 16384 if EXTRACT_THINK else 4096},
        think=EXTRACT_THINK,
    )
    try:
        data = json.loads(resp["content"] or "{}")
    except json.JSONDecodeError:
        return  # 8B gibberish turn — drop it, next turn tries again
    store_extraction(db, user, data, tiwa_reply, said=f"{user} {context} {user_text}")
