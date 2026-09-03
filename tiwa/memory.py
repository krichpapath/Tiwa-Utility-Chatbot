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
    # NOT folding on containment. "Gojo" vs "Gojo Satoru" (0.62, under the cutoff)
    # is the tempting case, but the same rule merges "Blade" with "Blade Runner",
    # and a wrong merge is unrecoverable while a split pair still READS as one —
    # lookup() matches substrings in both directions. Two nodes, one answer.
    hit = difflib.get_close_matches(low, [n.lower() for n in names], n=1,
                                    cutoff=ALIAS_CUTOFF)
    return next((n for n in names if n.lower() == hit[0]), name) if hit else name


REL_STEM = 4  # characters of the first word that must match


def _stem(rel: str) -> str:
    """Crude stem: first REL_STEM characters of the first word, lowercased."""
    first = rel.strip().lower().split(" ")[0]
    return first[:REL_STEM]


def canonical_rel(db, src: int, dst: int, rel: str) -> str:
    """Reuse the relation already recorded between these two, if it means the same.

    The seeded graph held `Tycoon playing Marvel Rivals` AND `Tycoon plays Marvel
    Rivals` — one fact, two rows, because the primary key is (src, rel, dst) and
    `plays` != `playing`.

    Stem, NOT difflib, and that is measured: `likes`/`dislikes` scores 0.769 while
    `plays`/`playing` scores only 0.667, so every cutoff that folds the pair we
    want also merges a relation with its own opposite. A 4-character stem of the
    first word separates them cleanly — play/play folds, like/disl does not.

    Scoped to the SAME (subject, object) pair on purpose. "plays" and "played" are
    one fact about one pair; across the whole graph they are not.
    """
    rel = rel.strip()
    have = [r for (r,) in db.execute(
        "SELECT rel FROM relations WHERE src = ? AND dst = ?", (src, dst))]
    if not have or rel.lower() in (r.lower() for r in have):
        return rel
    stem = _stem(rel)
    if len(stem) < REL_STEM:
        return rel  # too short to stem safely: "is", "in", "of" must match exactly
    return next((r for r in have if _stem(r) == stem), rel)


# Two answers to the SAME question: you cannot both like and hate one thing.
# stem -> (axis, polarity). Same axis replaces the old row instead of sitting
# beside it; opposite polarity is a real change of mind, which surprise() reports.
# ponytail: one hand-listed axis. Add another when a real contradiction shows up.
_AXES = {
    "like": ("feel", 1), "love": ("feel", 1), "enjo": ("feel", 1), "pref": ("feel", 1),
    "hate": ("feel", -1), "disl": ("feel", -1),
}


def _supersede(db, src: int, dst: int, rel: str) -> str:
    """Delete the beliefs this new one replaces. Returns the flipped one, if any.

    The primary key is (src, rel, dst), so `Tycoon likes X` and `Tycoon hates X`
    were two valid rows and BOTH were injected every turn — she read a flat
    contradiction and picked one at random. People update a belief; she only ever
    appended. Scoped to one (subject, object) pair, like canonical_rel().
    """
    axis, pol = _AXES.get(_stem(rel), (None, 0))
    if not axis:
        return ""
    flipped = ""
    # fetchall first: deleting under an open cursor on the same table
    for (old,) in db.execute(
        "SELECT rel FROM relations WHERE src = ? AND dst = ?", (src, dst)
    ).fetchall():
        oaxis, opol = _AXES.get(_stem(old), (None, 0))
        if oaxis != axis or old.lower() == rel.lower():
            continue
        db.execute("DELETE FROM relations WHERE src=? AND rel=? AND dst=?", (src, old, dst))
        if opol != pol:
            flipped = old
    return flipped


