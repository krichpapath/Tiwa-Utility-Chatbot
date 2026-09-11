"""Does she still sound like herself? Blind pairwise A/B, live.

Reads two files `latbench` wrote — one per arm, same messages, same memory. A
judge picks which reply is more ทิวา, against her own `prompts/tiwa.md`.

The arms used to be `TIWA_TURN=serial` and `TIWA_TURN=concurrent` inside one
process. This branch has one turn shape, so the arms are BRANCHES:

    git checkout main  && py -X utf8 tests\\latbench.py --tag main
    git checkout swarm && py -X utf8 tests\\latbench.py --tag swarm
    py -X utf8 tests\\personabench.py data\\ab_main.json data\\ab_swarm.json

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
    "properties": {"pick": {"type": "string", "enum": ["A", "B"]}, "why": {"type": "string"}},
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


def load(path: Path) -> dict:
    """One arm, keyed by the message so two files line up even if the sample
    order differs. `reply` is what latbench writes on this branch."""
    if not path.exists():
        raise SystemExit(
            f"no {path} — run tests/latbench.py --tag {path.stem[3:]} on that branch first"
        )
    return {
        r["text"]: (r.get("reply") or "").strip()
        for r in json.loads(path.read_text(encoding="utf-8"))
    }


async def main():
    # The A/B used to be one process flipping TIWA_TURN. There is one turn shape
    # per branch now, so the two arms are two FILES — run latbench on each branch
    # and hand both here. The judging is unchanged, and so is what a pass means.
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    if len(argv) != 2:
        raise SystemExit(
            "usage: personabench.py <arm-a.json> <arm-b.json>\n"
            "  e.g. py -X utf8 tests\\personabench.py data\\ab_main.json data\\ab_swarm.json\n"
            "  each file comes from: py -X utf8 tests\\latbench.py --tag <name>"
        )
    pa, pb = Path(argv[0]), Path(argv[1])
    a_name, b_name = pa.stem.replace("ab_", ""), pb.stem.replace("ab_", "")
    A, B = load(pa), load(pb)

    shared = [t for t in A if t in B and A[t] and B[t]]
    if not shared:
        raise SystemExit("the two arms share no messages — sample the same turns")
    print(f"{a_name} vs {b_name}: {len(shared)} pairs, each judged twice with the sides swapped\n")

    tally = {a_name: 0, b_name: 0, "tie": 0}
    print(f"{'#':>2} | {'message':30} | verdict     | why")
    print(f"{'-' * 2}-+-{'-' * 30}-+-------------+{'-' * 34}")
    for i, text in enumerate(shared, 1):
        # order 1: arm A is shown first. order 2: arm B is shown first.
        p1, w1 = await ask(text, A[text], B[text])
        p2, _ = await ask(text, B[text], A[text])
        first = {"A": a_name, "B": b_name}.get(p1)
        second = {"A": b_name, "B": a_name}.get(p2)
        if first and first == second:
            verdict, why = first, w1
        else:
            verdict, why = "tie", "judge disagreed with itself (position bias)"
        tally[verdict] += 1
        print(f"{i:2} | {text[:30]:30} | {verdict:11} | {why[:34]}")

    n = len(shared)
    print(f"\n{'':12} | wins | share")
    print(f"{'-' * 12}-+------+------")
    for k in (a_name, b_name, "tie"):
        print(f"{k:12} | {tally[k]:4} | {tally[k] / n * 100:4.0f}%")

    # The bar is "does not lose", not "wins". Persona was meant to be untouched.
    lost = tally[a_name] - tally[b_name]
    if lost > n * 0.2:
        print(
            f"\nPERSONA REGRESSION: {a_name} won {tally[a_name]}/{n}, "
            f"{b_name} {tally[b_name]}/{n}. She reads differently."
        )
    else:
        print(
            f"\npersona ok — no measurable regression "
            f"({a_name} {tally[a_name]}, {b_name} {tally[b_name]}, "
            f"tie {tally['tie']})"
        )


if __name__ == "__main__":  # importable: chatbench reuses ask()
    asyncio.run(main())
