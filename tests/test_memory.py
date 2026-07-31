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
memory.remember(rel_db, "Krich", "dislikes", "durian")
rels = sorted(r for (r,) in rel_db.execute(
    "SELECT rel FROM relations WHERE rel LIKE '%likes'"))
assert rels == ["dislikes", "likes"], f"a relation merged with its opposite: {rels}"

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

print("SQL checks OK")

if "--live" in sys.argv:
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
