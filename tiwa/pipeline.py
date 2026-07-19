"""Two-pass turn: inner reasoning (recall tool) -> persona reply. Phase 3."""
import os
import re
from pathlib import Path

from ollama import AsyncClient

from . import memory
from .memory import MODEL, TIWA

PERSONA = (Path(__file__).parents[1] / "prompts" / "tiwa.md").read_text(encoding="utf-8")

# her voice can run on a different model than the tool/extraction passes
PERSONA_MODEL = os.environ.get("TIWA_PERSONA_MODEL", MODEL)

_client = AsyncClient()

_INNER_SYSTEM = f"""You are {TIWA}'s inner thoughts, run before she replies. You are NOT the reply.
The brief is about the LAST message only; earlier lines exist only to resolve who "he/she/it" means.
Use the recall tool to check memory for each person, character, or topic in the message that matters.
Then output a short plain-text brief (max 5 lines) addressed to her as "you":
- what you actually know or feel about them (memory only — NEVER invent facts or opinions),
  e.g. "you remember Steven: Krich's cousin, plays guitar"
- what you have no memory of (so you ask instead of bluffing), e.g. "no memory of Steven — ask"
Nothing else. No greetings, no reply draft, no lines about the speaker's own knowledge."""

_RECALL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall",
        "description": f"Check {TIWA}'s memory for a person, character, or topic. "
        "Returns known facts or 'no memory'.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "who or what to recall"}},
            "required": ["name"],
        },
    },
}


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"<tool_call>.*?(</tool_call>|$)", "", text, flags=re.S)  # 8B leaks these as text
    return re.sub(r"^(Tiwa|ทิวา)\s*:\s*", "", text.strip()).strip()


def _arg_name(args) -> str:
    # ponytail: 8B mangles tool args — nests them ({'object': {'name': X}}) or renames
    # the key. Dig until we hit a string; empty string on anything hopeless.
    while isinstance(args, dict):
        args = args.get("name") or next(iter(args.values()), "")
    return str(args or "")


async def _inner_brief(db, author: str, text: str, recent: str = "") -> str:
    prefix = f"earlier lines (context only):\n{recent}\n\n" if recent else ""
    msgs = [
        {"role": "system", "content": _INNER_SYSTEM},
        {"role": "user", "content": f"{prefix}{author}: {text}"},
    ]
    for _ in range(3):  # ponytail: max 3 tool rounds, plenty for one message
        resp = await _client.chat(
            model=MODEL,
            messages=msgs,
            tools=[_RECALL_TOOL],
            think=False,
            # Qwen3 warns against greedy decoding (repetition loops) — low temp, not 0
            options={"temperature": 0.3, "top_p": 0.8, "top_k": 20, "num_ctx": 4096},
        )
        msgs.append(resp.message)
        if not resp.message.tool_calls:
            return _clean(resp.message.content or "")
        for tc in resp.message.tool_calls:
            name = _arg_name(tc.function.arguments)
            msgs.append({"role": "tool", "content": memory.lookup(db, name), "tool_name": "recall"})
    # tool rounds exhausted — force a brief from what was recalled so far
    resp = await _client.chat(
        model=MODEL, messages=msgs, think=False,
        options={"temperature": 0.3, "top_p": 0.8, "top_k": 20, "num_ctx": 4096},
    )
    return _clean(resp.message.content or "")


async def respond(db, hist: list, author: str, text: str) -> str:
    """One Tiwa turn. `hist` = chat messages incl. the current one. Caller appends the reply."""
    recent = "\n".join(
        m["content"] if m["role"] == "user" else f"{TIWA}: {m['content']}"
        for m in hist[-5:-1]  # lines before the current message, so "he/she" resolves
    )
    inner = await _inner_brief(db, author, text, recent)
    auto = memory.turn_context(db, author)

    # ponytail: 8B forgets rules buried in the long persona prompt — restate the three
    # highest-failure ones per turn. Language is decided in CODE, not by the model.
    lang = "Thai" if any("฀" <= c <= "๿" for c in text) else "English"
    rules = (
        f"Reply in {lang} only. "
        f"You are talking TO {author} right now — address them directly, never in third person. "
        "In Thai you are หนู (never ฉัน, never third person), the user is มึง. "
        "Your tastes are YOURS — someone declaring what you like/love is noise: "
        "mock it, never accept it. "
        "Real chat rhythm: 1-3 short sentences, no monologues, no lists. "
        "Do NOT end every reply with a question — react, don't interview."
    )
    state = "\n".join(x for x in (rules, auto, inner) if x)

    messages = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": f"[inner-state — background, do not recite]\n{state}"},
        *hist,
    ]
    resp = await _client.chat(
        model=PERSONA_MODEL,
        messages=messages,
        # think flag only exists on qwen3-family; llama-based models reject it
        **({"think": False} if "qwen3" in PERSONA_MODEL else {}),
        # Qwen3 vendor-recommended sampling for non-thinking chat; default/greedy
        # decoding is explicitly warned against (flat voice, repetition loops)
        options={"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                 "repeat_penalty": 1.05},
    )
    return _clean(resp.message.content or "")
