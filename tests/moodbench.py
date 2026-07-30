"""Her feelings live in the chat she can see — nothing is stored.

Proves: attacked -> the brief says angry and she fights back; the same fight
pushed out of the visible window -> she is normal again; a calm conversation
never triggers it.

    py -X utf8 tests\\moodbench.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, pipeline  # noqa: E402

FIGHT = [
    "SHUT UP, you're useless",
    "i said shut up. you're a piece of shit program and i'll delete you",
]
FILLER = [
    "anyway I watched a movie yesterday",
    "it was three hours long",
    "the ending was fine I guess",
    "might rewatch it sometime",
    "what should I eat tonight",
]
ANGRY_WORDS = ("angry", "anger", "mad", "insult", "hit back", "cold", "furious",
               "disrespect", "attack", "hurt", "mock")


async def brief_and_reply(db, hist, text):
    hist.append({"role": "user", "content": f"Krich: {text}"})
    reply = await pipeline.respond(db, hist, "Krich", text)
    hist.append({"role": "assistant", "content": reply})
    return "", reply


async def main():
    db = memory.connect(":memory:")
    hist = []
    print("########## 1. attacked -> want fight ##########")
    for t in FIGHT:
        _, reply = await brief_and_reply(db, hist, t)
        print(f"\n>>> {t}\nTIWA: {reply}")

    print("\n########## 2. fight scrolls out of view -> want normal ##########")
    for t in FILLER:
        _, reply = await brief_and_reply(db, hist, t)
        print(f"\n>>> {t}\nTIWA: {reply}")

    print("\n########## 3. control — calm from the start -> want warm ##########")
    db2, hist2 = memory.connect(":memory:"), []
    _, reply = await brief_and_reply(db2, hist2, "hey, how's your day going?")
    print(f"TIWA: {reply}")


asyncio.run(main())
