"""One chat() for every pass. Provider = ollama (local) or openrouter (API).

Sync on purpose: async callers wrap it in asyncio.to_thread, and memory.extract
already runs in a thread. One code path beats two.

Normalized return, so pipeline.py never learns which provider it talked to:
    {"content": str, "tool_calls": [{"id","name","args"}], "raw": <msg to append>}
"""
import datetime
import json
import os
import time
from pathlib import Path

import httpx
from ollama import Client


_warned = set()  # shadowed keys already announced


def load_env():
    """Minimal .env loader (KEY=VALUE). Here, not in bot.py, so every entrypoint
    (bot, chat, tests, `-m tiwa.llm`) sees the same keys."""
    f = Path(__file__).parents[1] / ".env"
    if not f.exists():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            # setdefault means the SYSTEM environment wins. That is the usual
            # convention and it stays — but silently, it cost an evening: a
            # refreshed OPENROUTER_API_KEY in .env did nothing for hours because a
            # stale one sat in the Windows user environment, and every call 401'd
            # with the file looking correct. The dashboard's Settings tab writes
            # this file, so a shadowed key means the control panel is lying to you.
            # `k not in _warned`: voice.py calls load_env() again on purpose (it
            # must not depend on import order), so without this every shadowed
            # key announces itself twice.
            if k in os.environ and os.environ[k] != v and k not in _warned:
                _warned.add(k)
                print(f"[tiwa] {k} in .env is IGNORED — your system environment "
                      f"already sets it, and that wins. Clear it there, or edit it "
                      f"there instead of in .env.")
            os.environ.setdefault(k, v)


load_env()

LOCAL_MODEL = "huihui_ai/qwen3-abliterated:8b"

# One knob picks where each pass runs: (tools+extraction, persona)
#   local — everything on your GPU. no key, no cost, no network.
#   mixed — local hands (tools, memory) + API voice. the quality pick.
#   api   — nothing local. ollama can be closed; the GPU is free for games.
_MODES = {
    "local": ("ollama", "ollama"),
    # G2 measured: local wins tool calls 8/8 at 1.1s vs 7.4s. G1b measured: the
    # API writes her better. So local hands + API personality is the quality pick.
    # NOT called "voice" — that word belongs to the speech track (Whisper/TTS).
    "mixed": ("ollama", "openrouter"),
    # nothing local at all — for when the GPU is busy gaming or absent
    "api": ("openrouter", "openrouter"),
}
# dropped "hybrid" (api tools + local persona): G2 showed it is strictly worse
# than "mixed" — slower tool calls, same accuracy.
MODE = os.environ.get("TIWA_MODE", "local")
if MODE not in _MODES:
    # warn, don't die: a stale value used to brick every entrypoint including
    # the dashboard you would use to fix it.
    print(f"[tiwa] TIWA_MODE={MODE!r} unknown — using 'local'. "
          f"Pick one of: {', '.join(_MODES)}")
    MODE = "local"
PROVIDER, PERSONA_PROVIDER = _MODES[MODE]

TOOL_MODEL = os.environ.get("TIWA_TOOL_MODEL", "deepseek/deepseek-v4-flash")
EXTRACT_MODEL = os.environ.get("TIWA_EXTRACT_MODEL", "deepseek/deepseek-v4-flash")
# her voice on the API path — swap freely, this is the one worth shopping for
PERSONA_API_MODEL = os.environ.get("TIWA_PERSONA_API_MODEL", "deepseek/deepseek-v4-flash")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# runaway insurance, not budgeting: a looping bug should not bill all night.
# 2M tokens/day is ~$0.30 at V4 Flash rates and far beyond normal chat.
DAILY_TOKENS = int(os.environ.get("TIWA_DAILY_TOKENS", "2000000"))
SPEND_FILE = Path(__file__).parents[1] / "data" / "spend.json"

_ollama = Client()


def spend(add: int = 0) -> int:
    """Tokens used today. Resets on date change. Returns the running total."""
    today = datetime.date.today().isoformat()
    try:
        d = json.loads(SPEND_FILE.read_text())
    except Exception:
        d = {}
    if d.get("date") != today:
        d = {"date": today, "tokens": 0}
    if add:
        d["tokens"] += add
        SPEND_FILE.parent.mkdir(exist_ok=True)
        SPEND_FILE.write_text(json.dumps(d))
    return d["tokens"]


def _ollama_chat(model, messages, tools, fmt, options, think):
    resp = _ollama.chat(
        model=model or LOCAL_MODEL,
        messages=messages,
        **({"tools": tools} if tools else {}),
        **({"format": fmt} if fmt else {}),
        **({"think": think} if "qwen3" in (model or LOCAL_MODEL) else {}),
        options=options or {},
    )
    m = resp.message
    calls = [
        {"id": f"c{i}", "name": tc.function.name, "args": tc.function.arguments}
        for i, tc in enumerate(m.tool_calls or [])
    ]
    raw = {"role": "assistant", "content": m.content or ""}
    if m.tool_calls:  # ollama accepts the dict form back
        raw["tool_calls"] = [
            {"function": {"name": c["name"], "arguments": c["args"]}} for c in calls
        ]
    return {"content": m.content or "", "tool_calls": calls, "raw": raw}


