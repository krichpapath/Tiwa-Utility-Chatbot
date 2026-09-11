"""Export her voice as fine-tune rows. OpenAI Chat Completions JSONL.

    py -X utf8 -m tiwa.record            -> data/persona.jsonl
    py -X utf8 -m tiwa.record --stats    -> what is in the log and what survives

`PLAN.md:345` and `next.md` both list this as the prerequisite for LoRA/SFT:
roughly a thousand good exchanges have to exist first, and nothing was writing
them down. `llm_log` already had every call; this is the filter, not new plumbing.

WHY THE FILTER IS EXACT, NOT A HEURISTIC
----------------------------------------
Every pass has a different system[0]: the tool pass opens with "You are ทิวา's
inner thoughts", dispatch with "You decide which of", DJ with "You are the DJ",
extraction with its schema. Only her voice opens with `prompts/tiwa.md` itself.

So a persona row is one whose FIRST system message IS the persona file. Not a
keyword guess — a string equality against the file on disk. Mini traces cannot
match it, which is what keeps multi-agent traces out of the persona training set
without needing a column to say so.

AND WHY THERE IS NO `arch` TAG
------------------------------
There does not need to be one. `pipeline.say()` is shared by both paths and
`forkbench` asserts serial and concurrent build a byte-identical state block —
so a persona row is the same row either way. If that ever stops being true,
forkbench fails first and this file is the second thing to fix.
"""

import argparse
import json
import re
from pathlib import Path

from . import memory
from .pipeline import PERSONA

OUT = memory.DATA_DIR / "persona.jsonl"

# llm.log_llm flattens messages to "[role]\ncontent" joined by blank lines.
_BLOCK = re.compile(r"^\[(system|user|assistant|tool)\]\n", re.M)

# _voice()'s follow-up also goes through say(), so it is a persona row by the
# rule above — but its "user" turn is the placeholder "(earlier message)", which
# would teach her to answer a message that is not there.
_FOLLOW_UP = "just this second found out"


def _messages(request: str) -> list:
    """Undo the flattening. Returns [] on anything that does not parse."""
    parts = _BLOCK.split(request or "")
    if len(parts) < 3:
        return []
    # split() gives ['', role, body, role, body, ...]
    return [{"role": r, "content": b.strip("\n")} for r, b in zip(parts[1::2], parts[2::2])]


def rows(db, keep_state: bool = True) -> list:
    """Every turn where she spoke, oldest first, as chat-completions messages.

    `keep_state` decides whether the per-turn `[inner-state]` block stays in.
    This is the one real judgment call in the file and it is not mine to make:

      keep it   — faithful to what she actually saw. Some replies are otherwise
                  unexplainable ("เปิดให้ละ" with no sign a song was queued).
      drop it   — next.md wants fine-tuning so she "sounds like herself without
                  prompt crutches", and training WITH the crutch teaches her to
                  need it.

    Export both and compare. Default keeps, because faithful is the safer default
    for a set nobody has trained on yet.
    """
    out = []
    for _, _, _, _, _, _, request, response in db.execute(
        "SELECT id, ts, provider, model, ms, tokens, request, response FROM llm_log ORDER BY id"
    ):
        msgs = _messages(request)
        if not msgs or msgs[0]["role"] != "system":
            continue
        if msgs[0]["content"].strip() != PERSONA.strip():
            continue  # every other pass, and every mini, falls out here
        if any(_FOLLOW_UP in m["content"] for m in msgs):
            continue
        reply = (response or "").split("\n[tool_calls]")[0].strip()
        if not reply:
            continue  # an empty turn teaches her to say nothing
        if not keep_state:
            msgs = [msgs[0]] + [m for m in msgs[1:] if not m["content"].startswith("[inner-state")]
        out.append({"messages": msgs + [{"role": "assistant", "content": reply}]})
    return out


def export(db, path: Path = OUT, keep_state: bool = True) -> int:
    data = rows(db, keep_state)
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in data:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(data)


def stats(db) -> dict:
    """What is in the log, by pass. The persona line is what this file exports."""
    seen = {}
    for (request,) in db.execute("SELECT request FROM llm_log"):
        msgs = _messages(request)
        head = msgs[0]["content"][:40].replace("\n", " ") if msgs else "(unparsed)"
        if msgs and msgs[0]["content"].strip() == PERSONA.strip():
            head = "PERSONA — her voice"
        seen[head] = seen.get(head, 0) + 1
    return seen


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stats", action="store_true", help="show the log breakdown only")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument(
        "--drop-state",
        action="store_true",
        help="strip the per-turn [inner-state] block (see rows())",
    )
    args = ap.parse_args()

    db = memory.connect()
    if args.stats:
        print(f"{'first system message':44} | rows")
        print(f"{'-' * 44}-+-----")
        for head, n in sorted(stats(db).items(), key=lambda kv: -kv[1]):
            print(f"{head:44} | {n:4}")
        raise SystemExit

    n = export(db, Path(args.out), keep_state=not args.drop_state)
    print(f"{n} persona rows -> {args.out}")
    if n < 1000:
        # next.md: LoRA/SFT needs roughly a thousand good exchanges to exist first
        print(f"({1000 - n} short of the ~1000 next.md says fine-tuning needs)")
