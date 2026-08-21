"""Control panel: every table fills, every button does what it says.

Runs against a throwaway .env and a throwaway db — your real settings and her
real memory are never touched. No browser and no server: the Gradio handlers are
plain functions, so the bench calls them the way the page does.

    py -X utf8 tests\\panelbench.py
"""
import asyncio
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, tools  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tiwa-panel-"))
db = memory.connect(str(TMP / "t.db"))
memory.connect = lambda *a, **k: db  # every handler gets the scratch db

import dashboard  # noqa: E402  (after the patch, so it never opens data/tiwa.db)

dashboard.ENV = TMP / ".env"
dashboard.ENV.write_text("DISCORD_TOKEN=secret-must-survive\nTIWA_MODE=mixed\n",
                         encoding="utf-8")

memory.remember(db, "Krich", "cousin of", "Steven")
memory.remember(db, "Steven", "plays", "guitar")
memory.remember(db, "ทิวา", "likes", "Gojo")
db.execute("INSERT INTO episodes(user, text, ts) VALUES(?,?,?)",
           ("Krich", "Krich promised to send the guitar recording", time.time()))
memory.log(db, "turn", "Krich: play bad apple -> เปิดให้ละ", 2300)
memory.log(db, "music", "playing Bad Apple!! (video)")
memory.log(db, "tool", "web_search('ผลบอลเมื่อคืน') -> Thairath [thairath.co.th]: ...", 640)
memory.log(db, "tool", "calendar_read('ignored') -> nothing next week", 210)
memory.log_llm("ollama", "qwen3", 900, 0, "[system]\nYou are ทิวา's inner thoughts",
               "you remember Krich")
memory.log_llm("openrouter", "deepseek", 700, 420,
               "[system]\n[inner-state — background", "เปิดให้ละ")
memory.log_llm("openrouter", "deepseek", 500, 300,
               "[system]\nYou are ทิวา's private memory judgment", '{"memories":[]}')
memory.log_llm("openrouter", "vl-8b", 800, 900,
               "[system]\nYou are describing an image for someone who is about to REACT",
               "a cat on a keyboard")
memory.log_llm("openrouter", "deepseek", 1100, 260,
               "[system]\nYou are ทิวา's memory settling while nobody is talking",
               "Krich only ever shows up to complain")
memory.log(db, "reflect", "Krich only ever shows up to complain", 1100)
# a reflection that concluded nothing: a watermark row, never an event
memory.reflect(db, "")
db.commit()


def count(table) -> int:
    return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def check(name, cond):
    print(f"| {name} | {'ok' if cond else 'FAIL'} |")
    assert cond, name


def refused(fn, *a):
    """A guard held: the handler raised instead of doing the thing."""
    try:
        fn(*a)
        return False
    except Exception:
        return True


print("| check | result |\n|---|---|")

# every panel fills, and says the thing it exists to say
now = dashboard.now_html()
check("now panel", "Mode" in now and "API tokens today" in now)
check("now counts her memory", ">3<" in now)  # three facts
health = dashboard.health_html()
check("health explains itself", "without it the bot cannot log in" in health)
check("health reads .env, not os.environ", "<b>Discord token</b>" in health)
check("health marks a set key ok", health.count("hrow ok") >= 2)
check("health states itself without colour alone",
      health.count("aria-label=") == 6)
check("activity plot has both axes",
      list(dashboard.activity_df().columns) == ["hour", "replies"])
check("recent music", "Bad Apple" in dashboard.music_df().to_string())

# the three passes, drawn left to right with their real median latency
passes = dashboard.passes_html()
check("passes name all three", all(p in passes
                                  for p in ("thinking", "her reply", "remembering")))
check("passes show a median", "MEDIAN" in passes.upper() and "0.9s" in passes)
check("passes count the calls", ">1<" in passes)
check("passes list the off-turn ones", "reflecting 1" in passes)

pf = dashboard.pass_df()
check("pass chart counts and times every pass",
      list(pf.columns) == ["pass", "calls", "median s"] and len(pf) >= 3)
people = dashboard.people_df()
check("people chart ranks by fact count", list(people.columns) == ["who", "facts"]
      and people["facts"].iloc[0] >= people["facts"].iloc[-1])

facts = dashboard.facts_df()
check("facts table", {"Krich", "Steven", "ทิวา"} <= set(facts["who"]))
check("facts carry their row id", sorted(facts["#"]) == sorted(
    r[0] for r in db.execute("SELECT rowid FROM relations")))
check("facts start unticked", not facts["forget"].any())
eps = dashboard.episodes_df()
# the blank watermark row must never show up as an episode she lived
check("blank watermark hidden", len(eps) == 1
      and "guitar recording" in eps["what"][0])

calls = dashboard.llm_df()
check("llm names the passes",
      {"thinking", "her reply", "remembering"} <= set(calls["pass"]))
check("llm names the vision pass", "seeing" in set(calls["pass"]))
check("llm names the reflection pass", "reflecting" in set(calls["pass"]))
check("llm rows carry their id", calls["#"].min() >= 1)

log = dashboard.log_df()
check("log has every kind", {"turn", "music", "tool", "reflect"} <= set(log["kind"]))
tool_rows = " ".join(log[log["kind"] == "tool"]["what"])
check("log shows the tool name", "web_search" in tool_rows)
check("log shows what she searched", "ผลบอลเมื่อคืน" in tool_rows)
check("log shows what came back", "thairath.co.th" in tool_rows)
check("log hides 'ignored' args", "ignored" not in tool_rows)

