"""Does she write down every question, or only what matters in a month?

Krich found her memory full of "X asked ทิวา what she likes to eat" — precise,
useless, and injected into every prompt. Most exchanges should store NOTHING.

    py -X utf8 tests\\episodebench.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory  # noqa: E402

# user line, her reply, should it leave an episode behind?
CASES = [
    ("hey what's your name again?", "ทิวา. same as last time.", False),
    ("what do you like to eat?", "ข้าวเหนียวหมูปิ้ง, obviously.", False),
    ("hi", "yo.", False),
    ("i'm Tycoon btw, nice to meet you", "cool. how do you know Krich?", False),
    ("where can i buy น้ำพริกกะปิ in bangkok?", "try any fresh market.", False),
    ("i'm flying to Japan in March for two weeks",
     "two weeks? bring me back something stupid.", True),
    ("i quit my job today", "damn. on purpose, or were you pushed?", True),
]


def main():
    kept = missed = 0
    print("| you said | episode written | want |")
    print("|---|---|---|")
    for text, reply, want in CASES:
        db = memory.connect(":memory:")
        memory.extract(db, "Krich", text, reply)
        eps = [t for (t,) in db.execute("SELECT text FROM episodes")]
        got = bool(eps)
        ok = got == want
        kept += got and not want
        missed += want and not got
        print(f"| {text[:44]} | {eps[0][:52] if eps else '—'} "
              f"| {'yes' if want else 'no'} |{'' if ok else '  **WRONG**'}")
    print(f"\nnoise stored: {kept} (want 0) · real events missed: {missed} (want 0)")

    # cap: one person cannot fill her head with diary entries
    db = memory.connect(":memory:")
    for i in range(memory.EPISODES_KEEP + 10):
        memory.store_extraction(db, "Krich", {"memories": [], "episode": f"thing {i}"})
    n = db.execute("SELECT COUNT(*) FROM episodes WHERE user='Krich'").fetchone()[0]
    print(f"episode cap: {n} kept (limit {memory.EPISODES_KEEP})")
    assert n == memory.EPISODES_KEEP

    # orphans: entities with nothing attached must not accumulate
    db = memory.connect(":memory:")
    memory.remember(db, "Krich", "likes", "coffee")
    rowid = db.execute("SELECT rowid FROM relations").fetchone()[0]
    memory.delete_relation(db, rowid)
    memory.prune_entities(db)
    db.commit()
    left = [n for (n,) in db.execute("SELECT name FROM entities")]
    print(f"after deleting the only fact, entities left: {left}")
    assert left == [memory.TIWA], left
    assert kept == 0 and missed == 0, "episode quality gate failed"
    print("\nepisodes ok — noise dropped, real events kept, growth bounded")


main()
