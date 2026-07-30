"""Search quality: does she call it, what keywords does she pick, what comes back.

Offline by default — the mechanics (dedupe, region, formatting) run against a fake
DDGS. `--live` also asks the real model what it would search for and hits the real
index, which is the part you have to read and judge yourself.

    py -X utf8 tests\\searchbench.py
    py -X utf8 tests\\searchbench.py --live
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, tools  # noqa: E402

LIVE = "--live" in sys.argv


def fake_ddgs(pages):
    """Stand in for ddgs.DDGS so the mechanics run with no network."""
    class Fake:
        def text(self, query, region="us-en", max_results=8):
            calls.append((query, region))
            return pages[: max_results]
    return Fake


calls = []


def offline():
    import ddgs

    pages = [{"title": f"Result {i}", "href": f"https://site{i}.com/a",
              "body": f"body {i}"} for i in range(8)]
    real_ddgs, ddgs.DDGS = ddgs.DDGS, fake_ddgs(pages)
    db = memory.connect(":memory:")

    tools.SEEN_URLS.clear()
    first = tools.web_search(db, "premier league results")
    assert first.count("\n") == 4, "5 results, not 3 — she was starving on 3"
    assert "[site0.com]" in first, "domain must be shown: it is her only source signal"

    # the exact complaint: search again, get the same page back
    again = tools.web_search(db, "premier league results")
    assert "already saw this turn" in again, again
    assert len(tools.SEEN_URLS) == 8

    tools.SEEN_URLS.clear()
    tools.web_search(db, "ผลบอลเมื่อคืน")
    assert calls[-1][1] == "th-th", calls[-1]
    tools.web_search(db, "premier league")
    assert calls[-1][1] == "us-en", calls[-1]

    ddgs.DDGS = real_ddgs  # --live must hit the real index, not these fakes
    print("| check | result |")
    print("|---|---|")
    print("| 5 results with domains | ok |")
    print("| repeat query in one turn | refused, told to change keywords |")
    print("| Thai query region | th-th |")
    print("| English query region | us-en |")


ASKS = [
    "มึงรู้ไหมว่าใครชนะบอลเมื่อคืน",
    "what's that new gojo thing everyone's on about",
    "ราคา RTX 5090 ตอนนี้เท่าไหร่",
    "is the new iphone any good",
    "เห็นเพื่อนพูดถึงเกม silksong อ่ะ มันคืออะไร",
]


async def live():
    """What keywords does she actually pick, and what comes back for them."""
    from tiwa import pipeline

    print("\n| they said | she searched | top domains |")
    print("|---|---|---|")
    for ask in ASKS:
        tools.SEEN_URLS.clear()
        db = memory.connect(":memory:")
        queries = []
        real = tools.TOOLS["web_search"]["fn"]

        def spy(db, arg, _real=real, _q=queries):
            _q.append(arg)
            return _real(db, arg)

        tools.TOOLS["web_search"]["fn"] = spy
        try:
            await pipeline._inner_brief(db, "Krich", ask)
        finally:
            tools.TOOLS["web_search"]["fn"] = real
        doms = sorted({u.split("/")[2] for u in tools.SEEN_URLS})[:3]
        print(f"| {ask} | {' / '.join(queries) or '— did not search'} | "
              f"{', '.join(doms) or '—'} |")
    print("\nJudge two things: did she search at all, and is the query keywords "
          "rather than the sentence they typed.")


offline()
if LIVE:
    asyncio.run(live())
print("\nsearch ok — dedupe, region and formatting behave"
      + ("" if LIVE else " (offline only; --live judges the keywords)"))
