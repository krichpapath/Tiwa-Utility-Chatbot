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


def prepare_change(text: str) -> dict:
    """Parse and validate once, before asking the human to approve exact details."""
    from . import llm
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=7)))
    resp = llm.chat(
        model=llm.EXTRACT_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[{"role": "system", "content":
                   f"Now: {now:%Y-%m-%d %H:%M} ({now:%A}), timezone {TZ}. "
                   "Convert this calendar request to JSON. Resolve relative dates. "
                   "Only add or cancel. Do not guess missing dates or titles; return empty fields."},
                  {"role": "user", "content": text}],
        fmt=_EVENT_FORMAT, options={"temperature": 0, "num_ctx": 2048})
    return validate_change(json.loads(resp["content"] or "{}"))


def validate_change(ev: dict) -> dict:
    if not isinstance(ev, dict) or ev.get("action") not in ("add", "cancel"):
        raise ValueError("calendar action must be add or cancel")
    if not isinstance(ev.get("title"), str) or not ev["title"].strip():
        raise ValueError("calendar title is required")
    if len(ev["title"]) > 200:
        raise ValueError("calendar title must be at most 200 characters")
    start = dt.datetime.fromisoformat(ev["start"])
    if "T" not in ev["start"]:
        raise ValueError("calendar start needs date and time")
    end = dt.datetime.fromisoformat(ev.get("end") or _plus1h(ev["start"]))
    if end <= start:
        raise ValueError("calendar end must be after start")
    return dict(action=ev["action"], title=ev["title"].strip(),
                start=start.isoformat(), end=end.isoformat())


def describe_change(ev: dict) -> str:
    return f"{ev['action']}: {ev['title']} | {ev['start']} → {ev['end']} ({TZ})"


def apply_change(text) -> str:
    """Apply the exact approved plan. String input supports older direct callers.

    Interactive surfaces pass a validated dict so approval never triggers a
    second model interpretation. Provider, parse and API failures return errors.
    """

    try:
        ev = prepare_change(text) if isinstance(text, str) else validate_change(text)
        svc = _service()
        if ev["action"] == "cancel":
            hits = (
                svc.events()
                .list(calendarId="primary", q=ev["title"], singleEvents=True, maxResults=250,
                      timeMin=_aware(ev["start"]).isoformat(),
                      timeMax=(_aware(ev["start"]) + dt.timedelta(seconds=1)).isoformat())
                .execute()
                .get("items", [])
            )
            hits = [hit for hit in hits if hit.get("summary", "").casefold() == ev["title"].casefold()
                    and hit.get("start", {}).get("dateTime")
                    and _aware(hit["start"]["dateTime"]) == _aware(ev["start"])]
            if not hits:
                return f"couldn't find '{ev['title']}' to cancel"
            if len(hits) != 1:
                return "calendar change refused: multiple matching events; cancel in Google Calendar"
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


def _aware(value: str):
    stamp = dt.datetime.fromisoformat(value)
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=dt.timezone(dt.timedelta(hours=7)))


if __name__ == "__main__":  # runnable check: pure helpers + schema, no network
    import inspect

    assert _plus1h("2026-07-20T15:00") == "2026-07-20T16:00:00"
    assert client_secret_path().endswith(".json")

    # every model call goes through llm.chat, so `api` mode needs nothing local
    # and the call lands in the model-call log
    src = inspect.getsource(prepare_change)
    assert "llm.chat" in src, "apply_change must route through llm.chat"
    assert "from ollama import" not in src, "apply_change bypasses the provider layer"
    assert "json" in src.lower(), "schema-constrained call needs 'json' in the prompt"

    # OpenRouter sends this as a strict json_schema: all properties required, no extras
    props = set(_EVENT_FORMAT["properties"])
    assert set(_EVENT_FORMAT["required"]) == props, "strict schema needs all keys required"
    assert _EVENT_FORMAT["additionalProperties"] is False
    print(f"gcal ok — helpers, llm.chat routing, strict schema ({len(props)} fields)")