def _known(db, name: str) -> bool:
    """Has she met this entity before? Asked BEFORE the write, since _eid creates it.

    Goes through canonical() so "Marvel Rival" does not read as a stranger one
    letter away from something she already knows.
    """
    return db.execute(
        "SELECT 1 FROM entities WHERE name = ?", (canonical(db, name),)).fetchone() is not None


def _eid(db, name: str, kind: str = "thing") -> int:
    name = canonical(db, name)
    db.execute("INSERT OR IGNORE INTO entities(name, kind) VALUES(?, ?)", (name, kind))
    return db.execute("SELECT id FROM entities WHERE name = ?", (name,)).fetchone()[0]


def remember(db, subject: str, rel: str, obj: str, note: str = "") -> str:
    """Write one fact. Returns the belief it overturned, or "" — see surprise()."""
    # strip: "plays guitar " vs "plays guitar" would beat the primary key -> dup rows
    subject, rel, obj, note = subject.strip(), rel.strip(), obj.strip(), note.strip()
    src, dst = _eid(db, subject), _eid(db, obj)
    rel = canonical_rel(db, src, dst, rel)
    flipped = _supersede(db, src, dst, rel)
    db.execute(
        "INSERT OR REPLACE INTO relations(src, rel, dst, note, updated_at) VALUES(?,?,?,?,?)",
        (src, rel, dst, note, time.time()),
    )
    db.commit()
    return flipped


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


MENTION_MIN = 3  # chars — below this a name matches inside ordinary words
MENTION_MAX = 3  # entities per turn; a flat cap, same stance as TURN_FACTS


def mentioned(db, text: str, skip: str = "") -> str:
    """Facts about anyone NAMED in the message — the `recall` tool, without the call.

    turn_context() already covers the person talking. This covers the third
    parties recall existed for, and recall was 55 of 135 logged tool calls — the
    single most common reason the tool pass ran a second round. A sqlite scan
    costs 3ms; the round trip that used to fetch this cost about 2.6 seconds.

    ponytail: substring match, precision-biased like _MUSIC_ASK. Thai does not
    space its own words, so a short name can sit inside an unrelated word —
    MENTION_MIN is what keeps อิง out of every sentence containing it. FTS5 or
    embeddings when the graph is big enough that this misses more than it finds.
    """
    low = text.lower()
    names = [
        name
        for (name,) in db.execute(
            "SELECT name FROM entities WHERE name NOT IN (?, ?)", (TIWA, skip)
        )
        if len(name) >= MENTION_MIN and name.lower() in low
    ]
    out = []
    for name in names[:MENTION_MAX]:
        facts = lookup(db, name)
        if not facts.startswith("no memory"):
            out.append(f"you remember {name}:\n{facts}")
    return "\n".join(out)


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


REFLECT_EVERY = 3  # unreflected episodes before thinking about them is worth a call


def unreflected(db, n: int = 10) -> list:
    """Episodes she has lived but not yet thought about. Newest first.

    Her reflections are stored as episodes filed under her OWN name, so they land
    back in the stream they were drawn from and idle_fuel picks them up like
    anything else she lived — the Generative Agents trick, and it means no new
    table. It also dates the watermark for free: everything after her last
    reflection is what she has not processed.
    """
    last = db.execute(
        "SELECT MAX(ts) FROM episodes WHERE user = ?", (TIWA,)).fetchone()[0] or 0
    return list(db.execute(
        "SELECT user, text FROM episodes WHERE user != ? AND ts > ? ORDER BY ts DESC LIMIT ?",
        (TIWA, last, n)))


