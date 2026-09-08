"""Live model measures using synthetic QA memories, never private memory.

No calendar writes, Discord posts, or voice calls. Requires API access.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = [("test_memory", "--live"), ("djminibench", "--live"),
         ("calbench", "--live"), ("searchbench", "--live"), ("eyebench", "--live"),
         ("tastebench", "--live"), ("extractbench", "openrouter"),
         ("factbench", "openrouter"), ("worthbench",), ("episodebench",),
         ("pickbench",), ("smoke",), ("moodbench",), ("chatbench",), ("livechat",)]


def run(case):
    name, *args = case
    out = ROOT / "qa-results" / "acceptance"
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tiwa-live-") as scratch:
        # Only generated fixture rows can reach a remote provider.
        target = sqlite3.connect(str(Path(scratch) / "tiwa.db"))
        target.close()
        # Paid-call usage remains in the real shared ledger; only memories/logs isolate.
        env = dict(os.environ, TIWA_DATA_DIR=scratch, TIWA_LOG_PROMPTS="0",
                   TIWA_SPEND_FILE=str(ROOT / "data" / "spend.json"), TIWA_QA_FIXTURES="1")
        try:
            p = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tests" / f"{name}.py"), *args],
                               cwd=ROOT, env=env, capture_output=True, timeout=600)
            code, output = p.returncode, p.stdout + p.stderr
        except subprocess.TimeoutExpired as e:
            code, output = 124, (e.stdout or b"") + (e.stderr or b"") + b"\nTIMEOUT"
    (out / f"live-{name}.log").write_bytes(output)
    row = dict(check=name, exit=code, seconds=round(time.monotonic() - started, 2))
    print(json.dumps(row), flush=True)
    return row


if __name__ == "__main__":
    cases = [c for c in CASES if not sys.argv[1:] or c[0] in sys.argv[1:]]
    # Serial paid calls: preserve provider capacity for normal conversations.
    results = [run(case) for case in cases]
    result_file = ROOT / "qa-results" / "acceptance" / "live-results.json"
    old = json.loads(result_file.read_text()) if result_file.exists() else []
    merged = {r["check"]: r for r in old}
    merged.update({r["check"]: r for r in results})
    result_file.write_text(json.dumps(list(merged.values()), indent=2))
    raise SystemExit(int(any(r["exit"] for r in results)))
