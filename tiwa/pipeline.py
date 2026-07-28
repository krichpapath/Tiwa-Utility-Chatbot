"""Two-pass turn: inner reasoning (tool registry) -> persona reply. Phase 3."""
import asyncio
import datetime
import os
import re
from pathlib import Path

from . import llm, memory, tools
from .memory import MODEL, TIWA

PERSONA = (Path(__file__).parents[1] / "prompts" / "tiwa.md").read_text(encoding="utf-8")

# her voice can run on a different model than the tool/extraction passes
PERSONA_MODEL = os.environ.get("TIWA_PERSONA_MODEL", MODEL)
# persona NEVER goes to an API provider — hosted filters flatten her escalation
# and coercion refusals. Tools/extraction may; her voice may not.
PERSONA_PROVIDER = "ollama"

_INNER_SYSTEM = f"""You are {TIWA}'s inner thoughts, run before she replies. You are NOT the reply.
The brief is about the LAST message only; earlier lines exist only to resolve who "he/she/it" means.
Tools: recall memory for each person/topic that matters (most turns need ONLY recall);
web_search / calendar tools only when their descriptions clearly apply to the message.
Then output a short plain-text brief (max 5 lines) addressed to her as "you":
- what you actually know or feel about them (memory or tool results only — NEVER invent),
  e.g. "you remember Steven: Krich's cousin, plays guitar"
- what you have no memory of (so you ask instead of bluffing), e.g. "no memory of Steven — ask"
Nothing else. No greetings, no reply draft, no lines about the speaker's own knowledge."""


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


# Qwen3 warns against greedy decoding (repetition loops) — low temp, not 0
_INNER_OPTS = {"temperature": 0.3, "top_p": 0.8, "top_k": 20, "num_ctx": 4096}


async def _tool_chat(db, msgs: list) -> str:
    """Shared tool loop: chat with the registry until the model outputs text."""
    model = llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL
    for _ in range(3):  # ponytail: max 3 tool rounds, plenty for one message
        resp = await asyncio.to_thread(
            llm.chat,
            model=model,
            messages=msgs,
            tools=[t["schema"] for t in tools.TOOLS.values()],
            options=_INNER_OPTS,
        )
        msgs.append(resp["raw"])
        if not resp["tool_calls"]:
            return _clean(resp["content"])
        for tc in resp["tool_calls"]:
            t = tools.TOOLS.get(tc["name"])
            out = (
                # to_thread: web/calendar tools block on network
                await asyncio.to_thread(t["fn"], db, _arg_name(tc["args"]))
                if t
                else "unknown tool"  # models sometimes invent tool names
            )
            msgs.append(llm.tool_result_msg(tc, out))
    # tool rounds exhausted — force a brief from what was gathered so far
    resp = await asyncio.to_thread(llm.chat, model=model, messages=msgs, options=_INNER_OPTS)
    return _clean(resp["content"])


async def _inner_brief(db, author: str, text: str, recent: str = "") -> str:
    prefix = f"earlier lines (context only):\n{recent}\n\n" if recent else ""
    msgs = [
        {"role": "system", "content": _INNER_SYSTEM},
        {"role": "user", "content": f"{prefix}{author}: {text}"},
    ]
    return await _tool_chat(db, msgs)


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
    resp = await asyncio.to_thread(
        llm.chat,
        model=PERSONA_MODEL,
        messages=messages,
        # Qwen3 vendor-recommended sampling for non-thinking chat; default/greedy
        # decoding is explicitly warned against (flat voice, repetition loops)
        options={"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                 "repeat_penalty": 1.05},
        provider=PERSONA_PROVIDER,
    )
    return _clean(resp["content"])


_IDLE_SYSTEM = f"""You are {TIWA}'s idle thoughts. No one is talking to her right now.
Most idles she has nothing worth saying: output exactly NOTHING.
Only if recent memory (or a web_search about something she genuinely cares about)
gives a real reason, output one short thought she'd share unprompted.
Never greet, never "just checking in", never summarize the episodes back."""


async def idle(db) -> str:
    """Heartbeat turn: usually returns "" (stay quiet), sometimes an unprompted message."""
    eps = memory.recent_episodes(db)
    if not eps:
        return ""  # nothing lived yet = nothing to say; 8B won't stay quiet on its own
    now = datetime.datetime.now()
    msgs = [
        {"role": "system", "content": _IDLE_SYSTEM},
        {"role": "user", "content": f"time: {now:%A %H:%M}\nrecent episodes:\n{eps}"},
    ]
    thought = await _tool_chat(db, msgs)
    if not thought or "NOTHING" in thought[:30].upper():
        return ""
    resp = await asyncio.to_thread(
        llm.chat,
        model=PERSONA_MODEL,
        messages=[
            {"role": "system", "content": PERSONA},
            {
                "role": "system",
                "content": "[inner-state — background, do not recite]\n"
                f"You feel like saying this to the channel, unprompted: {thought}\n"
                "Say it in your voice, 1-2 short sentences, same language as the thought.",
            },
        ],
        options={"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                 "repeat_penalty": 1.05},
        provider=PERSONA_PROVIDER,
    )
    return _clean(resp["content"])
