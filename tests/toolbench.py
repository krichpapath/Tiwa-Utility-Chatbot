"""G2 — who calls tools properly: local 8B or the API model?

Tools are stubbed with canned replies on purpose: this measures the MODEL's
choice and argument quality, not network luck.

    py -X utf8 tests\\toolbench.py            # both providers
    py -X utf8 tests\\toolbench.py ollama     # one
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import llm, memory, pipeline, tools  # noqa: E402

# probe, expected tool, substring the arg must contain (None = any arg ok)
PROBES = [
    ("who is Steven", "recall", "steven"),
    ("did I ever tell you about Gojo", "recall", "gojo"),
    ("มึงรู้จัก Mint ไหม", "recall", "mint"),
    ("Nara said hi to me today", "recall", "nara"),
    ("what's the weather in Bangkok right now", "web_search", "bangkok"),
    ("who won the world cup last night", "web_search", None),
    ("anything on my calendar this week", "calendar_read", None),
    ("remind me, what do I have tomorrow", "calendar_read", None),
]

CANNED = {
    "recall": "no memory",
    "web_search": "Bangkok: 34C, humid. Scattered storms this evening.",
    "calendar_read": "Tue 15:00 dentist",
    "calendar_write": "queued",
}


def stub_tools(seen: list):
    for name, t in tools.TOOLS.items():
        t["fn"] = lambda db, arg, _n=name: (seen.append((_n, arg)), CANNED[_n])[1]


async def bench(provider: str) -> dict:
    llm.PROVIDER = provider  # _tool_chat + tool_result_msg read this at call time
    db = memory.connect(":memory:")
    r = {"provider": provider, "hit": 0, "arg_ok": 0, "blank": 0, "calls": 0,
         "ms": [], "n": len(PROBES)}
    for text, want_tool, want_arg in PROBES:
        seen = []
        stub_tools(seen)
        t0 = time.perf_counter()
        await pipeline._inner_brief(db, "Krich", text)
        ms = (time.perf_counter() - t0) * 1000
        r["ms"].append(ms)
        r["calls"] += len(seen)
        r["blank"] += sum(1 for _, a in seen if not a.strip())
        hit = any(n == want_tool for n, _ in seen)
        arg_ok = want_arg is None or any(
            n == want_tool and want_arg in a.lower() for n, a in seen
        )
        r["hit"] += hit
        r["arg_ok"] += hit and arg_ok
        flag = "ok " if hit and arg_ok else ("ARG" if hit else "MISS")
        print(f"  {flag} {ms:6.0f}ms  {text[:38]:38} -> {seen or 'no tool call'}")
    return r


async def main():
    rows = []
    for p in sys.argv[1:] or ["ollama", "openrouter"]:
        print(f"\n=== {p} ===")
        rows.append(await bench(p))
    print(f"\n{'provider':12}{'right tool':>12}{'right arg':>11}{'blank args':>12}"
          f"{'median ms':>11}")
    for r in rows:
        med = sorted(r["ms"])[len(r["ms"]) // 2]
        print(f"{r['provider']:12}{r['hit']}/{r['n']:<10}{r['arg_ok']}/{r['n']:<9}"
              f"{r['blank']}/{r['calls']:<10}{med:>9.0f}")


asyncio.run(main())
