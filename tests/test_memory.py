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
