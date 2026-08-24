"""Routing accuracy, live. Does dispatch pick the right mini more often than the
tool pass picked the right tool?

The labelled set is mined from her own log — 111 real turns, in the real traffic
mix — and then HAND-CORRECTED, which is the part that matters. The raw labels are
what the old system *did*, and it was wrong often enough that scoring against
them would just reward replicating its mistakes. Every turn where a music ask
reached no tool is relabelled `dj`, because that is what should have happened.

That correction is also what makes the comparison fair in the other direction:
the same corrected labels score both arms.

Each case carries the previous turns as `recent`, because production does. "teto
version" and "venom - eminem" are unresolvable on their own, and grading a router
on context it never gets measures nothing.

    py -X utf8 tests\\dispatchbench.py            all 111 turns
    py -X utf8 tests\\dispatchbench.py --n 30     a cheaper slice

Costs real tokens: one dispatch call per case.
"""
import argparse
import asyncio
import collections
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, minis, pipeline  # noqa: E402

TOOL_TO_MINI = {"play_music": "dj", "queue_music": "dj", "skip_music": "dj",
                "stop_music": "dj", "web_search": "search",
                "calendar_read": "calendar", "calendar_write": "calendar"}

# Hand corrections, by index into the mined set. Every one of these is a turn
# where the tool pass called NOTHING and should have called something — the
# missed-ask failure djbench was built around. Left alone: questions about the
# deck, agreements, roleplay, and chat.
FORCE_DJ = {13, 23, 24, 35, 38, 40, 41, 52, 54, 60, 63, 65, 67, 82, 83, 87, 88,
            91, 93, 94, 101, 102, 103, 107, 108}

# The `search` labels were ALL wrong, and finding out why is the most useful
# thing this bench did. Every one of the 8 logged web_search calls was the old
# system identifying a TRACK before playing it — "Play Mili limbus song",
# "เปิดเพลง lobrary of ruina". DJ Tiwa does that itself now, so the correct
# answer is `dj` alone and dispatch was right where this set said it was wrong.
#
# Search Tiwa is for facts: scores, prices, news, something she does not
# recognise. In 111 real turns there is not one of those. Reported, not hidden.
NOT_SEARCH = {3, 29, 58, 62, 73, 74, 86}   # -> dj only
DECK_QUESTION = {92}                        # "ชื่อเพลง" — _doing() answers it

# THE SET IS FROZEN HERE, and it has to be.
#
# `mine()` derives a turn's ground truth from the `tool` rows logged beside it.
# This branch has no tool pass, so a turn she handles now logs `mini` rows and
# mines as "needed nothing" — which is how a live Discord session on 2026-08-24
# quietly appended 32 fixtures, three of them the exact behaviour that session
# was testing. "ใครชนะบอลเมื่อคืน" arrived labelled `[]` and dispatch was marked
# WRONG for searching it, which is the one thing it had just been fixed to do.
#
# Deriving labels from `mini` rows instead would be worse: grading dispatch
# against what dispatch did scores 100% by construction. A benchmark cannot mine
# its own answers from the system under test.
#
# So: the corrected set is these 111 turns and stops there. To grow it, replay
# newer turns, read them, and add the indices to the correction sets above by
# hand — which is the work that made the first 111 worth anything.
CORRECTED_THROUGH = 111


def mine(db) -> list:
    """Pair each logged turn with the tools that fired during it."""
    cases, pending = [], []
    for _, kind, text in db.execute(
        "SELECT id, kind, text FROM log WHERE kind IN ('turn','tool') ORDER BY id"
    ):
        if kind == "tool":
            m = re.match(r"([a-z_]+)\(", text)
            if m:
                pending.append(m.group(1))
            continue
        m = re.match(r"(.+?): (.*?) -> (.*)", text, re.S)
        if m:
            cases.append({"author": m.group(1), "text": m.group(2),
                          "reply": m.group(3), "tools": pending})
        pending = []

    extra = len(cases) - CORRECTED_THROUGH
    if extra > 0:
        print(f"note: {extra} newer turn(s) mined but NOT scored — no tool rows to "
              f"label them with. See CORRECTED_THROUGH.")
    cases = cases[:CORRECTED_THROUGH]

    for i, c in enumerate(cases):
        did = sorted({TOOL_TO_MINI[t] for t in c["tools"] if t in TOOL_TO_MINI})
        c["did"] = did                       # what the OLD system routed to
        want = set(did) | ({"dj"} if i in FORCE_DJ else set())
        if i in NOT_SEARCH:
            want = (want - {"search"}) | {"dj"}
        if i in DECK_QUESTION:
            want = set()
        c["want"] = sorted(want)
        # the window production gives dispatch, built the same way respond() does
        c["recent"] = "\n".join(
            f"{p['author']}: {p['text']}\nทิวา: {p['reply'][:120]}"
            for p in cases[max(0, i - 4):i])
    return cases


