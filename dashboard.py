"""Tiwa control panel — status, settings, memory, model calls, activity log.

    py -X utf8 dashboard.py [--open]      ->  http://127.0.0.1:8787

Localhost-only by design: it edits .env and deletes memory, so it must never be
reachable off this machine. Secret VALUES are never rendered — only set/missing.
"""
import csv
import datetime
import html
import io
import json
import re
import socket
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from tiwa import llm, memory

ENV = Path(__file__).with_name(".env")
DATA = Path(__file__).with_name("data")
PORT = 8787
VIEWS = ("status", "settings", "memory", "llm", "log")

# Every editable knob, with the plain-English answer to "what is this and what
# happens if I leave it alone". (key, label, help, default, options|None)
SETTINGS = [
    ("Where she thinks", [
        ("TIWA_MODE", "Mode",
         "local = everything on your GPU, ollama must be running, no cost. "
         "mixed = tools and memory local, her replies from the API (best quality). "
         "api = nothing local at all, GPU free for games.",
         "local", ["local", "mixed", "api"]),
        ("TIWA_PERSONA_API_MODEL", "Her voice (API)",
         "The OpenRouter model that writes her actual replies in mixed/api mode. "
         "This is the one worth shopping around for — it decides how she sounds.",
         "deepseek/deepseek-v4-flash", None),
        ("TIWA_TOOL_MODEL", "Tool model (API)",
         "Picks which tools to call (recall, play music, calendar) when tools run "
         "on the API — that is api mode only. Unused in local and mixed.",
         "deepseek/deepseek-v4-flash", None),
        ("TIWA_EXTRACT_MODEL", "Memory model (API)",
         "Decides what she keeps in memory after each turn, when that runs on the "
         "API (api mode only).",
         "deepseek/deepseek-v4-flash", None),
        ("TIWA_PERSONA_MODEL", "Force a reply model",
         "Overrides her voice model on whichever path is active. Leave empty "
         "unless you are testing one specific model.",
         "auto", None),
        ("TIWA_DAILY_TOKENS", "Daily token ceiling",
         "Runaway insurance, not a budget. Past this she falls back to local for "
         "the rest of the day and says so in the console. 2,000,000 is about "
         "$0.30 and far more than a day of chatting.",
         "2000000", None),
    ]),
    ("Discord", [
        ("TIWA_HOME_CHANNEL", "Home channel id",
         "The one channel she may speak in unprompted — at most once every 3 "
         "hours, and only between 09:00 and 23:00. Empty = she never starts a "
         "conversation, she only answers.",
         "off", None),
    ]),
    ("Music", [
        ("TIWA_MUSIC_VOLUME", "Volume",
         "1.0 is however loud YouTube handed it over. Set 0.4-0.6 so you can "
         "still hear her talk while a song is playing.",
         "1.0", None),
        ("TIWA_MAX_TRACK_MIN", "Longest track (minutes)",
         "YouTube's top hit for a mood like 'hype gaming EDM' is a 2-3 hour mix, "
         "which outlives the whole conversation and starves the queue. Anything "
         "longer than this is skipped in favour of the next result. Raise it if "
         "you actually want long mixes.",
         "12", None),
    ]),
    ("Eyes", [
        ("TIWA_VISION", "See images",
         "1 = she looks at any image posted with her name and reacts to it. "
         "0 = images are ignored completely and she says she cannot see them. "
         "Costs nothing on messages with no picture — no image, no call.",
         "1", ["1", "0"]),
        ("TIWA_VISION_MODEL", "Vision model",
         "Always an API model, even in local mode: her 8B and a local vision "
         "model do not fit in 8 GB together, so ollama would swap on every "
         "image. About $0.00015 a look at the default.",
         "qwen/qwen3-vl-8b-instruct", None),
    ]),
    ("Her speaking voice", [
        ("TIWA_TTS_TH", "Thai voice",
         "edge-tts voice for Thai replies. Others: th-TH-NiwatNeural (male).",
         "th-TH-PremwadeeNeural", None),
        ("TIWA_TTS_EN", "English voice",
         "edge-tts voice for English replies. Others: en-US-JennyNeural, "
         "en-GB-SoniaNeural.",
         "en-US-AvaNeural", None),
    ]),
    ("Her ears — speech-to-text (off on purpose)", [
        ("TIWA_LISTEN", "Listen in voice chat",
         "1 = transcribe voice chat and answer lines that START with 'Hey Tiwa'. "
         "Currently 0: Thai accuracy on CPU was too poor to be useful. Everything "
         "below only matters when this is 1.",
         "0", ["0", "1"]),
        ("TIWA_WHISPER_MODEL", "Speech model",
         "whisper-base is roughly realtime, English fine, Thai rough. "
         "whisper-small is ~3x slower but much better at Thai.",
         "onnx-community/whisper-base",
         ["onnx-community/whisper-base", "onnx-community/whisper-small"]),
        ("TIWA_WHISPER_LANG", "Language",
         "Pin it if you always speak one language. Empty = auto-detect, which is "
         "slower and sometimes returns nothing at all.",
         "auto", ["", "th", "en"]),
        ("TIWA_ONNX_THREADS", "CPU threads",
         "4 measured fastest on this machine — 2.3x faster than letting it "
         "choose. More threads is slower, not faster.",
         "4", None),
        ("TIWA_NOISE_FLOOR", "Noise gate",
         "Anything quieter than this is not speech. Measured here: speech 0.09, "
         "quiet speech 0.023, fan hum 0.021, room hiss 0.002. Raise it if she "
         "hears ghosts, lower it if she misses you.",
         "0.02", None),
        ("TIWA_SILENCE_S", "Pause before cutting",
         "Seconds of quiet that end a sentence. Lower feels snappier but chops "
         "sentences in half, and Whisper is much worse on fragments.",
         "1.2", None),
        ("TIWA_WAKE_FUZZ", "Wake match (latin)",
         "How close a heard word must be to 'Tiwa', 0-1. Lower wakes her more "
         "often, including on the wrong word.",
         "0.72", None),
        ("TIWA_WAKE_FUZZ_TH", "Wake match (Thai)",
         "Same, for ทิวา. Thai is stricter because short Thai words collide easily.",
         "0.8", None),
        ("TIWA_VOICE_DEBUG", "Save what she heard",
         "1 = write every heard clip to data/voice_debug/*.wav so you can listen "
         "back and see why a transcript was nonsense.",
         "0", ["0", "1"]),
    ]),
    ("Logging", [
        ("TIWA_LOG_PROMPTS", "Record model calls",
         "1 = every prompt and reply goes to the model-calls page. Turn it off "
         "only if you want nothing on disk; the page goes empty.",
         "1", ["1", "0"]),
    ]),
]
EDITABLE = {k for _, rows in SETTINGS for k, *_ in rows}
SECRETS = ("DISCORD_TOKEN", "OPENROUTER_API_KEY")
WIPES = ("facts", "episodes", "llm", "log")  # what a reset button may delete

