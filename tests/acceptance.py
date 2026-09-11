r"""Run deterministic acceptance checks in separate processes and temporary data.

Usage: .venv\Scripts\python.exe -X utf8 tests/acceptance.py
Live provider/Discord checks are separate: this command never needs credentials.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
CHECKS = """test_memory memoryrecallbench namebench relationshipbench djbench discordbench panelbench turnbench minibench
djminibench forkbench latebench searchminibench calminibench growthbench
recordbench tastebench calbench searchbench routerbench wakebench dumpbench
noisebench outagebench eyebench deploymentbench searchqualitybench activationbench recordingsbench dashboardbench groupvoicebench calendarproofbench calendareditbench daverecoverybench listenstartupbench musicartistbench musicqueuebench""".split()


def main():
    out = ROOT / "qa-results" / "acceptance"
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name in CHECKS:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="tiwa-qa-") as scratch:
            env = dict(os.environ, TIWA_DATA_DIR=scratch, TIWA_LOG_PROMPTS="0",
                       GRADIO_ANALYTICS_ENABLED="False")
            try:
                run = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "tests" / f"{name}.py")],
                                     cwd=ROOT, env=env, capture_output=True, timeout=120)
                code, output = run.returncode, run.stdout + run.stderr
            except subprocess.TimeoutExpired as error:
                code, output = 124, (error.stdout or b"") + (error.stderr or b"") + b"\nTIMEOUT\n"
        (out / f"{name}.log").write_bytes(output)
        row = dict(check=name, exit=code, seconds=round(time.monotonic() - started, 2))
        results.append(row)
        print(f"{'PASS' if code == 0 else 'FAIL'} {name}: {row['seconds']}s", flush=True)
    (out / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return int(any(row["exit"] for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
