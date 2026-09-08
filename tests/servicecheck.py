"""Read-only live service probes. No Discord messages or calendar writes."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from tiwa import gcal, llm, music


def main():
    results = {}
    def check(name, fn):
        try:
            results[name] = fn()
        except Exception as error:
            results[name] = {"error": type(error).__name__, "detail": str(error)[:250]}
        print(name, json.dumps(results[name], ensure_ascii=False), flush=True)

    def discord_probe():
        headers = {"Authorization": "Bot " + os.environ["DISCORD_TOKEN"]}
        with httpx.Client(base_url="https://discord.com/api/v10", headers=headers, timeout=20) as client:
            me = client.get("/users/@me")
            me.raise_for_status()
            channel = client.get("/channels/" + os.environ["TIWA_HOME_CHANNEL"])
            channel.raise_for_status()
            app = client.get("/oauth2/applications/@me")
            app.raise_for_status()
            return {"bot": me.json()["username"], "channel": channel.json()["id"],
                    "channel_type": channel.json()["type"],
                    "owner_id": (app.json().get("owner") or {}).get("id")}

    check("discord", discord_probe)
    check("provider", lambda: llm.chat(messages=[{"role": "user", "content": "Reply exactly: ok"}],
                                       options={"temperature": 0})["content"])
    check("calendar", lambda: "available" if not (s := gcal.upcoming(1)).startswith("calendar unavailable") else s)
    check("music_runtime", music.ready)
    check("music_search", lambda: {k: v for k, v in music.find("Mili Hero official").items() if k == "title"})
    from tiwa import tools, memory
    check("web_search", lambda: tools.web_search(memory.connect(":memory:"), "Python official documentation")[:400])
    out = Path(__file__).resolve().parents[1] / "qa-results" / "acceptance" / "services.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