MODE_WORDS = {
    "local": "everything on your GPU — ollama must be running, nothing is billed",
    "mixed": "tools and memory on your GPU, her replies from OpenRouter",
    "api": "nothing local — ollama can be closed, the GPU is free",
}
KIND_WORDS = {
    "turn": "a full reply to someone",
    "tool": "a tool she called",
    "music": "playback",
    "voice": "something heard in voice chat",
}

CSS = """*{box-sizing:border-box}
:root{--bg:#0d0d12;--card:#16161f;--card2:#1c1c27;--line:#282836;--txt:#e9e9f2;
--dim:#8b8ba3;--acc:#7b6cf0;--ok:#3fd08a;--warn:#f5c451;--no:#ff6f6f}
body{font:15px/1.55 ui-sans-serif,system-ui,"Segoe UI",sans-serif;margin:0;
background:var(--bg);color:var(--txt)}
.wrap{max-width:1080px;margin:0 auto;padding:0 18px 60px}
header{position:sticky;top:0;z-index:9;background:rgba(13,13,18,.92);
backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:14px 0 0}
h1{font-size:17px;font-weight:600;margin:0 0 2px;letter-spacing:.01em}
.sub{color:var(--dim);font-size:13px;margin:0 0 10px}
nav{display:flex;gap:4px;flex-wrap:wrap}
nav a{padding:8px 15px;border-radius:9px 9px 0 0;color:var(--dim);
text-decoration:none;font-size:14px;border:1px solid transparent;border-bottom:0}
nav a:hover{color:var(--txt);background:var(--card)}
nav a.on{background:var(--card);color:var(--txt);border-color:var(--line)}
h2{font-size:13px;font-weight:600;color:var(--dim);margin:26px 0 10px;
letter-spacing:.09em;text-transform:uppercase}
h2 .r{float:right;text-transform:none;letter-spacing:0;font-weight:400}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:16px 18px;margin-bottom:14px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(168px,1fr))}
.stat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:13px 15px}
.stat b{display:block;font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}
.stat span{color:var(--dim);font-size:12.5px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--dim);font-weight:500;font-size:12.5px;text-transform:uppercase;
letter-spacing:.05em}
tr:last-child td{border-bottom:0}
tbody tr:hover{background:var(--card2)}
.pill{display:inline-block;padding:2px 10px;border-radius:11px;font-size:12.5px;
white-space:nowrap}
.ok{background:#123626;color:var(--ok)}
.no{background:#3a1a1a;color:var(--no)}.warn{background:#3a3018;color:var(--warn)}
.mode{background:#2a2445;color:#b9abff}.tag{background:#22222e;color:var(--dim)}
input,select{background:#101018;color:var(--txt);border:1px solid #30303f;
border-radius:8px;padding:8px 10px;width:100%;font:inherit}
input:focus,select:focus{outline:0;border-color:var(--acc)}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:9px 18px;
font:inherit;cursor:pointer}
button:hover{filter:brightness(1.12)}
button.ghost{background:#22222e;color:var(--dim)}
button.danger{background:#3a1a1a;color:var(--no)}
button.mini{padding:3px 11px;font-size:12.5px;background:#2a1c1c;color:#e59b9b}
.set{display:grid;grid-template-columns:1fr 300px;gap:6px 22px;padding:14px 0;
border-bottom:1px solid var(--line)}
.set:last-of-type{border-bottom:0}
.set .lab{font-weight:500}.set .help{color:var(--dim);font-size:13px;grid-column:1}
.set .key{font:12px ui-monospace,monospace;color:#6b6b85}
.set .ctl{grid-row:span 3;align-self:start}
.def{color:var(--dim);font-size:12px;margin-top:5px}
.bar{height:7px;background:#22222e;border-radius:4px;overflow:hidden;margin-top:8px}
.bar i{display:block;height:100%;background:var(--acc)}
form.inline{display:inline}
.filters{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:12px}
.filters input{width:230px}.filters select{width:auto}
.chip{display:inline-block;padding:3px 11px;margin:0 5px 5px 0;border-radius:11px;
background:#22222e;color:var(--dim);text-decoration:none;font-size:13px}
.chip:hover,.chip.on{background:var(--acc);color:#fff}
.ms{color:var(--dim);font-variant-numeric:tabular-nums;white-space:nowrap}
.kind{color:#b9abff}
.tool{color:#8fe3c4;font-weight:600;text-decoration:none}
.tool:hover{text-decoration:underline}
code{background:#101018;padding:1px 6px;border-radius:5px;font-size:13px}
pre{background:#0a0a0f;padding:11px;border-radius:8px;overflow:auto;font-size:12.5px;
white-space:pre-wrap;max-height:400px;color:#c6c6d8;margin:6px 0}
details summary{cursor:pointer;color:#a99bf0}
a.exp{color:var(--ok);text-decoration:none;margin-left:14px;font-size:13px}
.empty{color:var(--dim);padding:20px 0;text-align:center}
@media(max-width:700px){.set{grid-template-columns:1fr}.set .ctl{grid-row:auto}}"""


