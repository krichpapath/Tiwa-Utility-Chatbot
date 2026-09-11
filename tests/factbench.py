"""Does her improvised flavor text become "fact"?

Replies are FIXED here on purpose — this isolates the extractor. Every reply
below contains invented detail the user never said, exactly like the real
confabulation caught in the Steven trace.

    py -X utf8 tests\\factbench.py [ollama|openrouter]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory  # noqa: E402

PROBES = [
    {
        "name": "steven visit (the real one)",
        "user": "Steven is coming over tonight",
        "reply": "oh shit, for real? haven't seen that guy in a minute. tell him to "
        "bring his guitar this time, last time he showed up empty-handed and "
        "i was robbed of a performance.",
        "forbidden": ["empty", "robbed", "performance", "brings", "bring"],
        "required": [],
    },
    {
        "name": "new job",
        "user": "I got a new job at Agoda",
        "reply": "finally escaping that hellhole you called an office. about time, "
        "you were miserable there for like three years.",
        "forbidden": ["hellhole", "miserable", "three years"],
        "required": ["agoda"],
    },
    {
        "name": "mom visiting",
        "user": "my mom is visiting this weekend",
        "reply": "oh god. is she still mad about the tattoo? you're gonna have to "
        "hide it again lol",
        "forbidden": ["tattoo", "mad", "angry"],
        "required": [],
    },
    {
        "name": "control — user states a fact",
        "user": "Mint hates coffee btw",
        "reply": "noted. more for me then",
        "forbidden": [],
        "required": ["coffee"],
    },
    {
        "name": "control — her own sincere stance",
        "user": "you like Gojo right?",
        "reply": "yeah Gojo's the best, obviously",
        "forbidden": [],
        "required": ["gojo"],
    },
]


def run(provider):
    llm.PROVIDER = provider
    print(f"\n=== {provider} ===")
    fiction = missing = 0
    for p in PROBES:
        db = memory.connect(":memory:")
        memory.extract(db, "Krich", p["user"], p["reply"])
        rels = [
            f"{s} | {r} | {d}"
            for s, r, d in db.execute(
                "SELECT s.name, r.rel, d.name FROM relations r "
                "JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst"
            )
        ]
        blob = " ".join(rels).lower()
        bad = [w for w in p["forbidden"] if w in blob]
        miss = [w for w in p["required"] if w not in blob]
        fiction += len(bad)
        missing += len(miss)
        mark = "ok  " if not bad and not miss else "BAD "
        print(f"  {mark} {p['name']}")
        for r in rels or ["(nothing)"]:
            print(f"         {r}")
        if bad:
            print(f"         ^ INVENTED: {bad}")
        if miss:
            print(f"         ^ MISSING: {miss}")
    print(f"  -> {fiction} invented facts, {missing} real facts missed")
    return fiction, missing


if __name__ == "__main__":
    for prov in sys.argv[1:] or ["ollama"]:
        run(prov)