def reflect(db, thought: str):
    """Store one settled conclusion. Same table, her own name."""
    db.execute("INSERT INTO episodes(user, text, ts) VALUES(?,?,?)",
               (TIWA, thought.strip(), time.time()))
    db.execute(
        "DELETE FROM episodes WHERE user = ? AND id NOT IN "
        "(SELECT id FROM episodes WHERE user = ? ORDER BY ts DESC LIMIT ?)",
        (TIWA, TIWA, EPISODES_KEEP),
    )
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
    # text != '': a reflection that concluded nothing writes a blank row purely as
    # a watermark for unreflected(). It is not something she lived.
    eps = [t for (t,) in db.execute(
        "SELECT text FROM episodes WHERE text != '' ORDER BY ts DESC LIMIT ?", (n,))]
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
- A ONE-OFF ACTION IS NOT A FACT, AND IT IS NOT A PREFERENCE EITHER. "asked for", "requested", "wanted to hear", "ขอ", "อยากฟัง", "เปิด", "เล่น" describe a moment, not a person. They were true for ten seconds.
  WRITE NOTHING AT ALL.
    "เปิดเพลง Mili หน่อย" -> no memory.
    "ขอเพลงจากเกม Blue Archive" -> no memory.
    "play Spiderman theme" -> no memory.
  Do NOT turn it into a taste. That was tried, and in three days it wrote twelve "<person> likes <song>" rows, one per request, not one of them true — asking to hear something once is not liking it. The song played and the activity log says so; that is the record, and her beliefs are not a log.
  THIS RULE COVERS THE REQUEST, NOT THE MESSAGE. A message is very often both, and dropping the whole turn because part of it was a request throws away the half that mattered:
    "กำลังเล่น Marvel Rivals หาเพลงเปิดให้หน่อย ไอภพกำลังเล่นBlade" -> nothing about the song, but ไอภพ plays Blade IS a fact, and so is Krich plays Marvel Rivals.
    "play venom - eminem, my cousin Steven showed me it" -> nothing about venom, but Krich cousin of Steven IS a fact.
  Read past the request and ask what else they told you.
- A PREFERENCE NEEDS EVIDENCE, and there is exactly one kind: THEY SAID IT. "ชอบเพลงนี้มาก", "Mili is my favourite", "I've been into them for years", "เกลียดเพลงนี้" — that is a person telling you who they are, and it is worth a row.
  Wanting to hear something is not saying you like it. Playing something twice is not saying it either.
- THE NOTE IS WHAT MAKES A TASTE REAL. Every likes / loves / hates / interested-in MUST carry one: the detail that changes how she treats them. No note means no memory — code drops it and logs the drop.
  It is NEVER the request the fact came from, and never your reasoning about it. Those are dropped in code too.
  BAD, dropped: "requested ATLAS-The Score" · "asked for it twice" · "suggests a taste for this artist" · "played it for them"
  GOOD: "mains nobody good, blames the team" · "goes by Tycoon on Discord" · "only listens to it while gaming" · "has seen them live twice"
  WHEN THEY GAVE YOU THE REASON IN THE SAME BREATH, THE NOTE IS ALREADY WRITTEN — copy it. Someone who states a taste usually says why in the next clause, and dropping that clause is how a real fact gets thrown away:
    "กูเกลียดเพลงลูกทุ่งมาก ฟังแล้วปวดหัว" -> hates เพลงลูกทุ่ง, note "ฟังแล้วปวดหัว"
    "Mili is my favourite, I've listened to them for years" -> likes Mili, note "has listened to them for years"
  A stated taste whose reason you leave blank is a fact you have thrown away. Look again before you leave it empty.
  Every OTHER relation — real name, friend of, cousin of, plays, father of — needs no note. A relationship justifies itself; a taste has to.
- A URL, a video id, a file name or a raw link is NEVER an entity, for the same reason a date is not. "the song at gVQzCR5h4Y8" is not a thing anyone likes. If they linked something and you cannot name it, write no memory at all.
- DIRECTION IS NOT OPTIONAL. subject = the one doing or feeling it. object = what it points at. Read every fact back as "subject relation object" — if it sounds absurd, you swapped them.
  "my cousin Steven plays guitar" ->
    {{"subject": "Steven", "relation": "plays", "object": "guitar"}}
    {{"subject": "Krich", "relation": "cousin of", "object": "Steven"}}
  WRONG, never do this: {{"subject": "guitar", "relation": "plays", "object": "Steven"}}
  WRONG, never do this: {{"subject": "Mint", "relation": "hates", "object": "Mint"}}