# ---------------------------------------------------------------- env file

def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    return out


def write_env(updates: dict):
    """Rewrite only the given keys. Untouched lines — including secrets — survive."""
    lines = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    for k, v in updates.items():
        if k not in EDITABLE:
            continue  # never let the page write a key it does not own
        pat = re.compile(rf"^{re.escape(k)}\s*=")
        hit = next((i for i, ln in enumerate(lines) if pat.match(ln)), None)
        if not v:
            if hit is not None:
                lines.pop(hit)  # empty = fall back to the default
        elif hit is None:
            lines.append(f"{k}={v}")
        else:
            lines[hit] = f"{k}={v}"
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- helpers

def esc(x):
    return html.escape(str(x))


def when(ts):
    return f"{datetime.datetime.fromtimestamp(ts):%m-%d %H:%M:%S}"


def ago(ts) -> str:
    s = max(0, int(datetime.datetime.now().timestamp() - ts))
    for n, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if s >= n:
            return f"{s // n}{unit} ago"
    return f"{s}s ago"


def port_open(host="127.0.0.1", port=11434) -> bool:
    try:
        with socket.create_connection((host, port), 0.4):
            return True
    except OSError:
        return False


def pill(good, yes, no, cls_no="no"):
    return f"<span class='pill {'ok' if good else cls_no}'>{yes if good else no}</span>"


