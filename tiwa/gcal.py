"""Google Calendar for Krich — reads free, writes only after Discord ✅.

One-time auth: py -X utf8 gcal_auth.py  (browser consent -> data/gcal_token.json)
"""
import datetime as dt
import glob
import json
from pathlib import Path

from .memory import DATA_DIR, MODEL

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TOKEN = DATA_DIR / "gcal_token.json"
TZ = "Asia/Bangkok"


def client_secret_path() -> str:
    hits = glob.glob(str(Path(__file__).parents[1] / "client_secret*.json"))
    if not hits:
        raise FileNotFoundError("no client_secret*.json in project root")
    return hits[0]


def _service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    if not TOKEN.exists():
        raise RuntimeError("no calendar auth — run: py -X utf8 gcal_auth.py")
    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN.write_text(creds.to_json())
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def upcoming(days: int = 7) -> str:
    """Next `days` of events, one line each — the calendar_read tool."""
    try:
        now = dt.datetime.now(dt.timezone.utc)
        events = (
            _service()
            .events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=(now + dt.timedelta(days=days)).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            )
            .execute()
            .get("items", [])
        )
    except Exception as e:  # no token / network — brief reports it, turn survives
        return f"calendar unavailable: {e}"
    if not events:
        return f"calendar empty for the next {days} days"
    return "\n".join(
        f"{ev['start'].get('dateTime', ev['start'].get('date'))} — "
        f"{ev.get('summary', '(no title)')}"
        for ev in events
    )


_EVENT_FORMAT = {
    "type": "object",
    "properties": {
        # ponytail: add + cancel only; "move X" = cancel then add, ask Krich to rephrase
        "action": {"type": "string", "enum": ["add", "cancel"]},
        "title": {"type": "string"},
        "start": {"type": "string", "description": "ISO 8601 local, e.g. 2026-07-20T15:00"},
        "end": {"type": ["string", "null"]},
    },
    # every property required + no extras: OpenRouter sends this as a STRICT json_schema
    # and rejects a schema whose properties are not all required. "end" may be null.
    "required": ["action", "title", "start", "end"],
    "additionalProperties": False,
}


def _plus1h(start_iso: str) -> str:
    return (dt.datetime.fromisoformat(start_iso) + dt.timedelta(hours=1)).isoformat()


def apply_change(text: str) -> str:
    """Run AFTER Krich's ✅ only. Schema-constrained parse (reliable) -> API call.

    Goes through llm.chat like every other model call: it used to build an ollama
    client directly, which meant a confirmed calendar write needed ollama running
    even in `api` mode — the one mode whose point is needing nothing local — and
    the call never showed up in the model-call log.
    """
    from . import llm

    now = dt.datetime.now()
    resp = llm.chat(
        # same per-pass model choice as memory.extract: this is an extraction job
        model=llm.EXTRACT_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[
            {
                "role": "system",
                # the word "JSON" is load-bearing: DeepSeek returns empty content
                # without it, and llm.chat asserts it is present
                "content": f"Now: {now:%Y-%m-%d %H:%M} ({now:%A}), timezone {TZ}. "
                "Convert this calendar request to JSON. Resolve relative dates.",
            },
            {"role": "user", "content": text},
        ],
        fmt=_EVENT_FORMAT,
        options={"temperature": 0, "num_ctx": 2048},
    )
    try:
        ev = json.loads(resp["content"] or "{}")
        svc = _service()
        if ev["action"] == "cancel":
            hits = (
                svc.events()
                .list(calendarId="primary", q=ev["title"], singleEvents=True, maxResults=1,
                      timeMin=dt.datetime.now(dt.timezone.utc).isoformat())
                .execute()
                .get("items", [])
            )
            if not hits:
                return f"couldn't find '{ev['title']}' to cancel"
            svc.events().delete(calendarId="primary", eventId=hits[0]["id"]).execute()
            return f"cancelled: {hits[0].get('summary', ev['title'])}"
        created = (
            svc.events()
            .insert(
                calendarId="primary",
                body={
                    "summary": ev["title"],
                    "start": {"dateTime": ev["start"], "timeZone": TZ},
                    "end": {"dateTime": ev.get("end") or _plus1h(ev["start"]), "timeZone": TZ},
                },
            )
            .execute()
        )
        return f"added: {created.get('summary')} @ {ev['start']}"
    except Exception as e:
        return f"calendar change failed: {e}"


if __name__ == "__main__":  # runnable check: pure helpers + schema, no network
    import inspect

    assert _plus1h("2026-07-20T15:00") == "2026-07-20T16:00:00"
    assert client_secret_path().endswith(".json")

    # every model call goes through llm.chat, so `api` mode needs nothing local
    # and the call lands in the model-call log
    src = inspect.getsource(apply_change)
    assert "llm.chat" in src, "apply_change must route through llm.chat"
    assert "from ollama import" not in src, "apply_change bypasses the provider layer"
    assert "json" in src.lower(), "schema-constrained call needs 'json' in the prompt"

    # OpenRouter sends this as a strict json_schema: all properties required, no extras
    props = set(_EVENT_FORMAT["properties"])
    assert set(_EVENT_FORMAT["required"]) == props, "strict schema needs all keys required"
    assert _EVENT_FORMAT["additionalProperties"] is False
    print(f"gcal ok — helpers, llm.chat routing, strict schema ({len(props)} fields)")
