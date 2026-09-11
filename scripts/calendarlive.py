"""Manual live calendar acceptance. Creates two QA events, deletes one, leaves one visible."""

import datetime as dt
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import gcal

stamp = uuid.uuid4().hex[:8]
start = (dt.datetime.now(dt.timezone(dt.timedelta(hours=7))) + dt.timedelta(days=1)).replace(
    hour=18, minute=0, second=0, microsecond=0
)
end = start + dt.timedelta(minutes=15)
report = {"run": stamp, "checks": [], "events": []}
path = gcal.DATA_DIR / "calendar-acceptance.json"


def save():
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def check(name, passed):
    report["checks"].append({"test": name, "passed": bool(passed)})
    save()
    print(name, "PASS" if passed else "FAIL", flush=True)
    assert passed, name


def locate(title, at):
    hits = (
        gcal._service()
        .events()
        .list(
            calendarId="primary",
            q=title,
            timeMin=at.isoformat(),
            timeMax=(at + dt.timedelta(hours=1)).isoformat(),
            singleEvents=True,
        )
        .execute()
        .get("items", [])
    )
    return [e for e in hits if e.get("summary") == title and e.get("status") != "cancelled"]


for role in ["Visible", "Delete me"]:
    title = f"Tiwa QA {stamp} — {role}"
    plan = dict(action="add", title=title, start=start.isoformat(), end=end.isoformat())
    result = gcal.apply_change(plan)
    check("create " + role, result.startswith("added:"))
    hits = locate(title, start)
    check("read back " + role, len(hits) == 1)
    event = hits[0]
    report["events"].append(
        {"id": event["id"], "title": title, "url": event.get("htmlLink"), "deleted": False}
    )
    save()
    if role == "Visible":
        edited = title + " — edited successfully"
        new_start = start + dt.timedelta(minutes=30)
        result = gcal.apply_change(
            dict(plan, action="edit", new_title=edited, new_start=new_start.isoformat())
        )
        check("edit title and move", result.startswith("edited:"))
        current = gcal._service().events().get(calendarId="primary", eventId=event["id"]).execute()
        check(
            "verify persisted edit",
            current["summary"] == edited and gcal._aware(current["start"]["dateTime"]) == new_start,
        )
        check(
            "preserve 15 minute duration",
            gcal._aware(current["end"]["dateTime"]) - gcal._aware(current["start"]["dateTime"])
            == dt.timedelta(minutes=15),
        )
        report["events"][-1].update(
            title=edited, start=current["start"]["dateTime"], url=current.get("htmlLink")
        )
        check("recall via upcoming", edited in gcal.upcoming())
    else:
        result = gcal.apply_change(dict(plan, action="cancel"))
        check("delete disposable event", result.startswith("cancelled:"))
        check("verify absent after deletion", not locate(title, start))
        report["events"][-1]["deleted"] = True
        save()
check(
    "invalid event rejected",
    gcal.apply_change(dict(action="add", title="", start=start.isoformat())).startswith(
        "calendar change failed:"
    ),
)
print(json.dumps([e for e in report["events"] if not e["deleted"]], ensure_ascii=False), flush=True)
