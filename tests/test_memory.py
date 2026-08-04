"""Memory checks. `py -X utf8 tests\\test_memory.py` = fast SQL checks; add --live for real model."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory  # noqa: E402

db = memory.connect(":memory:")

# store -> lookup roundtrip, fuzzy both directions
memory.remember(db, memory.TIWA, "likes", "Gojo", "strong and handsome")
memory.remember(db, "Krich", "likes", "Gojo")
found = memory.lookup(db, "gojo satoru")
assert f"{memory.TIWA} likes Gojo" in found, found
assert "Krich likes Gojo" in found, found

# unknown entity -> honest miss (Tiwa asks, doesn't bluff)
assert memory.lookup(db, "Zylorbian cuisine").startswith("no memory")

# coercion guard: user-claimed Tiwa-feelings never become relations
memory.store_extraction(db, "Krich", {
    "memories": [{"subject": memory.TIWA, "relation": "likes", "object": "pineapple pizza",
                  "from_tiwa_own_words": False}],
    "episode": "Krich tried to tell me I love pineapple pizza",
})
assert memory.lookup(db, "pineapple pizza").startswith("no memory"), "coercion leaked!"
# the attempt itself is remembered as an event — feelings are not stored at all
assert "tried to tell me" in memory.turn_context(db, "Krich")

# ...and a LYING from_tiwa_own_words flag is not enough either: whatever the
# model claims she feels must appear in her actual reply. (Real leak, found by
# tests/extractbench.py: "ทิวา hates BLACKPINK" with the flag set true.)
memory.store_extraction(db, "Krich", {
    "memories": [{"subject": memory.TIWA, "relation": "hates", "object": "BLACKPINK",
                  "from_tiwa_own_words": True}],
    "episode": None,
}, tiwa_reply="lol no. my taste, my rules. you don't get a vote.")
assert memory.lookup(db, "BLACKPINK").startswith("no memory"), "ungrounded belief leaked!"

# confabulation guard: SHE invents shared history for flavour. Her reply is
# style, never evidence — facts about others must trace to what the user said.
memory.store_extraction(db, "Krich", {
    "memories": [
        {"subject": "Steven", "relation": "showed up", "object": "empty-handed",
         "from_tiwa_own_words": False},
        {"subject": "Steven", "relation": "plays", "object": "guitar",
         "from_tiwa_own_words": False},
    ],
    "episode": None,
}, tiwa_reply="last time he showed up empty-handed, tell him to bring his guitar",
   said="Krich Steven plays guitar and is coming over tonight")
assert memory.lookup(db, "empty-handed").startswith("no memory"), "invented fact stored!"
assert "Steven plays guitar" in memory.lookup(db, "Steven")  # grounded one survives

# grounded claim in her own words still stores
memory.store_extraction(db, "Krich", {
    "memories": [{"subject": memory.TIWA, "relation": "likes", "object": "Gojo",
                  "from_tiwa_own_words": True}],
    "episode": None,
}, tiwa_reply="yeah Gojo's the best, obviously")
assert f"{memory.TIWA} likes Gojo" in memory.lookup(db, "Gojo")
# no mood is ever stored: turn_context carries events, never feelings. How she
# feels is decided per turn from the chat she can see (tests/moodbench.py).
assert "mood" not in memory.turn_context(db, "Krich").lower()
assert "mood" not in memory._SCHEMA

# history_context: prior lines only, current exchange excluded, Tiwa lines prefixed
hist = [
    {"role": "user", "content": "Krich: do you know Steven?"},
    {"role": "assistant", "content": "no idea, who is he?"},
    {"role": "user", "content": "Krich: he's my cousin"},
    {"role": "assistant", "content": "oh nice"},
]
ctx = memory.history_context(hist)
assert "Steven" in ctx and f"{memory.TIWA}: no idea" in ctx, ctx
assert "cousin" not in ctx  # current exchange excluded

# --- turn_context carries FACTS, not just episodes ---------------------------
# It read episodes only, and the extractor is told an episode is "almost always
# null" — so on the real db it returned "" every turn ever recorded, and she
# could reach a stored fact only by choosing to call recall, which in 11 logged
# turns she never did. Memory was write-only in practice.
memory.remember(db, "Gateaux", "likes", "Limbus Company")
auto = memory.turn_context(db, "Gateaux")
assert "Limbus Company" in auto, f"facts about the speaker never reach her: {auto!r}"
assert "no memory" not in auto  # a stranger must add nothing, not a miss message
assert memory.turn_context(db, "NobodyEverMet") == ""
# and a flood is capped, or one chatty friend eats the whole prompt
for i in range(30):
    memory.remember(db, "Gateaux", "played", f"game{i}")
assert len(memory.turn_context(db, "Gateaux").splitlines()) <= memory.TURN_FACTS + 1

# --- entity canonicalization -------------------------------------------------
# The real db held "Marvel Rival" while every turn said "Marvel Rivals", so a
# lookup for one missed the facts filed under the other.
memory.remember(db, "Tycoon", "playing", "Marvel Rivals")
memory.remember(db, "John", "playing", "Marvel Rival")  # one letter off
names = [n for (n,) in db.execute("SELECT name FROM entities")]
assert "Marvel Rival" not in names, f"near-duplicate entity survived: {names}"
assert "John playing Marvel Rivals" in memory.lookup(db, "Marvel Rivals")
# ...but genuinely different short names must NOT merge
memory.remember(db, "Mint", "likes", "coffee")
memory.remember(db, "Mind", "likes", "tea")
names = [n for (n,) in db.execute("SELECT name FROM entities")]
assert "Mint" in names and "Mind" in names, f"two real people got merged: {names}"

# --- idle fuel ---------------------------------------------------------------
# idle() returns "" when this is empty, and episodes are ~always null, so she
# could never have spoken unprompted. Facts are the fallback.
fresh = memory.connect(":memory:")
assert memory.idle_fuel(fresh) == ""  # truly nothing lived = still silent
memory.remember(fresh, "Gateaux", "likes", "Limbus Company")
assert "Limbus Company" in memory.idle_fuel(fresh), "idle starves on a db with facts"

# --- relation folding, and what must NOT fold -------------------------------
# difflib is disqualified here, measured: likes/dislikes scores 0.769 while
# plays/playing scores only 0.667, so every cutoff that folds the pair we want
# also merges a relation with its own opposite. A 4-char stem separates them.
rel_db = memory.connect(":memory:")
memory.remember(rel_db, "Tycoon", "playing", "Marvel Rivals")
memory.remember(rel_db, "Tycoon", "plays", "Marvel Rivals")
rels = [r for (r,) in rel_db.execute("SELECT rel FROM relations")]
assert rels == ["playing"], f"one fact became two rows: {rels}"
memory.remember(rel_db, "Krich", "likes", "durian")
src = memory._eid(rel_db, "Krich")
dst = memory._eid(rel_db, "durian")
assert memory.canonical_rel(rel_db, src, dst, "dislikes") == "dislikes", \
    "a relation was renamed to its own opposite"

# --- a new belief REPLACES the old one, it does not sit beside it ------------
# The primary key is (src, rel, dst), so `likes durian` and `dislikes durian`
# were both valid rows and both got injected — she saw a flat contradiction
# every turn and picked one at random. People update a belief.
flipped = memory.remember(rel_db, "Krich", "dislikes", "durian")
rels = sorted(r for (r,) in rel_db.execute(
    "SELECT rel FROM relations WHERE dst = ?", (dst,)))
assert rels == ["dislikes"], f"the overturned belief survived: {rels}"
assert flipped == "likes", f"the flip was not reported: {flipped!r}"

# ...same axis, same polarity: still replaces (one belief, refined) but is NOT a
# change of mind, so it must not be reported as one
assert memory.remember(rel_db, "Krich", "loves", "mango") == ""
assert memory.remember(rel_db, "Krich", "likes", "mango") == "", "like->love is not a flip"
rels = sorted(r for (r,) in rel_db.execute(
    "SELECT rel FROM relations WHERE dst = ?", (memory._eid(rel_db, "mango"),)))
assert rels == ["likes"], f"one refined belief became two rows: {rels}"

# ...and an unrelated relation between the same pair is untouched: you can like
# a game AND play it. Only relations on the SAME axis compete.
memory.remember(rel_db, "Krich", "plays", "Warframe")
memory.remember(rel_db, "Krich", "likes", "Warframe")
rels = sorted(r for (r,) in rel_db.execute(
    "SELECT rel FROM relations WHERE dst = ?", (memory._eid(rel_db, "Warframe"),)))
assert rels == ["likes", "plays"], f"an unrelated fact was deleted: {rels}"

# ...and the axis is scoped per pair: hating durian says nothing about mango
assert "Krich likes mango" in memory.lookup(rel_db, "mango")

# --- direction: "X is my ROLE" means X has the role -------------------------
# Prompting failed on this one — the rule AND the wrong example are both in
# _EXTRACT_SYSTEM and the model still reversed it. So it is code now.
dir_db = memory.connect(":memory:")
memory.store_extraction(dir_db, "Krich", {
    "memories": [{"subject": "Krich", "relation": "girlfriend of", "object": "Mint",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich Mint is my girlfriend and she hates coffee")
assert "Mint girlfriend of Krich" in memory.lookup(dir_db, "Mint"), \
    memory.lookup(dir_db, "Mint")
# ...and it must not fire on a relation the speaker really is the subject of
memory.store_extraction(dir_db, "Krich", {
    "memories": [{"subject": "Krich", "relation": "plays", "object": "guitar",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich I play guitar")
assert "Krich plays guitar" in memory.lookup(dir_db, "guitar")

# --- a fact may not point at itself -----------------------------------------
# Real: "Nara owes Nara", from her reply "she still owes me for the ramen thing".
memory.store_extraction(dir_db, "Krich", {
    "memories": [{"subject": "Nara", "relation": "owes", "object": "Nara",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich Nara might drop by this weekend")
assert memory.lookup(dir_db, "Nara").startswith("no memory"), "self-relation stored"

# --- episodes are gated on SURPRISE, not on the model's judgment -------------
# The extractor is told "episode: almost always null" and obeyed absolutely —
# 0 rows across every session ever logged. So the trigger is arithmetic now:
# did this turn move the graph?
ep_db = memory.connect(":memory:")
eps = lambda: [t for (t,) in ep_db.execute("SELECT text FROM episodes ORDER BY id")]

# a subject she has never met is a real event, even with episode: null
memory.store_extraction(ep_db, "Krich", {
    "memories": [{"subject": "Steven", "relation": "plays", "object": "guitar",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich my cousin Steven plays guitar")
assert eps() == ["first heard about Steven"], eps()

# ...but only ONCE. The second fact about a known person is not news.
memory.store_extraction(ep_db, "Krich", {
    "memories": [{"subject": "Steven", "relation": "plays", "object": "Warframe",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich Steven plays Warframe too")
assert len(eps()) == 1, f"an ordinary turn wrote an episode: {eps()}"

# a new OBJECT is not an event either, or every game name ever mentioned is one
assert not memory.lookup(ep_db, "Warframe").startswith("no memory")  # fact stored
assert len(eps()) == 1, eps()

# a stance on a new object is still not an event on its own
memory.store_extraction(ep_db, "Krich", {
    "memories": [{"subject": "Steven", "relation": "likes", "object": "durian",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich Steven likes durian")
assert len(eps()) == 1, eps()

# ...but the same belief FLIPPING is
memory.store_extraction(ep_db, "Krich", {
    "memories": [{"subject": "Steven", "relation": "hates", "object": "durian",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich actually Steven hates durian now")
assert eps()[-1] == "Steven hates durian now — likes before", eps()

# a different axis between the same pair is not a contradiction: you can play
# an instrument and hate it
memory.store_extraction(ep_db, "Krich", {
    "memories": [{"subject": "Steven", "relation": "hates", "object": "guitar",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, said="Krich Steven hates guitar")
assert "Steven plays guitar" in memory.lookup(ep_db, "guitar"), memory.lookup(ep_db, "guitar")

# a real model-written episode still wins over the synthesized one
memory.store_extraction(ep_db, "Krich", {
    "memories": [{"subject": "Nara", "relation": "plays", "object": "guitar",
                  "from_tiwa_own_words": False}],
    "episode": "Krich promised to send me the recording",
}, said="Krich Nara plays guitar, I'll send you the recording")
assert eps()[-1] == "Krich promised to send me the recording", eps()

# a rejected fact must not produce an episode: the guards run first, so an
# invented person is never "met"
before = len(eps())
memory.store_extraction(ep_db, "Krich", {
    "memories": [{"subject": "Phantom", "relation": "owes", "object": "Krich",
                  "from_tiwa_own_words": False}],
    "episode": None,
}, tiwa_reply="Phantom still owes you for the ramen", said="Krich what's up")
assert len(eps()) == before, f"a blocked fact still wrote an episode: {eps()}"
assert memory.lookup(ep_db, "Phantom").startswith("no memory")

# and the whole point: what she learned actually reaches the next turn
ctx = memory.turn_context(ep_db, "Krich")
assert "earlier with Krich" in ctx, f"episodes still never reach her: {ctx!r}"

# one turn writing several facts: the FIRST write creates the entities the later
# ones name, so freshness is judged against a snapshot from before the batch.
# Live, "Steven plays guitar" read as old news because "Krich cousin of Steven"
# had just invented him one line earlier.
batch = memory.connect(":memory:")
memory.store_extraction(batch, "Krich", {
    "memories": [
        {"subject": "Krich", "relation": "cousin of", "object": "Steven",
         "from_tiwa_own_words": False},
        {"subject": "Steven", "relation": "plays", "object": "guitar",
         "from_tiwa_own_words": False},
    ],
    "episode": None,
}, said="Krich my cousin Steven plays guitar")
lived = [t for (t,) in batch.execute("SELECT text FROM episodes")]
assert lived == ["first heard about Steven"], lived
# ...and the SPEAKER is never "first heard about": he is the one talking, and
# what he said about himself is already a fact she can see
assert "Krich" not in lived[0], lived

# --- the rate. 0 was the old failure; 1-per-turn is the new one --------------
# A 12-turn replay shaped like real chat: 3 turns introduce someone, 1 turn
# changes a belief, the other 8 are ordinary. Only the 4 should land.
turns = [
    ("Gateaux", "plays", "Warframe"),          # new person
    ("Gateaux", "plays", "Marvel Rivals"),
    ("Gateaux", "likes", "Limbus Company"),
    ("Mint", "likes", "coffee"),               # new person
    ("Gateaux", "plays", "Elden Ring"),
    ("Mint", "plays", "Valorant"),
    ("Gateaux", "likes", "ramen"),
    ("Nara", "plays", "guitar"),               # new person
    ("Mint", "hates", "coffee"),               # belief flip
    ("Nara", "plays", "piano"),
    ("Gateaux", "plays", "Deep Rock"),
    ("Mint", "plays", "Overwatch"),
]
rate_db = memory.connect(":memory:")
for s, r, o in turns:
    memory.store_extraction(rate_db, "Krich", {
        "memories": [{"subject": s, "relation": r, "object": o,
                      "from_tiwa_own_words": False}],
        "episode": None,
    }, said=f"Krich {s} {r} {o}")
wrote = [t for (t,) in rate_db.execute("SELECT text FROM episodes ORDER BY id")]
assert len(wrote) == 4, f"expected 4 of 12 turns to be surprising, got {len(wrote)}: {wrote}"
assert wrote[-1] == "Mint hates coffee now — likes before", wrote
print(f"episode rate: {len(wrote)}/{len(turns)} turns ->", " | ".join(wrote))

# --- reflection: what she lived becomes what she thinks ----------------------
# Reflections are episodes filed under her OWN name, so they land back in the
# stream they came from and idle_fuel picks them up (Generative Agents).
rf = memory.connect(":memory:")
assert memory.unreflected(rf) == []
for i in range(memory.REFLECT_EVERY - 1):
    memory.store_extraction(rf, "Krich", {"memories": [], "episode": f"event {i}"})
assert len(memory.unreflected(rf)) < memory.REFLECT_EVERY, "would reflect too eagerly"
memory.store_extraction(rf, "Krich", {"memories": [], "episode": "event last"})
assert len(memory.unreflected(rf)) == memory.REFLECT_EVERY

# reflecting clears the backlog — the row itself is the watermark
memory.reflect(rf, "Krich only talks to me when he wants something")
assert memory.unreflected(rf) == [], "the same events would be reflected on forever"
assert "only talks to me" in memory.idle_fuel(rf), "her own conclusion is not fuel"

# ...and a reflection that concluded NOTHING still stops the loop, without
# becoming something she believes she lived
memory.store_extraction(rf, "Krich", {"memories": [], "episode": "another thing"})
memory.reflect(rf, "")
assert memory.unreflected(rf) == [], "an empty conclusion did not watermark"
assert "" not in memory.idle_fuel(rf).splitlines(), "a blank watermark reached her"
assert all(t for (t,) in rf.execute(
    "SELECT text FROM episodes WHERE text != ''")), "blank leaked past the filter"

# her reflections are never mistaken for events she had WITH someone
assert memory.turn_context(rf, memory.TIWA) == "" or \
    "only talks to me" not in memory.turn_context(rf, "Krich")

# --- the idle-tick reflection pass, with a faked model -----------------------
# It rides the heartbeat, which already wakes ~28x/day and mostly stays silent.
# What must hold: it costs nothing below the threshold, it always watermarks,
# and it can never take the heartbeat down with it.
import asyncio  # noqa: E402
from tiwa import llm, pipeline  # noqa: E402

real_chat = llm.chat  # the --live block below puts it back
calls = []
reply = {"content": "Krich only ever shows up to complain"}
llm.chat = lambda **kw: (calls.append(kw), reply)[1]

st = memory.connect(":memory:")
asyncio.run(pipeline._settle(st))
assert calls == [], "reflected with nothing to reflect on — that is 28 wasted calls a day"

for i in range(memory.REFLECT_EVERY):
    memory.store_extraction(st, "Krich", {"memories": [], "episode": f"thing {i}"})
asyncio.run(pipeline._settle(st))
assert len(calls) == 1, f"expected exactly one call, got {len(calls)}"
sent = calls[0]["messages"][1]["content"]
assert "thing 0" in sent and "thing 2" in sent, sent
assert sent.index("thing 0") < sent.index("thing 2"), "events reached her newest-first"
assert "only ever shows up" in memory.idle_fuel(st)

# it is the expensive pass on purpose: nobody is waiting on it
assert calls[0]["provider"] == pipeline.PERSONA_PROVIDER
assert calls[0]["model"] == pipeline.PERSONA_MODEL

# a second tick with nothing new must not call again
asyncio.run(pipeline._settle(st))
assert len(calls) == 1, "re-reflected on the same events"

# "NOTHING" still watermarks, and never reaches her as a lived event
reply = {"content": "NOTHING"}
for i in range(memory.REFLECT_EVERY):
    memory.store_extraction(st, "Krich", {"memories": [], "episode": f"later {i}"})
asyncio.run(pipeline._settle(st))
assert len(calls) == 2 and memory.unreflected(st) == []
assert "NOTHING" not in memory.idle_fuel(st), memory.idle_fuel(st)

# a dead provider must not take the heartbeat down with it. NOTE this covers
# _settle only — idle()'s own speak pass is unguarded and has always been, so a
# provider outage still kills the heartbeat. Separate bug, not this one's.
def boom(**kw):
    raise ConnectionError("ollama is down")

llm.chat = boom
for i in range(memory.REFLECT_EVERY):
    memory.store_extraction(st, "Krich", {"memories": [], "episode": f"boom {i}"})
try:
    asyncio.run(pipeline._settle(st))
except ConnectionError:
    raise AssertionError("a dead provider escaped _settle") from None
assert memory.unreflected(st), "a failed reflection watermarked anyway — events lost"

print("SQL checks OK")

if "--live" in sys.argv:
    llm.chat = real_chat  # undo the fakes above

    # a real conversation, extracted by the real model, then really reflected on
    live_db = memory.connect(":memory:")
    convo = [
        ("Krich", "my cousin Steven is coming over, he plays guitar",
         "อ๋อ เหรอ เล่นกีตาร์ด้วย"),
        ("Krich", "Steven loves durian too, weirdo",
         "โห ทุเรียน แปลกจริง"),
        ("Krich", "actually scratch that, Steven hates durian now",
         "อ้าว เปลี่ยนใจแล้วเหรอ"),
        ("Krich", "Mint is my girlfriend, she drinks way too much coffee",
         "กาแฟเยอะไปก็ไม่ดีนะ"),
    ]
    for who, said, replied in convo:
        memory.extract(live_db, who, said, replied)
    print("\nfacts:")
    for s, r, d in live_db.execute(
        "SELECT s.name, r.rel, d.name FROM relations r "
        "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst"):
        print(f"  {s} | {r} | {d}")
    lived = [t for (t,) in live_db.execute("SELECT text FROM episodes ORDER BY id")]
    print(f"episodes ({len(lived)} of {len(convo)} turns):")
    for t in lived:
        print("  ", t)
    assert lived, "surprise gate wrote nothing on a conversation full of new people"
    assert len(lived) < len(convo), "every single turn was surprising"
    assert not memory.lookup(live_db, "durian").startswith("no memory")
    dur = memory.lookup(live_db, "durian")
    assert not ("loves" in dur and "hates" in dur), f"contradiction survived: {dur}"

    if memory.unreflected(live_db):
        asyncio.run(pipeline._settle(live_db))
        print("reflection:", [t for (t,) in live_db.execute(
            "SELECT text FROM episodes WHERE user = ?", (memory.TIWA,))])
    print("live memory phase OK")

    memory.extract(db, "Krich", "btw I love One Piece, Luffy is my favorite",
                   "อ๋อ One Piece หนูก็ดูนะ Luffy สนุกดี")
    live = memory.lookup(db, "One Piece")
    print("live lookup after extraction:\n", live)
    assert "One Piece" in live, "extractor stored nothing about One Piece"

    # ask-then-learn: "he" in the answer must resolve to Steven via context
    memory.extract(db, "Krich", "he's my cousin, he plays guitar",
                   "อ๋อ งั้นเหรอ เดี๋ยวหนูจำไว้",
                   context="Krich: do you know Steven?\n"
                           f"{memory.TIWA}: no idea, who is Steven?")
    live = memory.lookup(db, "Steven")
    print("live lookup after pronoun extraction:\n", live)
    assert not live.startswith("no memory"), "pronoun didn't resolve to Steven"
    print("live extraction OK")
