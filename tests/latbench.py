"""The A/B, live: same turns through serial and concurrent, timed.

Her log said the six seconds was never the work —

    whole turn 6431ms p50 | inner pass 2597ms x1.7 | persona 1958ms | tools 3ms

— so S3 took the tool pass off the path and let her talk while dispatch runs.
This is whether that actually happened against a real model.

**Time to HER REPLY**, not time to everything settled. `respond()` returns her
words; late work keeps running behind it, and that is the point.

Runs against a COPY of data/tiwa.db so real memory is in play (turn_context and
mentioned() both read it) without writing fixtures into the live activity log —
djbench learned that one the hard way.

    py -X utf8 tests\\latbench.py            12 turns x 2 arms
    py -X utf8 tests\\latbench.py --n 6

Writes data/ab.json for personabench. Costs real tokens.
"""
import argparse
import asyncio
import json
import re
import shutil
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, pipeline  # noqa: E402

TARGET_MS = 3500  # SWARM.md S3's abandon condition
AB = memory.DATA_DIR / "ab.json"


def sample(db, n: int) -> list:
    """Real turns, in the real mix: music asks, chat, a question, a search."""
    turns = []
    for _, kind, text in db.execute(
        "SELECT id, kind, text FROM log WHERE kind='turn' ORDER BY id"
    ):
        m = re.match(r"(.+?): (.*?) -> (.*)", text, re.S)
        if m and m.group(2).strip():
            turns.append({"author": m.group(1), "text": m.group(2),
                          "reply": m.group(3)})
    # Stratified, not just spread. Music is 87 of 111 real turns, so an even
    # sample is almost all "play X" — which times the same path over and over and
    # tells personabench nothing, because voice shows up in chat and in fights,
    # not in a queue confirmation. Half music, half everything else.
    ask = [t for t in turns if pipeline._missed_music(t["text"])]
    chat = [t for t in turns if not pipeline._missed_music(t["text"])]
    half = n // 2
    picked = []
    for group, want in ((ask, n - half), (chat, half)):
        step = max(1, len(group) // want) if group else 1
        picked += group[::step][:want]
    for i, t in enumerate(picked):
        j = turns.index(t)
        t["hist"] = [{"role": "user", "content": f"{p['author']}: {p['text']}"}
                     for p in turns[max(0, j - 3):j]]
        t["hist"].append({"role": "user", "content": f"{t['author']}: {t['text']}"})
    return picked


async def one(db, t, mode):
    was, pipeline.TURN_MODE = pipeline.TURN_MODE, mode
    try:
        t0 = time.perf_counter()
        reply = await pipeline.respond(db, list(t["hist"]), t["author"], t["text"])
        return reply, (time.perf_counter() - t0) * 1000
    finally:
        pipeline.TURN_MODE = was


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=12)
    args = ap.parse_args()

    live = Path(memory.DB_PATH)
    copy = Path(memory.DATA_DIR) / "ab_copy.db"
    shutil.copy(live, copy)
    db = memory.connect(str(copy))

    turns = sample(memory.connect(str(live)), args.n)
    print(f"{len(turns)} real turns x 2 arms\n")
    print(f"{'#':>2} | {'message':38} | {'serial':>8} | {'concurrent':>10}")
    print(f"{'-'*2}-+-{'-'*38}-+-{'-'*8}-+-{'-'*10}")

    rows, ser, con = [], [], []
    for i, t in enumerate(turns, 1):
        s_reply, s_ms = asyncio.run(one(db, t, "serial"))
        c_reply, c_ms = asyncio.run(one(db, t, "concurrent"))
        ser.append(s_ms)
        con.append(c_ms)
        rows.append({"author": t["author"], "text": t["text"],
                     "serial": s_reply, "concurrent": c_reply,
                     "ms_serial": round(s_ms), "ms_concurrent": round(c_ms)})
        print(f"{i:2} | {t['text'][:38]:38} | {s_ms:7.0f}ms | {c_ms:9.0f}ms")

    print(f"\n{'':10} | {'p50':>9} | {'p95':>9} | {'mean':>9}")
    print(f"{'-'*10}-+-{'-'*9}-+-{'-'*9}-+-{'-'*9}")
    for name, v in (("serial", ser), ("concurrent", con)):
        print(f"{name:10} | {pct(v, .5):8.0f}ms | {pct(v, .95):8.0f}ms | "
              f"{statistics.mean(v):8.0f}ms")

    AB.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    db.close()  # Windows will not unlink a file sqlite still has open
    copy.unlink(missing_ok=True)

    p50 = pct(con, .5)
    cut = (1 - p50 / pct(ser, .5)) * 100
    print(f"\nconcurrent p50 is {cut:.0f}% under serial. Target is {TARGET_MS}ms.")
    print(f"pairs -> {AB} (personabench reads this)")
    if p50 >= TARGET_MS:
        print(f"\nABANDON CONDITION: p50 {p50:.0f}ms misses the {TARGET_MS}ms target")
    else:
        print(f"\nlatency ok — p50 {p50:.0f}ms, under the {TARGET_MS}ms target")