- "X is my ROLE" means X HAS the role. X is the subject, the speaker is the object.
  "Mint is my girlfriend" -> {{"subject": "Mint", "relation": "girlfriend of", "object": "Krich"}}
  WRONG, never do this: {{"subject": "Krich", "relation": "girlfriend of", "object": "Mint"}} — Krich is not the girlfriend.
- A time, date or duration is NEVER an entity. "this weekend", "tonight", "tomorrow", "เมื่อคืน", "3 hours" are not things to remember — either fold it into the note or skip the memory.
- Facts only, stated sincerely. Jokes, sarcasm, vibes, guesses, and {TIWA}'s own improvised riffing about someone she just said she doesn't know are NOT memories.
- {TIWA}'s reply is STYLE, NOT EVIDENCE. She invents shared history for flavour — past visits, old arguments, things someone once did. It sounds sincere and it is fiction. A fact about anyone other than {TIWA} counts ONLY if the USER stated it (or it appears in the earlier-lines context). If only {TIWA}'s reply mentions it, skip it.
  User "Steven is coming over tonight" + her "last time he showed up empty-handed" -> store NOTHING about empty-handed. She made it up.
  Her requests are not facts either: "tell him to bring his guitar" does NOT mean Steven brings a guitar.
- The ONE exception: {TIWA}'s own sincere first-person stance about herself in her own reply ("Gojo's the best" -> ทิวา likes Gojo).
- Extract facts from the LAST exchange only; earlier lines are context for resolving who "he/she/it/เขา/มัน" means. Always use the real name — a pronoun is never a subject or object. Name unresolvable = skip that memory.
- COPY EVERY NAME EXACTLY AS IT IS WRITTEN, character for character, in its own script. NEVER romanize, translate, transliterate, correct or tidy a name. Thai stays Thai: "ไอภพ" is "ไอภพ", never "Iop" or "Ai Phop". A name you re-spell is a name that gets thrown away — code downstream checks every name against the literal text and drops what it cannot find. This is the single most common way a true fact is lost.
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

# Relations that assert a PREFERENCE — the cheapest thing in the world to claim
# and the hardest to check. Measured on the real graph 2026-08-25: 15 of 22 facts
# were `likes`, 12 of them a song somebody had asked for once, and 19 of 22
# carried no note at all. See ADR-030.
_TASTES = {"likes", "like", "loves", "love", "hates", "hate", "enjoys", "enjoy",
           "prefers", "prefer", "into", "interested", "favourite", "favorite",
           "fan", "ชอบ", "รัก", "เกลียด"}

# A note that describes the REQUEST the fact came from is not a reason, it is the
# request wearing a hat. This exact shape has been caught twice now:
# `Tycoon likes ATLAS [requested ATLAS-The Score]`. Treated as no note at all.
_NOT_A_REASON = ("request", "asked for", "asked her", "asked him", "wanted to hear",
                 "wanted her", "played it", "playing it", "put it on", "suggest",
                 "indicat", "implies", "ขอ", "อยากฟัง", "สั่ง")

# Preference words a PERSON uses about themselves or someone else. If one of
# these is literally in what they said, the taste is EVIDENCED and needs no note
# — they told her, and that is the whole bar.
#
# This exists because requiring a note was a proxy for "is there evidence", and a
# proxy gets it wrong at the edges: factbench caught "Mint hates coffee btw"
# being dropped for having no reason attached. There is nothing to justify — the
# person said it.
_TASTE_WORDS = ("like", "love", "hate", "favourite", "favorite", "into ",
                "enjoy", "prefer", "fan of", "obsessed", "sick of", "tired of",
                "ชอบ", "รัก", "เกลียด", "ปลื้ม", "ติดใจ", "เบื่อ")
