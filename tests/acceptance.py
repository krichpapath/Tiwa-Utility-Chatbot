r"""Run deterministic acceptance checks in separate processes and temporary data.

Usage: .venv\Scripts\python.exe -X utf8 tests/acceptance.py
Live provider/Discord checks are separate: this command never needs credentials.
"""

import argparse
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
noisebench outagebench eyebench deploymentbench searchqualitybench activationbench recordingsbench dashboardbench groupvoicebench calendarproofbench calendareditbench daverecoverybench listenstartupbench musicartistbench musicintentbench musicqueuebench calendarconfirmbench calendarconversationbench""".split()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "checks", nargs="*", help="Check names without .py; default: all offline checks"
    )
    parser.add_argument("--list", action="store_true", help="List the offline checks")
    args = parser.parse_args()
    if args.list:
        print("\n".join(CHECKS))
        return 0
    selected = args.checks or CHECKS
    unknown = set(selected) - set(CHECKS)
    if unknown:
        parser.error("Unknown offline checks: " + ", ".join(sorted(unknown)))
    out = ROOT / "qa-results" / "acceptance"
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name in selected:
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="tiwa-qa-") as scratch:
            env = dict(
                os.environ,
                TIWA_DATA_DIR=scratch,
                TIWA_LOG_PROMPTS="0",
                GRADIO_ANALYTICS_ENABLED="False",
                TIWA_LISTEN="0",
                TIWA_VOICE="dj",
            )
            try:
                run = subprocess.run(
                    [sys.executable, "-X", "utf8", str(ROOT / "tests" / f"{name}.py")],
                    cwd=ROOT,
                    env=env,
                    capture_output=True,
                    timeout=120,
                )
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