# click a model call open — by id, so a sorted or filtered table still works
row = calls[calls["pass"] == "seeing"].iloc[0]
asked, came = dashboard.open_call(type("E", (), {"row_value": list(row)})())
check("opening a call shows the prompt", "describing an image" in asked)
check("opening a call shows the reply", came == "a cat on a keyboard")

# exports
check("export memory.json", '"subject"' in
      Path(dashboard.export_memory()).read_text(encoding="utf-8"))
check("export memory.csv", "guitar" in
      Path(dashboard.export_facts()).read_text(encoding="utf-8-sig"))
check("export episodes.csv", "guitar recording" in
      Path(dashboard.export_episodes()).read_text(encoding="utf-8-sig"))
check("export llm.json", '"provider"' in
      Path(dashboard.export_llm()).read_text(encoding="utf-8"))
check("export log.csv", "bad apple" in
      Path(dashboard.export_log()).read_text(encoding="utf-8-sig").lower())
check("csv opens in Excel as Thai", Path(dashboard.export_facts())
      .read_bytes().startswith(b"\xef\xbb\xbf"))

# settings write, and only the keys the page owns
vals = ["api" if k == "TIWA_MODE" else "0.5" if k == "TIWA_MUSIC_VOLUME" else ""
        for k in dashboard.KEYS]
dashboard.save_settings(*vals)
env = dashboard.read_env()
check("saves editable keys",
      env["TIWA_MODE"] == "api" and env["TIWA_MUSIC_VOLUME"] == "0.5")
check("refuses to touch secrets", env["DISCORD_TOKEN"] == "secret-must-survive")
dashboard.write_env({"NOT_A_KEY": "x"})
check("refuses unknown keys", "NOT_A_KEY" not in dashboard.read_env())
check("reload shows what is on disk",
      dashboard.reload_settings()[dashboard.KEYS.index("TIWA_MODE")] == "api")

check("reset needs the tick", refused(dashboard.reset_settings, False)
      and dashboard.read_env()["TIWA_MODE"] == "api")
dashboard.reset_settings(True)
env = dashboard.read_env()
check("reset clears settings",
      "TIWA_MODE" not in env and "TIWA_MUSIC_VOLUME" not in env)
check("reset keeps secrets", env["DISCORD_TOKEN"] == "secret-must-survive")
check("secrets are never rendered", "secret-must-survive" not in dashboard.secrets_md()
      and "✅ set" in dashboard.secrets_md())

# forget ticked rows. The browser sorts and filters the table itself, so the
# handler must go by row id — shuffle the frame to prove position is not used.
facts = dashboard.facts_df().sample(frac=1, random_state=0)
facts.loc[facts["about"] == "Steven", "forget"] = True
dashboard.forget_facts(facts)
check("forget ticked facts, whatever the row order", count("relations") == 2
      and "Steven" not in set(dashboard.facts_df()["about"]))
untouched = dashboard.facts_df()
dashboard.forget_facts(untouched)
check("nothing ticked forgets nothing", count("relations") == 2)
eps = dashboard.episodes_df()
eps.loc[:, "forget"] = True
dashboard.forget_episodes(eps)
check("forget ticked episodes", count("episodes") == 1)  # the watermark stays

# reset buttons, each behind its own tick
check("wipe needs the tick", refused(dashboard.wipe, "facts", False)
      and count("relations") == 2)
dashboard.wipe("facts", True)
check("wipe facts", count("relations") == 0)
dashboard.wipe("episodes", True)
check("wipe episodes", count("episodes") == 0)
dashboard.wipe("llm", True)
check("wipe model log", count("llm_log") == 0)
dashboard.wipe("log", True)
check("wipe activity log", count("log") == 0)
check("wipe whitelist holds", refused(dashboard.wipe, "entities", True)
      and count("entities") >= 1)

# the chat tab, offline: her brain is stubbed, the glue around it is the point
seen = {}


async def fake_respond(db_, hist, author, text, images=()):
    seen["hist"] = [dict(m) for m in hist]
    tools.DJ.append(("queue", "lofi"))
    tools.PENDING_CALENDAR.append("lunch tomorrow")
    return "เล่นให้ละ"


dashboard.pipeline.respond = fake_respond
memory.extract = lambda *a, **k: seen.__setitem__("extracted", True)

history = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
reply = asyncio.run(dashboard.chat_fn("เปิดเพลง", history, "Krich", False))
check("chat labels your lines with your name", seen["hist"][0]["content"] == "Krich: hi")
check("chat leaves her own lines alone", seen["hist"][1]["content"] == "yo")
check("chat sends the new message", seen["hist"][-1]["content"] == "Krich: เปิดเพลง")
check("chat says what only Discord can do",
      "queue lofi" in reply and "lunch tomorrow" in reply)
check("chat drains the pending queues",
      tools.DJ == [] and tools.PENDING_CALENDAR == []
      and tools.PENDING_MUSIC is None)
check("chat can talk without remembering", "extracted" not in seen)
asyncio.run(dashboard.chat_fn("hi", [], "Krich", True))
check("chat remembers when asked", seen.get("extracted") is True)

print("\npanel ok — 6 tabs, tables, exports, saves, resets, chat")