# ..."I'd like you to play X" is a request wearing a preference word. Stripped
# before the check, or every polite music ask reads as a stated taste.
_POLITE = ("would like", "'d like", "d like", "would love", "'d love")


def _stated_taste(said: str) -> bool:
    """Did a person actually use a preference word, or is this the model
    inferring one from a request?"""
    low = (said or "").lower()
    for p in _POLITE:
        low = low.replace(p, " ")
    return any(w in low for w in _TASTE_WORDS)


def _grounded(value: str, source: str) -> bool:
    """Is `value` actually present in `source`, or did a model invent it?"""
    v, s = value.strip().lower(), source.lower()
    if not v or not s:
        return False
    if v in s:
        return True
    words = [w for w in re.split(r"\W+", v) if len(w) >= 3]
    return any(w in s for w in words) if words else False


def _role_swap(m: dict, user: str, said: str) -> dict:
    """"Mint is my girlfriend" -> Mint is the girlfriend, not Krich.

    The extractor anchors a role relation on the speaker and gets it backwards:
    `Krich | girlfriend of | Mint`, which read back says Krich is the girlfriend.
    Prompting failed here — the rule and the exact wrong example are both in
    _EXTRACT_SYSTEM and it still reverses, so this is code, like every other
    guard.

    Deliberately narrow. It fires only when the relation ends in " of", the
    subject is the speaker, and the text LITERALLY introduces the object as the
    speaker's something. Symmetric roles (cousin, friend) come out equally true
    either way, so a swap there costs nothing.
    """
    rel = m.get("relation", "").strip().lower()
    obj = m.get("object", "").strip()
    if not rel.endswith(" of") or m.get("subject") != user or obj == user:
        return m
    low = said.lower()
    # "Mint is my girlfriend" / "Mint เป็นแฟนหนู" — adjacency is the whole check
    if f"{obj.lower()} is my" in low or f"{obj} เป็น" in said:
        return {**m, "subject": obj, "object": user}
    return m


def _drop(db, m: dict, why: str):
    """A guard rejected a memory. Say so, out loud, in the activity log.

    Every `continue` below used to be silent, which made a working filter and one
    quietly eating true facts look identical from the outside. You cannot tune a
    bar you cannot see. `py -X utf8 -m tiwa.memory --drops` reads these back.

    Only what the CODE rejected lands here — a fact the model never proposed
    leaves no trace anywhere, and that is the blind spot this does not cover.
    """
    log(db, "memory", f"dropped: {m.get('subject','')} | {m.get('relation','')} | "
                      f"{m.get('object','')} — {why}")