async def run(cases) -> list:
    out = []
    for i, c in enumerate(cases, 1):
        db = memory.connect(":memory:")
        raw = await minis.dispatch(db, c["author"], c["text"], c["recent"])
        # grade the SYSTEM, not the model alone: production always applies the
        # deterministic veto and net over the router's answer
        jobs = pipeline.route(db, raw["dispatch"], c["text"],
                          pipeline._missed_music(c["text"]))
        out.append(sorted({name for name, _ in jobs}))
        print(f"\r  dispatching {i}/{len(cases)}...", end="", flush=True)
    print("\r" + " " * 40 + "\r", end="")
    return out


def report(cases, got):
    per = collections.defaultdict(lambda: [0, 0])       # label -> [hit, total]
    old_hits = new_hits = 0
    empty_total = empty_new_fp = empty_old_fp = 0
    misses = []

    for c, g in zip(cases, got):
        want, did = c["want"], c["did"]
        key = ",".join(want) or "(nothing)"
        per[key][1] += 1
        if g == want:
            per[key][0] += 1
            new_hits += 1
        else:
            misses.append((c, want, g))
        if did == want:
            old_hits += 1
        if not want:
            empty_total += 1
            empty_new_fp += bool(g)
            empty_old_fp += bool(did)

    n = len(cases)
    print(f"{'label':14} | {'n':>4} | dispatch accuracy")
    print(f"{'-'*14}-+-{'-'*4}-+------------------")
    for key, (hit, tot) in sorted(per.items(), key=lambda kv: -kv[1][1]):
        print(f"{key:14} | {tot:4} | {hit:3}/{tot:<3} {hit/tot*100:5.1f}%")

    print(f"\n{'':22} | {'tool pass (old)':>16} | {'dispatch (new)':>15}")
    print(f"{'-'*22}-+-{'-'*16}-+-{'-'*15}")
    print(f"{'overall accuracy':22} | {old_hits/n*100:15.1f}% | {new_hits/n*100:14.1f}%")
    print(f"{'false positives on':22} | {empty_old_fp:>6}/{empty_total:<9} | "
          f"{empty_new_fp:>6}/{empty_total:<8}")
    print(f"{'  turns needing none':22} | {empty_old_fp/empty_total*100:15.1f}% | "
          f"{empty_new_fp/empty_total*100:14.1f}%")

    print(f"\nfirst misses ({len(misses)} total):")
    for c, want, g in misses[:8]:
        print(f"  want {str(want):18} got {str(g):18} | {c['text'][:44]}")

    return new_hits / n, old_hits / n, empty_new_fp / empty_total


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="first N cases only")
    args = ap.parse_args()

    cases = mine(memory.connect())
    if args.n:
        cases = cases[:args.n]
    print(f"{len(cases)} turns, hand-corrected, in the real traffic mix\n")
    got = asyncio.run(run(cases))
    new, old, fp = report(cases, got)

    # SWARM.md's abandon condition: dispatch must not be worse than the tool pass.
    assert new >= old, (
        f"ABANDON CONDITION: dispatch {new*100:.1f}% is worse than the tool pass "
        f"{old*100:.1f}% on the same hand-corrected labels")
    print(f"\ndispatch ok — {new*100:.1f}% vs the tool pass's {old*100:.1f}%, "
          f"{fp*100:.1f}% false positives on turns needing nothing")
