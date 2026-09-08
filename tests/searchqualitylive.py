"""Small live search sample. Synthetic questions; no Discord posts or playback."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import memory, minis, music, tools

rows = []
for kind, tasks in (
    ("music", ["Mili Hero", "Warframe Red Line", "Bodyslam แสงสุดท้าย"]),
    ("web", ["In Python, how are str.casefold() and str.lower() different?",
             "เกม Hollow Knight Silksong คือเกมอะไร ใครพัฒนา",
             "Who won the match last night?"]),
):
    for task in tasks:
        started = time.monotonic()
        tools.new_turn()
        try:
            if kind == "music":
                hit = music.find(task)
                result = {k: hit[k] for k in ("title", "id", "duration")}
            else:
                result = minis.search(memory.connect(":memory:"), task)
            row = dict(kind=kind, task=task, result=result)
        except Exception as error:
            row = dict(kind=kind, task=task, error=f"{type(error).__name__}: {error}")
        row["seconds"] = round(time.monotonic() - started, 2)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        Path("qa-results/search-quality-live.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
