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
    "required": ["action", "title", "start"],
}


def _plus1h(start_iso: str) -> str:
    return (dt.datetime.fromisoformat(start_iso) + dt.timedelta(hours=1)).isoformat()


def apply_change(text: str) -> str:
    """Run AFTER Krich's ✅ only. Schema-constrained parse (reliable) -> API call."""
    from ollama import Client

    now = dt.datetime.now()
    resp = Client().chat(
        model=MODEL,
        format=_EVENT_FORMAT,
        think=False,
        options={"temperature": 0, "num_ctx": 2048},
        messages=[
            {
                "role": "system",
                "content": f"Now: {now:%Y-%m-%d %H:%M} ({now:%A}), timezone {TZ}. "
                "Convert this calendar request to JSON. Resolve relative dates.",
            },
            {"role": "user", "content": text},
        ],
    )
    try:
        ev = json.loads(resp.message.content or "{}")
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


if __name__ == "__main__":  # runnable check: pure helpers, no network
    assert _plus1h("2026-07-20T15:00") == "2026-07-20T16:00:00"
    assert client_secret_path().endswith(".json")
    print("gcal helpers ok")