def _openrouter_chat(model, messages, tools, fmt, options, think):
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("TIWA_PROVIDER=openrouter but OPENROUTER_API_KEY is unset")
    opts = options or {}
    body = {
        "model": model or TOOL_MODEL,
        "messages": messages,
        "temperature": opts.get("temperature", 0.7),
        "top_p": opts.get("top_p", 0.8),
        # measured from Thailand: these two cut ~40% off round-trip. Do NOT use
        # reasoning effort "minimal" — it turns reasoning ON (105 chars, 30 tok).
        # `think` used to be an ollama-only knob and silently did nothing here;
        # it now means the same thing on both providers. Off is still the default
        # for every pass a human waits on — measured 6.8x slower on the tool pass
        # for zero accuracy gain (scratch A/B, July 2026).
        "reasoning": {"enabled": bool(think)},
        "provider": {"sort": "throughput"},
    }
    if tools:
        body["tools"] = tools
    if fmt:
        # DeepSeek returns empty content unless the prompt literally says "json"
        assert any("json" in str(m.get("content", "")).lower() for m in messages), (
            "schema-constrained call needs the word 'json' in a prompt"
        )
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "out", "strict": True, "schema": fmt},
        }
    r = httpx.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {key}"},
        json=body,
        timeout=120,
    )
    if r.is_error:
        # raise_for_status() reports "401 Unauthorized" and discards the body,
        # where the provider says WHICH 401 this is. "User not found." (revoked or
        # wrong key) reads nothing like "Insufficient credits" or a rate limit, and
        # the difference is the whole diagnosis. Measured cost of not having it:
        # an evening spent on a key that was fine.
        why = ""
        try:
            why = (r.json().get("error") or {}).get("message", "")
        except Exception:
            why = r.text[:200]
        raise httpx.HTTPStatusError(
            f"{r.status_code} from OpenRouter: {why or r.reason_phrase}",
            request=r.request, response=r)
    j = r.json()
    spend((j.get("usage") or {}).get("total_tokens", 0))
    msg = j["choices"][0]["message"]
    calls = [
        # OpenAI-style args are a JSON *string*; _arg_name expects dict-or-str
        {"id": tc["id"], "name": tc["function"]["name"], "args": _loads(tc["function"]["arguments"])}
        for tc in msg.get("tool_calls") or []
    ]
    return {"content": msg.get("content") or "", "tool_calls": calls, "raw": msg}


def _loads(s):
    try:
        return json.loads(s) if isinstance(s, str) else s
    except json.JSONDecodeError:
        return s  # let _arg_name deal with junk


LOG_PROMPTS = os.environ.get("TIWA_LOG_PROMPTS", "1") != "0"


def chat(model=None, messages=None, tools=None, fmt=None, options=None, think=False,
         provider=None):
    """One call. `provider` overrides the mode default."""
    use_api = (provider or PROVIDER) == "openrouter"
    if use_api and DAILY_TOKENS and spend() >= DAILY_TOKENS:
        # over budget -> local, which needs ollama running. Loud, not silent.
        print(f"[tiwa] daily token ceiling hit ({spend()}) — falling back to local")
        use_api, model = False, None
    fn = _openrouter_chat if use_api else _ollama_chat
    before, t0 = spend(), time.perf_counter()
    out = fn(model, messages or [], tools, fmt, options, think)
    if LOG_PROMPTS:
        from . import memory  # late: memory has no llm import at module level

        req = "\n\n".join(
            f"[{m.get('role', '?')}]\n{m.get('content', '')}" for m in (messages or [])
        )
        resp = out["content"] or ""
        if out["tool_calls"]:
            resp += "\n[tool_calls] " + str(out["tool_calls"])
        memory.log_llm(
            "openrouter" if use_api else "ollama",
            model or (TOOL_MODEL if use_api else LOCAL_MODEL),
            (time.perf_counter() - t0) * 1000,
            spend() - before,
            req,
            resp,
        )
    return out


def tool_result_msg(call: dict, content: str) -> dict:
    """Tool output in whichever shape the active provider expects."""
    if PROVIDER == "openrouter":
        return {"role": "tool", "tool_call_id": call["id"], "content": content}
    return {"role": "tool", "tool_name": call["name"], "content": content}


if __name__ == "__main__":  # gate 1 check: every provider answers the same probe
    import sys
    import time

    print(f"TIWA_MODE={MODE}  tools/memory -> {PROVIDER}  her words -> {PERSONA_PROVIDER}\n")
    probe = [{"role": "user", "content": "Reply with exactly: ok"}]
    for p in sys.argv[1:] or ["ollama"]:
        model = None if p == "ollama" else TOOL_MODEL
        t0 = time.perf_counter()
        out = chat(model=model, messages=probe, options={"temperature": 0}, provider=p)
        ms = (time.perf_counter() - t0) * 1000
        assert out["content"].strip(), f"{p} returned empty content"
        print(f"{p:11} {ms:7.0f} ms  {model or LOCAL_MODEL:32} {out['content'].strip()[:40]}")
    print("provider layer ok")
