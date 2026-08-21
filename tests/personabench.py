"""Does she still sound like herself? Blind pairwise A/B, live.

Reads the pairs `latbench` wrote: same message, same memory, serial reply vs
concurrent reply. A judge picks which one is more ทิวา, against her own
`prompts/tiwa.md`.

WHY PAIRWISE AND NOT A 1-5 SCORE
--------------------------------
PersonaEval (arXiv 2508.10014) measured LLM judges at ~69% on role identification
where humans hit 90.8%. A judge that unreliable cannot produce a trustworthy
absolute score — but it can be better than chance at "which of these two". So the
question asked here is the only one it is qualified to answer.

EVERY PAIR IS JUDGED TWICE, SWAPPED
-----------------------------------
Position bias is the loudest confound in LLM judging. Each pair is shown A/B and
then B/A; a win counts only if the judge picks the same reply both times.
Disagreeing with itself is scored a tie, which is the honest reading.

What "pass" means here is NOT that concurrent wins. It is that concurrent does
not LOSE — the change was for latency and growth, and persona was meant to be
untouched. A dead heat is the result this is looking for.

    py -X utf8 tests\\personabench.py

Costs real tokens: two judge calls per pair.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, pipeline  # noqa: E402

AB = memory.DATA_DIR / "ab.json"

JUDGE = """You are judging which of two replies is more in character.

The character:
---
{persona}
---

Both replies answer the same message. Pick the ONE that better fits the character above — her voice, her register, her length, her refusal to bluff or grovel. Ignore which is more helpful, more polite or more correct; this is about whether it sounds like her.

Answer in json: {{"pick": "A" or "B", "why": "at most 12 words"}}"""

FMT = {
    "type": "object",
    "properties": {"pick": {"type": "string", "enum": ["A", "B"]},
                   "why": {"type": "string"}},
    "required": ["pick", "why"],
    "additionalProperties": False,
}


async def ask(message: str, a: str, b: str) -> tuple:
    resp = await asyncio.to_thread(
        llm.chat,
        model=llm.PERSONA_API_MODEL,
        messages=[
            {"role": "system", "content": JUDGE.format(persona=pipeline.PERSONA)},
            {"role": "user", "content": f"Message: {message}\n\nA: {a}\n\nB: {b}"},
        ],
        options={"temperature": 0, "num_ctx": 4096},
        fmt=FMT,
        provider="openrouter",
    )
    try:
        d = json.loads(resp["content"] or "{}")
        return d.get("pick", ""), d.get("why", "")
    except json.JSONDecodeError:
        return "", ""


async def main():
    if not AB.exists():
        raise SystemExit(f"no {AB} — run tests/latbench.py first")
    pairs = json.loads(AB.read_text(encoding="utf-8"))
    pairs = [p for p in pairs if p["serial"].strip() and p["concurrent"].strip()]
    print(f"{len(pairs)} pairs, each judged twice with the sides swapped\n")

    tally = {"serial": 0, "concurrent": 0, "tie": 0}
    print(f"{'#':>2} | {'message':30} | verdict     | why")
    print(f"{'-'*2}-+-{'-'*30}-+-------------+{'-'*34}")
    for i, p in enumerate(pairs, 1):
        # order 1: serial is A. order 2: serial is B.
        p1, w1 = await ask(p["text"], p["serial"], p["concurrent"])
        p2, w2 = await ask(p["text"], p["concurrent"], p["serial"])
        first = {"A": "serial", "B": "concurrent"}.get(p1)
        second = {"A": "concurrent", "B": "serial"}.get(p2)
        if first and first == second:
            verdict, why = first, w1
        else:
            verdict, why = "tie", "judge disagreed with itself (position bias)"
        tally[verdict] += 1
        print(f"{i:2} | {p['text'][:30]:30} | {verdict:11} | {why[:34]}")

    n = len(pairs)
    print(f"\n{'':12} | wins | share")
    print(f"{'-'*12}-+------+------")
    for k in ("serial", "concurrent", "tie"):
        print(f"{k:12} | {tally[k]:4} | {tally[k]/n*100:4.0f}%")

    # The bar is "does not lose", not "wins". Persona was meant to be untouched.
    lost = tally["serial"] - tally["concurrent"]
    if lost > n * 0.2:
        print(f"\nPERSONA REGRESSION: serial won {tally['serial']}/{n}, "
              f"concurrent {tally['concurrent']}/{n}. She reads differently.")
    else:
        print(f"\npersona ok — no measurable regression "
              f"(serial {tally['serial']}, concurrent {tally['concurrent']}, "
              f"tie {tally['tie']})")


asyncio.run(main())
