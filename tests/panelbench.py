"""Control panel: every page renders, every button does what it says.

Runs against a throwaway .env and a throwaway db — your real settings and her
real memory are never touched.

    py -X utf8 tests\\panelbench.py
"""
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="tiwa-panel-"))
db = memory.connect(str(TMP / "t.db"))
memory.connect = lambda *a, **k: db  # every page and POST gets the scratch db

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
memory.log_llm("openrouter", "vl-8b", 800, 900,  # not "qwen3-*": the provider
                                                # filter check below greps for it
               "[system]\nYou are describing an image for someone who is about to REACT",
               "a cat on a keyboard")
memory.log_llm("openrouter", "deepseek", 1100, 260,
               "[system]\nYou are ทิวา's memory settling while nobody is talking",
               "Krich only ever shows up to complain")
memory.log(db, "reflect", "Krich only ever shows up to complain", 1100)
# a reflection that concluded nothing: a watermark row, never an event
memory.reflect(db, "")
db.commit()

srv = HTTPServer(("127.0.0.1", 8788), dashboard.H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8788"


def get(path):
    with urllib.request.urlopen(BASE + path) as r:
        return r.read().decode("utf-8")


def post(**form):
    data = urllib.parse.urlencode(form).encode()
    with urllib.request.urlopen(BASE + "/", data=data) as r:
        return r.read().decode("utf-8")


def check(name, cond):
    print(f"| {name} | {'ok' if cond else 'FAIL'} |")
    assert cond, name


print("| check | result |\n|---|---|")

# every page renders, and says the thing it exists to say
check("status page", "Mode:" in get("/?view=status")
      and "API tokens today" in get("/?view=status"))
s = get("/?view=settings")
check("settings explain themselves", "Runaway insurance" in s and "default:" in s)
check("secrets never rendered", "secret-must-survive" not in s and "missing" in s)
check("music setting present", "TIWA_MUSIC_VOLUME" in s)
m = get("/?view=memory")
check("memory groups by subject", "Krich" in m and "Steven" in m and "Gojo" in m)
check("memory search filters", "Gojo" not in get("/?view=memory&q=guitar"))
lg = get("/?view=llm")
check("llm page names the passes", "thinking" in lg and "her reply" in lg
      and "remembering" in lg)
check("llm names the vision pass", "seeing" in lg and "cat on a keyboard" in lg)
check("llm pass filter", "private memory judgment"
      not in get("/?view=llm&pass=thinking"))
check("vision filters to itself",
      "memory judgment" not in get("/?view=llm&pass=seeing"))
check("llm names the reflection pass", "reflecting" in lg and "only ever shows up" in lg)
check("reflection filters to itself",
      "memory judgment" not in get("/?view=llm&pass=reflecting"))
check("llm provider filter", "qwen3" not in get("/?view=llm&prov=openrouter"))
check("log explains the reflect kind", "idle-time conclusion" in get("/?view=log"))
# the blank watermark row must never render as an episode she lived
mem = get("/?view=memory")
check("blank watermark hidden", "1 of 1 episodes" in mem
      and "guitar recording" in mem)
check("log kind filter", "Bad Apple" not in get("/?view=log&kind=turn"))
check("log text search", "Bad Apple" in get("/?view=log&q=bad"))

# the whole point of the tool rows: which tool, and what she typed into it
lt = get("/?view=log&kind=tool")
check("log shows the tool name", ">web_search</a>" in lt)
check("log shows what she searched", "<code>ผลบอลเมื่อคืน</code>" in lt)
check("log shows what came back", "thairath.co.th" in lt)
check("log hides 'ignored' args", "<code>ignored</code>" not in lt)
check("tool name filters to itself",
      "calendar_read" not in get("/?view=log&kind=tool&q=web_search"))

# exports
check("export memory.json", '"subject"' in get("/export/memory.json"))
check("export memory.csv", "guitar" in get("/export/memory.csv"))
check("export episodes.csv", "guitar recording" in get("/export/episodes.csv"))
check("export llm.json", '"provider"' in get("/export/llm.json"))
check("export log.csv", "bad apple" in get("/export/log.csv").lower())

# settings write, and only the keys the page owns
post(action="settings", TIWA_MODE="api", TIWA_MUSIC_VOLUME="0.5",
     DISCORD_TOKEN="hacked", NOT_A_KEY="x")
env = dashboard.read_env()
check("saves editable keys", env["TIWA_MODE"] == "api"
      and env["TIWA_MUSIC_VOLUME"] == "0.5")
check("refuses to touch secrets", env["DISCORD_TOKEN"] == "secret-must-survive")
check("refuses unknown keys", "NOT_A_KEY" not in env)

post(action="reset_settings")
env = dashboard.read_env()
check("reset clears settings", "TIWA_MODE" not in env
      and "TIWA_MUSIC_VOLUME" not in env)
check("reset keeps secrets", env["DISCORD_TOKEN"] == "secret-must-survive")

# per-row forget
rid = db.execute("SELECT rowid FROM relations LIMIT 1").fetchone()[0]
post(action="del_rel", id=str(rid))
check("forget one fact", db.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 2)

# reset buttons
post(action="wipe", what="facts")
check("wipe facts", db.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 0)
post(action="wipe", what="episodes")
check("wipe episodes", db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0] == 0)
post(action="wipe", what="llm")
check("wipe model log", db.execute("SELECT COUNT(*) FROM llm_log").fetchone()[0] == 0)
post(action="wipe", what="log")
check("wipe activity log", db.execute("SELECT COUNT(*) FROM log").fetchone()[0] == 0)
post(action="wipe", what="entities")  # not on the whitelist
check("wipe whitelist holds",
      db.execute("SELECT COUNT(*) FROM entities").fetchone()[0] >= 1)

srv.shutdown()
print("\npanel ok — 5 pages, filters, exports, saves, resets")
