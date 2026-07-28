"""One chat() for every pass. Provider = ollama (local) or openrouter (API).

Sync on purpose: async callers wrap it in asyncio.to_thread, and memory.extract
already runs in a thread. One code path beats two.

Normalized return, so pipeline.py never learns which provider it talked to:
    {"content": str, "tool_calls": [{"id","name","args"}], "raw": <msg to append>}
"""
import json
import os

import httpx
from ollama import Client

PROVIDER = os.environ.get("TIWA_PROVIDER", "ollama")
LOCAL_MODEL = "huihui_ai/qwen3-abliterated:8b"

# per-pass knobs: persona stays local by default even when PROVIDER=openrouter
TOOL_MODEL = os.environ.get("TIWA_TOOL_MODEL", "deepseek/deepseek-v4-flash")
EXTRACT_MODEL = os.environ.get("TIWA_EXTRACT_MODEL", "deepseek/deepseek-v4-flash")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_ollama = Client()


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
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
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


def chat(model=None, messages=None, tools=None, fmt=None, options=None, think=False,
         provider=None):
    """One call. `provider` overrides the env default (persona pins itself local)."""
    fn = _openrouter_chat if (provider or PROVIDER) == "openrouter" else _ollama_chat
    return fn(model, messages or [], tools, fmt, options, think)


def tool_result_msg(call: dict, content: str) -> dict:
    """Tool output in whichever shape the active provider expects."""
    if PROVIDER == "openrouter":
        return {"role": "tool", "tool_call_id": call["id"], "content": content}
    return {"role": "tool", "tool_name": call["name"], "content": content}


if __name__ == "__main__":  # gate 1 check: both providers answer the same probe
    import sys

    probe = [{"role": "user", "content": "Reply with exactly: ok"}]
    for p in sys.argv[1:] or ["ollama"]:
        out = chat(
            model=None if p == "ollama" else TOOL_MODEL,
            messages=probe,
            options={"temperature": 0},
            provider=p,
        )
        assert out["content"].strip(), f"{p} returned empty content"
        print(f"{p}: {out['content'].strip()[:60]}")
    print("provider layer ok")