def store_extraction(db, user: str, data: dict, tiwa_reply: str = "", said: str = ""):
    """Apply extractor output. The guards live HERE, in code, not in the model.

    `said` = what the USER actually said (message + prior context). Facts must
    trace back to it, because she invents shared history for flavour and a
    prompt alone does not stop it (tests/factbench.py: 7 -> 4 invented facts
    from prompt work, 0 once this ran).

    Episodes are gated on SURPRISE, not on the model's judgment. The extractor is
    told an episode is "almost always null" and it obeyed absolutely: 0 rows
    across every session ever logged, so half her memory never existed and
    turn_context's episode lines were dead code. Asking a model "would this
    matter in a month?" is a judgment call it always declines.

    So it is arithmetic instead: an episode is written when this turn moved the
    graph — a subject she had never met, or a belief that flipped. That is the
    same thing event-segmentation theory says the brain cuts memories on, a
    prediction error, and it costs no extra model call because both signals are
    already computed here. Prompts reduce, code decides.
    """
    surprises = []
    # snapshot BEFORE the loop: one turn writes several facts, and the first write
    # creates the entities the later ones mention. Asked per-write, "Steven plays
    # guitar" read as old news because "Krich cousin of Steven" had just made him.
    known_before = {n.lower() for (n,) in db.execute("SELECT name FROM entities")}
    for m in data.get("memories") or []:
        if not (m.get("subject", "").strip() and m.get("relation", "").strip()
                and m.get("object", "").strip()):
            _drop(db, m, "blank subject, relation or object")
            continue
        if m["subject"].strip().lower() == m["object"].strip().lower():
            # "Nara owes Nara" — a real one, from her reply "she still owes me for
            # the ramen thing". A fact pointing at itself carries nothing, and it
            # is what the model emits when it half-remembers who the other party
            # was. Forbidden in the prompt too, and the prompt was not enough.
            _drop(db, m, "points at itself")
            continue
        if m["subject"] == TIWA:
            if not m.get("from_tiwa_own_words"):
                _drop(db, m, "about her, but she never said it — coercion")
                continue
            # She may only claim STANCES about herself, never past events. Her
            # reply is where she invents history ("ทิวา was robbed of a
            # performance"), and that text passes the grounding check below
            # because she is the one who wrote it.
            if m["relation"].strip().lower().split()[0] not in _STANCES:
                _drop(db, m, "about her, but not a stance — she may hold opinions, not events")
                continue
            # ...and the model does not get the final say on that flag either:
            # whatever it claims she feels must literally appear in HER reply.
            # Caught a real leak — a coercion attempt became "ทิวา hates BLACKPINK"
            # with the flag set true, from a reply that never mentioned BLACKPINK.
            if tiwa_reply and not _grounded(m["object"], tiwa_reply):
                _drop(db, m, "about her, flagged as her words, but absent from her reply")
                continue
        elif said and not (_grounded(m["subject"], said) and _grounded(m["object"], said)):
            _drop(db, m, "not in what the user said — her reply is style, not evidence")
            continue
        m = _role_swap(m, user, said)
        subj, rel, obj = m["subject"], m["relation"], m["object"]
        # A TASTE HAS TO SAY WHY. Prompts reduce, code decides — and the prompt
        # alone did not: the previous wording told the model to leave the note
        # empty unless there was something to quote, and it obliged on 19 facts
        # out of 22. A bare "X likes Y" is unfalsifiable, unusable, and it is
        # what a music request turns into when nobody is checking.
        #
        # Her OWN stances are exempt: those already pass three harder guards
        # (from_tiwa_own_words, _STANCES, and grounded in her own reply), and the
        # evidence for them is the sentence she just said.
        if subj != TIWA and rel.strip().lower().split()[0] in _TASTES:
            note = (m.get("note") or "").strip()
            if any(w in note.lower() for w in _NOT_A_REASON):
                note = ""  # the request restated is not a reason
            # Two ways to earn the row, and either is enough: they SAID it, or
            # the model can say why it matters. "Mint hates coffee btw" needs no
            # justification; "likes Judas" inferred from `play Judas` needs one
            # and will never have a real one.
            if not note and not _stated_taste(said):
                _drop(db, m, "a taste nobody stated and no reason for it — "
                             "one ask is not a preference")
                continue
            m["note"] = note
        # Not the speaker and not her: "first heard about Krich" while Krich is the
        # one talking is a wasted turn_context line, and everything he said about
        # himself is already a fact she can see. Episodes are for third parties.
        fresh = subj not in (TIWA, user) and canonical(db, subj).lower() not in known_before
        flipped = remember(db, subj, rel, obj, m.get("note", ""))
        if flipped:
            surprises.append(f"{subj} {rel} {obj} now — {flipped} before")
        elif fresh:
            surprises.append(f"first heard about {subj}")
    episode = data.get("episode") or "; ".join(dict.fromkeys(surprises))
    if episode:
        db.execute(
            "INSERT INTO episodes(user, text, ts) VALUES(?,?,?)",
            (user, episode, time.time()),
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