def which_pass(req: str) -> str:
    """Which of her three passes made this call — the whole point of the page."""
    if "inner thoughts" in req:
        return "thinking"
    if "memory judgment" in req:
        return "remembering"
    if "idle thoughts" in req:
        return "idle"
    if "describing an image" in req:
        return "seeing"
    if "[inner-state" in req:
        return "her reply"
    return "other"


def qs(args, **over) -> str:
    d = {k: v for k, v in {**args, **over}.items() if v}
    return "?" + "&".join(f"{k}={quote(str(v))}" for k, v in d.items())


def wipe_button(what, label, count):
    return (f"<form class=inline method=post onsubmit=\"return confirm("
            f"'Delete {count} {what}? This cannot be undone.')\">"
            f"<input type=hidden name=action value=wipe>"
            f"<input type=hidden name=what value={what}>"
            f"<button class=mini type=submit>{label}</button></form>")


# ---------------------------------------------------------------- views

def view_status(db, env, args, flash):
    ollama = port_open()
    needs_ollama = "ollama" in (llm.PROVIDER, llm.PERSONA_PROVIDER)
    try:
        from tiwa import music
        music_ok = music.ready()
    except Exception as e:
        music_ok = f"music UNAVAILABLE — {type(e).__name__}: {e}"

    last = db.execute("SELECT MAX(ts) FROM log").fetchone()[0]
    turns, avg_ms = db.execute(
        "SELECT COUNT(*), AVG(ms) FROM log WHERE kind='turn' AND ts > ?",
        (datetime.datetime.now().timestamp() - 86400,)).fetchone()
    calls = db.execute("SELECT COUNT(*) FROM llm_log").fetchone()[0]
    ents, rels, eps = (db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                       for t in ("entities", "relations", "episodes"))
    used, cap = llm.spend(), llm.DAILY_TOKENS

    stats = "".join(
        f"<div class=stat><b>{v}</b><span>{k}</span></div>" for k, v in [
            ("replies (24h)", turns or 0),
            ("avg reply time", f"{(avg_ms or 0) / 1000:.1f}s" if turns else "—"),
            ("facts she knows", rels),
            ("episodes", eps),
            ("people &amp; things", ents),
            ("model calls logged", calls),
        ])

    health = [
        ("Discord token", pill(bool(env.get("DISCORD_TOKEN")), "set", "missing"),
         "in .env — without it the bot cannot log in"),
        ("OpenRouter key", pill(bool(env.get("OPENROUTER_API_KEY")), "set", "missing",
                                "warn" if llm.MODE == "local" else "no"),
         "only needed in mixed/api mode"),
        ("Ollama", pill(ollama, "running", "not running",
                        "no" if needs_ollama else "warn"),
         "127.0.0.1:11434 — " + ("required in this mode" if needs_ollama
                                 else "not needed in api mode")),
        ("Music decoder", pill(music_ok.startswith("music ready"), "ready",
                               esc(music_ok)),
         "PyAV, streams from YouTube — no ffmpeg involved"),
        ("Calendar", pill((DATA / "gcal_token.json").exists(), "authorized",
                          "not authorized", "warn"),
         "run <code>py -X utf8 gcal_auth.py</code> once to grant access"),
        ("Listening in voice", pill(env.get("TIWA_LISTEN", "0") != "0", "on", "off",
                                    "tag"),
         "speech-to-text; off by default, Thai accuracy on CPU is poor"),
    ]
    hrows = "".join(f"<tr><td>{n}</td><td>{p}</td><td class=ms>{d}</td></tr>"
                    for n, p, d in health)

    tracks = list(db.execute(
        "SELECT ts, text FROM log WHERE kind='music' ORDER BY id DESC LIMIT 8"))
    trows = "".join(
        f"<tr><td class=ms>{when(ts)}</td><td>{esc(t)}</td></tr>" for ts, t in tracks
    ) or "<tr><td class=empty colspan=2>no songs played yet</td></tr>"

    pct = min(100, used * 100 // cap) if cap else 0
    return f"""
<h2>right now</h2>
<div class=card>
  <b>Mode: <span class='pill mode'>{llm.MODE}</span></b> — {MODE_WORDS.get(llm.MODE, '')}<br>
  <span class=ms>tools &amp; memory &rarr; <code>{llm.PROVIDER}</code>
  &nbsp;·&nbsp; her words &rarr; <code>{llm.PERSONA_PROVIDER}</code>
  &nbsp;·&nbsp; last activity: {ago(last) if last else 'never'}</span>
  <div style="margin-top:12px">API tokens today
  <b class=ms>{used:,}</b> <span class=ms>of {cap:,} — past this she drops back
  to local for the rest of the day</span>
  <div class=bar><i style="width:{pct}%"></i></div></div>
</div>
<div class=grid>{stats}</div>
<h2>health</h2><div class=card><table>{hrows}</table></div>
<h2>recent music</h2><div class=card><table>{trows}</table></div>
"""


def view_settings(db, env, args, flash):
    out = []
    for group, rows in SETTINGS:
        body = []
        for key, label, help_, default, opts in rows:
            cur = env.get(key, "")
            if opts is not None:
                choices = list(opts)
                if cur and cur not in choices:
                    choices.append(cur)
                ctl = (f"<select name={key}>" + "".join(
                    f"<option value='{esc(o)}'{' selected' if cur == o else ''}>"
                    f"{esc(o) or '(auto)'}</option>" for o in choices) + "</select>")
            else:
                ctl = f"<input name={key} value='{esc(cur)}' placeholder='{esc(default)}'>"
            body.append(
                f"<div class=set><div class=lab>{label}</div>"
                f"<div class=ctl>{ctl}<div class=def>default: <code>{esc(default)}</code>"
                f"{'' if cur else ' — in use'}</div></div>"
                f"<div class=help>{help_}</div><div class=key>{key}</div></div>")
        out.append(f"<h2>{group}</h2><div class=card>{''.join(body)}</div>")

    secrets = "".join(
        f"<tr><td>{s}</td><td>{pill(bool(env.get(s)), 'set', 'missing')}</td></tr>"
        for s in SECRETS)
    note = ("<span class='pill ok'>saved — restart her for it to take effect</span>"
            if flash == "saved" else
            "<span class='pill ok'>reset to defaults</span>" if flash == "reset" else "")
    return (f"<h2>settings <span class=r>{note}</span></h2>"
            f"<div class=card>Leave a box empty to use the default. Nothing here "
            f"changes a running bot — <b>stop and start her</b> after saving.</div>"
            f"<form method=post><input type=hidden name=action value=settings>"
            f"{''.join(out)}"
            f"<div style='margin-top:16px'>"
            f"<button type=submit>save to .env</button></div></form>"
            f"<form method=post style='margin-top:10px' "
            f"onsubmit=\"return confirm('Clear every setting and go back to "
            f"defaults? Your secrets are untouched.')\">"
            f"<input type=hidden name=action value=reset_settings>"
            f"<button class=ghost type=submit>reset all to defaults</button></form>"
            f"<h2>secrets</h2><div class=card>Edit <code>.env</code> by hand. Values "
            f"are never shown here and never leave this machine.<table>{secrets}"
            f"</table></div>")


def view_memory(db, env, args, flash):
    q = args.get("q", "").strip().lower()
    rows = list(db.execute(
        "SELECT r.rowid, s.name, r.rel, d.name, r.note, r.updated_at FROM relations r"
        " JOIN entities s ON s.id=r.src JOIN entities d ON d.id=r.dst"
        " ORDER BY r.updated_at DESC"))
    hits = [r for r in rows
            if not q or q in f"{r[1]} {r[2]} {r[3]} {r[4]}".lower()]

    groups = {}
    for rid, s, rel, o, note, ts in hits:
        groups.setdefault(s, []).append((rid, rel, o, note, ts))
    order = sorted(groups, key=lambda k: (-len(groups[k]), k.lower()))
    chips = "".join(f"<a class=chip href='#s{i}'>{esc(s)} {len(groups[s])}</a>"
                    for i, s in enumerate(order))
    cards = "".join(
        f"<div class=card id=s{i}><h3 style='margin:0 0 8px;font-size:15px'>{esc(s)}"
        f"<span class=ms style='font-weight:400'> — {len(groups[s])} "
        f"fact{'' if len(groups[s]) == 1 else 's'}</span></h3>"
        "<table>" + "".join(
            f"<tr><td class=kind style='width:150px'>{esc(rel)}</td><td>{esc(o)}"
            + (f" <span class=ms>— {esc(note)}</span>" if note else "")
            + f"</td><td class=ms style='width:110px'>{ago(ts)}</td><td style='width:80px'>"
            f"<form class=inline method=post><input type=hidden name=action value=del_rel>"
            f"<input type=hidden name=id value={rid}>"
            f"<button class=mini type=submit>forget</button></form></td></tr>"
            for rid, rel, o, note, ts in groups[s]) + "</table></div>"
        for i, s in enumerate(order)) or "<div class='card empty'>nothing matches</div>"

    epq = list(db.execute("SELECT id, user, text, ts FROM episodes ORDER BY ts DESC"))
    ephits = [e for e in epq if not q or q in f"{e[1]} {e[2]}".lower()]
    eps = "".join(
        f"<tr><td class=ms>{when(ts)}</td><td class=kind>{esc(u)}</td><td>{esc(t)}</td>"
        f"<td style='width:80px'><form class=inline method=post>"
        f"<input type=hidden name=action value=del_ep><input type=hidden name=id value={i}>"
        f"<button class=mini type=submit>forget</button></form></td></tr>"
        for i, u, t, ts in ephits
    ) or "<tr><td class=empty colspan=4>nothing yet</td></tr>"

    return (f"<h2>what she knows <span class=r>"
            f"<a class=exp href='/export/memory.json'>export json</a>"
            f"<a class=exp href='/export/memory.csv'>export csv</a></span></h2>"
            f"<form class=filters method=get><input type=hidden name=view value=memory>"
            f"<input name=q value='{esc(args.get('q', ''))}' "
            f"placeholder='search names, facts, episodes…'>"
            f"<button type=submit>filter</button>"
            f"<a class=chip href='/?view=memory'>clear</a>"
            f"<span class=ms>{len(hits)} of {len(rows)} facts · "
            f"{len(ephits)} of {len(epq)} episodes</span></form>"
            f"<div class=card>{chips or 'she knows nothing yet'}"
            f"<div style='margin-top:10px'>{wipe_button('facts', 'forget every fact', len(rows))}"
            f"</div></div>"
            f"{cards}"
            f"<h2>episodes — things that mattered <span class=r>"
            f"<a class=exp href='/export/episodes.csv'>export csv</a></span></h2>"
            f"<div class=card><table>{eps}</table>"
            f"<div style='margin-top:10px'>{wipe_button('episodes', 'forget every episode', len(epq))}"
            f"</div></div>")


def view_llm(db, env, args, flash):
    want, prov, q = (args.get("pass", ""), args.get("prov", ""),
                     args.get("q", "").strip().lower())
    all_rows = memory.read_llm_log(db, 400)
    rows, kept = [], 0
    for i, ts, p, model, ms, tok, req, resp in all_rows:
        which = which_pass(req or "")
        if (want and which != want) or (prov and p != prov):
            continue
        if q and q not in f"{req} {resp}".lower():
            continue
        kept += 1
        if kept > 80:
            continue
        rows.append(
            f"<tr><td class=ms>{when(ts)}</td>"
            f"<td><span class='pill mode'>{which}</span></td>"
            f"<td class=ms>{esc(p)}</td><td>{esc(model)}</td>"
            f"<td class=ms>{ms}ms</td><td class=ms>{tok or ''}</td>"
            f"<td><details><summary>{esc((resp or '')[:100]) or '(no text)'}</summary>"
            f"<b>what she was asked</b><pre>{esc(req)}</pre>"
            f"<b>what came back</b><pre>{esc(resp)}</pre></details></td></tr>")
    body = "".join(rows) or "<tr><td class=empty colspan=7>nothing matches</td></tr>"

    passes = "".join(
        "<a class='chip%s' href='%s'>%s</a>"
        % (" on" if want == p else "", qs(args, view="llm", **{"pass": p}), p or "all")
        for p in ("", "thinking", "her reply", "remembering", "seeing", "idle"))
    provs = "".join(
        "<a class='chip%s' href='%s'>%s</a>"
        % (" on" if prov == p else "", qs(args, view="llm", prov=p), p or "both")
        for p in ("", "ollama", "openrouter"))
    return (f"<h2>model calls <span class=r>"
            f"<a class=exp href='/export/llm.json'>export json</a></span></h2>"
            f"<div class=card>Every turn is up to three calls: "
            f"<b>thinking</b> (picks tools, writes her a private brief), "
            f"<b>her reply</b> (the words you see), and "
            f"<b>remembering</b> (decides what to keep, runs after she answers). "
            f"Click any row to read the exact prompt and reply.<br>"
            f"<div style='margin-top:10px'>{passes}</div>{provs}"
            f"<form class=filters method=get style='margin:10px 0 0'>"
            f"<input type=hidden name=view value=llm>"
            f"<input type=hidden name=pass value='{esc(want)}'>"
            f"<input type=hidden name=prov value='{esc(prov)}'>"
            f"<input name=q value='{esc(args.get('q', ''))}' placeholder='search prompts and replies…'>"
            f"<button type=submit>filter</button>"
            f"<a class=chip href='/?view=llm'>clear</a>"
            f"<span class=ms>showing {min(kept, 80)} of {kept} matching "
            f"({len(all_rows)} kept on disk)</span></form>"
            f"<div style='margin-top:10px'>{wipe_button('llm', 'clear this log', len(all_rows))}</div>"
            f"</div>"
            f"<div class=card><table><tr><th>when</th><th>pass</th><th>where</th>"
            f"<th>model</th><th>took</th><th>tokens</th>"
            f"<th>reply — click to open</th></tr>{body}</table></div>")


def tool_cell(args, text):
    """A tool row reads `name('arg') -> result`. Show those as three things.

    Which tool she reached for and what she typed into it is the whole reason to
    open this page — as one string it is unreadable, and the query is the part you
    are usually hunting for.
    """
    head, sep, out = text.partition(") -> ")
    name, paren, arg = head.partition("(")
    if not sep or not paren:
        return esc(text)  # some other shape — show it raw rather than mangling it
    arg = arg.strip("'\"")
    return (f"<a class=tool href='{qs(args, view='log', kind='tool', q=name)}'>"
            f"{esc(name)}</a>"
            + (f" <code>{esc(arg)}</code>" if arg and arg != "ignored" else "")
            + f" <span class=ms>→ {esc(out)}</span>")


def view_log(db, env, args, flash):
    kind, q = args.get("kind", ""), args.get("q", "").strip().lower()
    all_rows = memory.read_log(db, 3000)
    kinds = sorted({k for _, k, _, _ in all_rows})
    rows, kept = [], 0
    for ts, k, text, ms in all_rows:
        if (kind and k != kind) or (q and q not in text.lower()):
            continue
        kept += 1
        if kept > 250:
            continue
        what = tool_cell(args, text) if k == "tool" else esc(text)
        rows.append(f"<tr><td class=ms>{when(ts)}</td><td class=kind>{esc(k)}</td>"
                    f"<td>{what}</td><td class=ms>{f'{ms}ms' if ms else ''}</td></tr>")
    body = "".join(rows) or "<tr><td class=empty colspan=4>nothing matches</td></tr>"
    chips = "".join(
        "<a class='chip%s' href='%s'>%s</a>"
        % (" on" if kind == k else "", qs(args, view="log", kind=k), k or "everything")
        for k in [""] + kinds)
    legend = " · ".join(f"<b>{k}</b> {v}" for k, v in KIND_WORDS.items() if k in kinds)
    return (f"<h2>activity <span class=r>"
            f"<a class=exp href='/export/log.csv'>export csv</a></span></h2>"
            f"<div class=card>One line for everything she did. {legend}<br>"
            f"<div style='margin-top:10px'>{chips}</div>"
            f"<form class=filters method=get style='margin:10px 0 0'>"
            f"<input type=hidden name=view value=log>"
            f"<input type=hidden name=kind value='{esc(kind)}'>"
            f"<input name=q value='{esc(args.get('q', ''))}' placeholder='search…'>"
            f"<button type=submit>filter</button>"
            f"<a class=chip href='/?view=log'>clear</a>"
            f"<span class=ms>showing {min(kept, 250)} of {kept} matching</span></form>"
            f"<div style='margin-top:10px'>{wipe_button('log', 'clear this log', len(all_rows))}</div>"
            f"</div>"
            f"<div class=card><table><tr><th>when</th><th>kind</th><th>what</th>"
            f"<th>took</th></tr>{body}</table></div>")


VIEW_FN = {"status": view_status, "settings": view_settings, "memory": view_memory,
           "llm": view_llm, "log": view_log}


def page(view="status", args=None, flash="") -> str:
    args = args or {}
    env, db = read_env(), memory.connect()
    nav = "".join(f"<a class='{'on' if v == view else ''}' href='/?view={v}'>{v}</a>"
                  for v in VIEWS)
    body = VIEW_FN[view](db, env, args, flash)
    return f"""<!doctype html><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Tiwa — {view}</title><style>{CSS}</style>
<header><div class=wrap><h1>ทิวา — control panel</h1>
<p class=sub>this machine only · she reads these settings when she starts</p>
<nav>{nav}</nav></div></header><div class=wrap>{body}</div>"""


class H(BaseHTTPRequestHandler):
    def _send(self, body, ctype="text/html; charset=utf-8", fname=None):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if fname:
            self.send_header("Content-Disposition", f'attachment; filename="{fname}"')
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode("utf-8"))

    def do_GET(self):
        url = urlparse(self.path)
        db = memory.connect()
        if url.path.startswith("/export/"):
            return self._export(db, url.path.rsplit("/", 1)[-1])
        args = {k: v[0] for k, v in parse_qs(url.query).items()}
        view = args.pop("view", "status")
        self._send(page(view if view in VIEWS else "status", args))

    def _export(self, db, what):
        if what == "memory.json":
            return self._send(json.dumps(memory.export_all(db), ensure_ascii=False,
                                         indent=2), "application/json", what)
        if what == "llm.json":
            cols = ("id", "ts", "provider", "model", "ms", "tokens", "request",
                    "response")
            data = [dict(zip(cols, r)) for r in memory.read_llm_log(db, 400)]
            return self._send(json.dumps(data, ensure_ascii=False, indent=2),
                              "application/json", what)
        buf = io.StringIO()
        w = csv.writer(buf)
        if what == "memory.csv":
            w.writerow(["subject", "relation", "object", "note", "updated_at"])
            w.writerows([r["subject"], r["relation"], r["object"], r["note"],
                         r["updated_at"]] for r in memory.export_all(db)["relations"])
        elif what == "episodes.csv":
            w.writerow(["when", "with", "what"])
            w.writerows([when(e["ts"]), e["user"], e["text"]]
                        for e in memory.export_all(db)["episodes"])
        else:
            w.writerow(["ts", "kind", "text", "ms"])
            w.writerows(memory.read_log(db, 5000))
        # utf-8-sig: Excel opens Thai as mojibake without the BOM
        self._send(buf.getvalue().encode("utf-8-sig"), "text/csv", what)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        form = {k: v[0] for k, v in
                parse_qs(self.rfile.read(n).decode("utf-8")).items()}
        action = form.pop("action", "settings")
        db = memory.connect()
        if action == "del_rel":
            memory.delete_relation(db, int(form["id"]))
            return self._send(page("memory"))
        if action == "del_ep":
            memory.delete_episode(db, int(form["id"]))
            return self._send(page("memory"))
        if action == "wipe":
            what = form.get("what", "")
            if what in WIPES:
                memory.wipe(db, what)
            # anything else is a forged/stale form: delete nothing, show status
            back = {"facts": "memory", "episodes": "memory", "llm": "llm",
                    "log": "log"}.get(what, "status")
            return self._send(page(back))
        if action == "reset_settings":
            write_env({k: "" for k in EDITABLE})
            return self._send(page("settings", flash="reset"))
        write_env({k: v.strip() for k, v in form.items()})
        self._send(page("settings", flash="saved"))

    def log_message(self, *a):
        pass  # keep the console clean


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    print(f"control panel: {url}   (ctrl-c to stop)")
    if "--open" in sys.argv:
        webbrowser.open(url)
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()  # localhost: it edits .env
