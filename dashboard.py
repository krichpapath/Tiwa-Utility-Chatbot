"""Tiwa control panel — watch her, talk to her, change any knob.

    py -X utf8 dashboard.py [--open]      ->  http://127.0.0.1:8787

Gradio. Every table searches and sorts itself, so this file holds no filter
widgets and no HTTP handler. The one stylesheet at the bottom exists because
Gradio spaces every block equally, and equal spacing is no grouping at all.

Localhost-only by design: it edits .env, wipes memory, and (on the chat tab)
spends your API key, so it must never be reachable off this machine. Secret
VALUES are never rendered — only set/missing.
"""
import asyncio
import csv
import datetime
import io
import json
import re
import socket
import statistics
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import gradio as gr
import pandas as pd

from tiwa import llm, memory, pipeline, tools


ENV = Path(__file__).with_name(".env")
DATA = Path(__file__).with_name("data")
PORT = 8787

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
    ("Memory", [
        ("TIWA_EXTRACT_THINK", "Think before remembering",
         "Turns the model's reasoning on for the memory write pass — the only "
         "pass nobody waits on, since it runs after her reply is already sent. "
         "Measured and NOT recommended: no accuracy gain either provider, and "
         "on the local 8B it was 15x slower and leaked JSON into an entity name "
         "(tests/extractbench.py --think).",
         "0", ["0", "1"]),
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
    "reflect": "an idle-time conclusion about someone",
}


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
    if "memory settling" in req:
        return "reflecting"
    if "[inner-state" in req:
        return "her reply"
    return "other"


DB = memory.connect()  # check_same_thread=False — Gradio answers on worker threads
LIVE = 400  # ponytail: flat cap on the live tables. Paginate the day it bites.


# ---------------------------------------------------------------- now

def now_html() -> str:
    """The whole answer to "is she alright", in one screen and one reading order:
    what she is running on, then what is broken, then what she has been doing.

    Health sits second because it is why you opened the page. It used to be last.
    """
    turns, avg_ms = DB.execute(
        "SELECT COUNT(*), AVG(ms) FROM log WHERE kind='turn' AND ts > ?",
        (time.time() - 86400,)).fetchone()
    rels, eps, ents, calls = (DB.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                              for t in ("relations", "episodes", "entities", "llm_log"))
    last = DB.execute("SELECT MAX(ts) FROM log").fetchone()[0]
    used, cap = llm.spend(), llm.DAILY_TOKENS
    nums = "".join(
        f"<div><b>{v}</b><span>{k}</span></div>" for k, v in (
            ("replies, last 24h", turns or 0),
            ("average reply", f"{(avg_ms or 0) / 1000:.1f}s" if turns else "—"),
            ("facts she knows", rels),
            ("episodes", eps),
            ("people &amp; things", ents),
            ("model calls kept", calls)))
    return (
        f"<div class='card'><div class='status'>"
        f"<div class='mode'>"
        f"<p class='lead'>Mode <b>{llm.MODE}</b> — {MODE_WORDS.get(llm.MODE, '')}</p>"
        f"<p class='sub'>tools &amp; memory &rarr; <code>{llm.PROVIDER}</code>"
        f"<i>·</i>her words &rarr; <code>{llm.PERSONA_PROVIDER}</code>"
        f"<i>·</i>last activity {ago(last) if last else 'never'}</p></div>"
        f"<div class='spend'><div class='meter-top'><span>API tokens today</span>"
        f"<b>{used:,}</b><span>of {cap:,}</span></div>"
        f"<div class='bar'><i style='transform:scaleX("
        f"{min(1, used / cap) if cap else 0:.4f})'></i></div>"
        f"<p class='sub'>past this she falls back to local for the rest of the day</p>"
        f"</div></div>"
        f"{health_html()}<div class='nums'>{nums}</div></div>")


def _passes() -> dict:
    """Calls and median latency per pass, over the model log's rolling window."""
    by = {}
    for _, _, _, _, ms, _, req, _ in memory.read_llm_log(DB, LIVE):
        by.setdefault(which_pass(req or ""), []).append(ms or 0)
    return by


def passes_html() -> str:
    """One turn, left to right. The three passes are the shape of the whole product,
    and 'which pass is slow' is the question this panel exists to answer."""
    by = _passes()
    steps = [("thinking", "picks tools, writes her a private brief"),
             ("her reply", "the words you actually see"),
             ("remembering", "decides what to keep, after she answers")]
    out = []
    for i, (name, what) in enumerate(steps):
        ms = by.get(name, [])
        took = f"{statistics.median(ms) / 1000:.1f}s" if ms else "—"
        out.append(f"<div class='pass'><b>{name}</b><span>{what}</span>"
                   f"<em>{took}<i>median</i></em><em>{len(ms)}<i>calls</i></em></div>")
        if i < 2:
            out.append("<svg class='flow' viewBox='0 0 34 8' aria-hidden='true'>"
                       "<path d='M0 4h26M22 1l4 3-4 3'/></svg>")
    extra = [(k, v) for k, v in by.items()
             if k not in {n for n, _ in steps} and k != "other"]
    tail = ("<p class='sub aside'>also on the clock: " + " · ".join(
        f"{k} {len(v)}" for k, v in sorted(extra)) + "</p>") if extra else ""
    return f"<div class='card pad'><div class='passes'>{''.join(out)}</div>{tail}</div>"


def health_html() -> str:
    """Six things that are either true or not. A markdown table made them a grid of
    equal cells; they are a list, and the failing one has to be the loud one."""
    env = read_env()
    needs_ollama = "ollama" in (llm.PROVIDER, llm.PERSONA_PROVIDER)
    try:
        from tiwa import music
        decoder = music.ready()
    except Exception as e:
        decoder = f"music UNAVAILABLE — {type(e).__name__}: {e}"
    # (ok, warn-not-error, what, why it matters)
    rows = [
        (bool(env.get("DISCORD_TOKEN")), False, "Discord token",
         "in .env — without it the bot cannot log in"),
        (bool(env.get("OPENROUTER_API_KEY")), llm.MODE == "local", "OpenRouter key",
         "only needed in mixed/api mode"),
        (port_open(), not needs_ollama, "Ollama",
         "127.0.0.1:11434 — " + ("required in this mode" if needs_ollama
                                 else "not needed in api mode")),
        (decoder == "music ready", False, "Music decoder",
         "PyAV, streams from YouTube — no ffmpeg involved"
         if decoder == "music ready" else decoder),
        ((DATA / "gcal_token.json").exists(), True, "Calendar",
         "run <code>py -X utf8 gcal_auth.py</code> once to grant access"),
        (env.get("TIWA_LISTEN", "0") != "0", True, "Listening in voice",
         "speech-to-text; off on purpose, Thai accuracy on CPU is poor"),
    ]
    return "<div class='health'>" + "".join(
        f"<div class='hrow {'ok' if ok else 'soft' if soft else 'bad'}'>"
        f"<span class='dot' role='img' aria-label="
        f"'{'ok' if ok else 'warning' if soft else 'not working'}'></span>"
        f"<b>{what}</b><span class='why'>{why}</span></div>"
        for ok, soft, what, why in rows) + "</div>"


def activity_df() -> pd.DataFrame:
    """Replies per hour over the last day: the shape of a normal day, so an
    abnormal one shows up without reading a single log line."""
    # the label carries the date because a 24h window can hold the same clock hour
    # twice, and a bar chart with two "14:00" columns is a lie
    hours = Counter(
        f"{datetime.datetime.fromtimestamp(ts):%m-%d %H}:00"
        for ts, in DB.execute("SELECT ts FROM log WHERE kind='turn' AND ts > ?",
                              (time.time() - 86400,)))
    return pd.DataFrame(sorted(hours.items()), columns=["hour", "replies"])


def music_df() -> pd.DataFrame:
    return pd.DataFrame(
        [(when(ts), t) for ts, t in DB.execute(
            "SELECT ts, text FROM log WHERE kind='music' ORDER BY id DESC LIMIT 12")],
        columns=["when", "what"])


def pass_df() -> pd.DataFrame:
    """How the model log divides up — one frame, two questions: how many calls each
    pass costs, and how long each one takes."""
    return pd.DataFrame(
        [(name, len(ms), round(statistics.median(ms) / 1000, 2))
         for name, ms in sorted(_passes().items(), key=lambda kv: -len(kv[1]))],
        columns=["pass", "calls", "median s"])


def people_df() -> pd.DataFrame:
    """Who she actually knows things about. A long tail of one-fact names usually
    means the extraction pass is inventing people."""
    return pd.DataFrame(
        DB.execute("SELECT s.name, COUNT(*) FROM relations r"
                   " JOIN entities s ON s.id = r.src GROUP BY s.name"
                   " ORDER BY COUNT(*) DESC LIMIT 14").fetchall(),
        columns=["who", "facts"])


# ---------------------------------------------------------------- tables

def _tool_row(text: str) -> str:
    """A tool line is `name('arg') -> result`. Space it out. Which tool she reached
    for and what she typed into it is the whole reason to open this page, and as
    one unbroken string it is unreadable."""
    head, sep, out = text.partition(") -> ")
    name, paren, arg = head.partition("(")
    if not sep or not paren:
        return text  # some other shape — show it raw rather than mangling it
    arg = arg.strip("'\"")
    return "   ".join([name] + ([arg] if arg and arg != "ignored" else [])
                      + ["→ " + out])


def log_df() -> pd.DataFrame:
    return pd.DataFrame(
        [(when(ts), k, _tool_row(text) if k == "tool" else text,
          f"{ms}ms" if ms else "")
         for ts, k, text, ms in memory.read_log(DB, LIVE)],
        columns=["when", "kind", "what", "took"])


def llm_df() -> pd.DataFrame:
    return pd.DataFrame(
        [(i, when(ts), which_pass(req or ""), prov, model, ms, tok or 0,
          " ".join((resp or "").split())[:160] or "(no text)")
         for i, ts, prov, model, ms, tok, req, resp in memory.read_llm_log(DB, LIVE)],
        columns=["#", "when", "pass", "where", "model", "ms", "tokens", "reply"])


def open_call(evt: gr.SelectData):
    """Click any row to read the exact prompt and the exact reply."""
    row = DB.execute("SELECT request, response FROM llm_log WHERE id = ?",
                     (int(evt.row_value[0]),)).fetchone()
    return row if row else ("", "")


def facts_df() -> pd.DataFrame:
    return pd.DataFrame(
        [(rid, False, s, rel, o, note or "", ago(ts))
         for rid, s, rel, o, note, ts in DB.execute(
             "SELECT r.rowid, s.name, r.rel, d.name, r.note, r.updated_at"
             " FROM relations r JOIN entities s ON s.id = r.src"
             " JOIN entities d ON d.id = r.dst ORDER BY r.updated_at DESC")],
        columns=["#", "forget", "who", "what", "about", "note", "updated"])


def episodes_df() -> pd.DataFrame:
    # text != '': blank rows are reflection watermarks, not events (memory.reflect)
    return pd.DataFrame(
        [(i, False, when(ts), u, t) for i, u, t, ts in DB.execute(
            "SELECT id, user, text, ts FROM episodes WHERE text != ''"
            " ORDER BY ts DESC")],
        columns=["#", "forget", "when", "with", "what"])


def _ticked(df) -> list:
    """Row ids whose forget box is ticked. By id, never by row position — the table
    sorts and filters itself in the browser, so position means nothing back here."""
    return [int(r[0]) for r in df.itertuples(index=False)
            if str(r[1]).lower() in ("true", "1")]


def forget_facts(df):
    ids = _ticked(df)
    for rid in ids:
        memory.delete_relation(DB, rid)
    gr.Info(f"forgot {len(ids)} fact(s)" if ids else "tick a forget box first")
    return facts_df()


def forget_episodes(df):
    ids = _ticked(df)
    for eid in ids:
        memory.delete_episode(DB, eid)
    gr.Info(f"forgot {len(ids)} episode(s)" if ids else "tick a forget box first")
    return episodes_df()


def wipe(what, sure):
    if not sure:
        raise gr.Error("tick 'yes, really' first — this cannot be undone")
    memory.wipe(DB, what)  # whitelisted table names live in memory._WIPEABLE
    gr.Info(f"{what}: all gone")


# ---------------------------------------------------------------- exports

def _dump(name, rows, header=None) -> str:
    out = Path(tempfile.gettempdir()) / name
    if name.endswith(".json"):
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    else:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(header)
        w.writerows(rows)
        # utf-8-sig: Excel opens Thai as mojibake without the BOM
        out.write_bytes(buf.getvalue().encode("utf-8-sig"))
    return str(out)


def export_memory():
    return _dump("memory.json", memory.export_all(DB))


def export_facts():
    return _dump("memory.csv",
                 [[r["subject"], r["relation"], r["object"], r["note"],
                   r["updated_at"]]
                  for r in memory.export_all(DB)["relations"]],
                 ["subject", "relation", "object", "note", "updated_at"])


def export_episodes():
    return _dump("episodes.csv",
                 [[when(e["ts"]), e["user"], e["text"]]
                  for e in memory.export_all(DB)["episodes"]],
                 ["when", "with", "what"])


def export_llm():
    cols = ("id", "ts", "provider", "model", "ms", "tokens", "request", "response")
    return _dump("llm.json",
                 [dict(zip(cols, r)) for r in memory.read_llm_log(DB, LIVE)])


def export_log():
    return _dump("log.csv", memory.read_log(DB, 5000), ["ts", "kind", "text", "ms"])


# ---------------------------------------------------------------- settings

KEYS = [k for _, rows in SETTINGS for k, *_ in rows]
BLANKS = [None if opts is not None else "" for _, rows in SETTINGS
          for *_, opts in rows]


def save_settings(*vals):
    write_env({k: str(v or "").strip() for k, v in zip(KEYS, vals)})
    gr.Info("saved to .env — stop and start her before it takes effect")


def reload_settings():
    env = read_env()
    return [env.get(k) or b for k, b in zip(KEYS, BLANKS)]


def reset_settings(sure):
    if not sure:
        raise gr.Error("tick 'yes, really' first — every setting goes back to default")
    write_env(dict.fromkeys(EDITABLE, ""))
    gr.Info("back to defaults. Your secrets were not touched.")
    return BLANKS


def secrets_md() -> str:
    env = read_env()
    return "| secret | |\n|---|---|\n" + "\n".join(
        f"| `{s}` | {'✅ set' if env.get(s) else '❌ missing'} |" for s in SECRETS)


# ---------------------------------------------------------------- chat

def _pending() -> str:
    """She can ask for music, voice and calendar writes, and only the Discord bot
    can carry those out. Drain them so they never leak into the bot's next turn,
    and say so, rather than letting her look like she did it."""
    jobs = [f"{act} {arg}".strip() for act, arg in tools.DJ]
    tools.DJ.clear()
    if tools.PENDING_MUSIC is not None:
        jobs.insert(0, "stop music" if tools.PENDING_MUSIC == ""
                    else f"play {tools.PENDING_MUSIC}")
        tools.PENDING_MUSIC = None
    jobs += [f"calendar: {c}" for c in tools.PENDING_CALENDAR]
    tools.PENDING_CALENDAR.clear()
    if tools.PENDING_JOIN or tools.PENDING_LEAVE:
        jobs.append("join/leave voice")
        tools.PENDING_JOIN = tools.PENDING_LEAVE = False
    return (f"\n\n*asked for: {'; '.join(jobs)} — only the Discord bot can do that*"
            if jobs else "")


async def chat_fn(message, history, who, remember):
    """One real turn — same brain, same database, same three passes as Discord."""
    hist = [{"role": m["role"],
             "content": f"{who}: {m['content']}" if m["role"] == "user"
                        else m["content"]}
            for m in history]
    hist.append({"role": "user", "content": f"{who}: {message}"})
    late_lines = []
    async def late(line):
        late_lines.append(line)
    try:
        reply = await pipeline.respond(DB, hist, who, message, on_late=late)
        pending = pipeline._pending.get(who)
        if pending:
            await asyncio.gather(pending, return_exceptions=True)
    except Exception as error:
        return f"⚠️ brain call failed: {type(error).__name__}; retry when provider is available"
    if remember:
        # a full model call, and nobody waits on it in Discord either — keep it off
        # the event loop so the next message is not stuck behind it
        await asyncio.to_thread(memory.extract, DB, who, message, reply,
                                memory.history_context(hist))
    return "\n\n".join([reply, *late_lines]) + _pending()


# ---------------------------------------------------------------- the page

READONLY = dict(interactive=False, show_search="filter", wrap=True, max_height=560)
PLOT = dict(height=250, container=False, elem_classes="plot")


def sec(title, note=""):
    """A section heading. Generous space above, tight space below — the heading has
    to belong to what follows it, and Gradio's uniform 16px gap gives it to neither."""
    return gr.Markdown(f"##### {title}" + (f"<span>{note}</span>" if note else ""),
                       elem_classes="sec")


with gr.Blocks(title="Tiwa — control panel", fill_width=True) as demo:
    gr.Markdown("### ทิวา — control panel<span>this machine only · she reads these "
                "settings when she starts</span>", elem_classes="hdr")

    with gr.Tabs():
        with gr.Tab("Now"):
            live = gr.Checkbox(True, label="live — refreshes every 4s",
                               container=False, elem_classes="live")
            # what she runs on, what is broken, what she holds — no heading, because
            # the unlabelled block is the one that leads
            now = gr.HTML(now_html)

            sec("one turn, three passes", "median over the last 400 model calls")
            passes = gr.HTML(passes_html)

            with gr.Row(equal_height=False):
                with gr.Column(scale=7):
                    sec("her day", "replies per hour, last 24 hours")
                    plot = gr.BarPlot(activity_df, x="hour", y="replies", sort="x",
                                      x_label_angle=-45, x_title=None, y_title=None,
                                      **PLOT)
                with gr.Column(scale=5):
                    sec("recent music", "the last dozen she put on")
                    music_tbl = gr.Dataframe(music_df, show_label=False,
                                             interactive=False, wrap=True,
                                             max_height=250,
                                             column_widths=["28%", "72%"])
            beat = gr.Timer(4)
            beat.tick(lambda: (now_html(), passes_html(), activity_df(), music_df()),
                      outputs=[now, passes, plot, music_tbl],
                      show_progress="hidden")
            live.change(lambda on: gr.Timer(active=on), live, beat)

        with gr.Tab("Chat"):
            gr.Markdown(
                "Talk to her here exactly as Discord does — same brain, same "
                "memory, same bill. Music, voice and calendar writes are named "
                "but not carried out: only the bot can do those.",
                elem_classes="intro")
            with gr.Row():
                who = gr.Textbox("Krich", label="who you are",
                                 info="she files what she learns under this name")
                remember = gr.Checkbox(True, label="let her remember this",
                                       info="untick to talk without writing to memory")
            gr.ChatInterface(chat_fn, additional_inputs=[who, remember],
                             save_history=True, editable=True)

        with gr.Tab("Settings"):
            gr.Markdown("Leave a box empty to use the default. Nothing here changes "
                        "a running bot — **stop and start her** after saving.",
                        elem_classes="intro")
            with gr.Row():
                save_btn = gr.Button("save to .env", variant="primary")
                reload_btn = gr.Button("reload from .env")
            fields, env0 = {}, read_env()
            for group, rows in SETTINGS:
                with gr.Accordion(group, open=group.startswith("Where")):
                    for key, label, help_, default, opts in rows:
                        info = f"{help_}  ·  default: {default or '(empty)'}"
                        cur = env0.get(key) or None
                        fields[key] = (
                            gr.Dropdown([(o or "(default)", o) for o in opts],
                                        value=cur, label=label, info=info,
                                        allow_custom_value=True)
                            if opts is not None else
                            gr.Textbox(cur, label=label, info=info,
                                       placeholder=default))
            boxes = [fields[k] for k in KEYS]
            save_btn.click(save_settings, boxes, None)
            reload_btn.click(reload_settings, None, boxes)
            with gr.Accordion("danger", open=False):
                sure_reset = gr.Checkbox(False, label="yes, really")
                gr.Button("reset every setting to its default", variant="stop").click(
                    reset_settings, sure_reset, boxes)
            sec("secrets", "edit .env by hand — values never leave this machine")
            gr.Markdown(secrets_md)

        with gr.Tab("Memory") as tab_mem:
            gr.Markdown("Everything she knows. Tick **forget** on any rows and press "
                        "the button — the search box filters, it never deletes.",
                        elem_classes="intro")
            sec("who she knows about", "facts held per name")
            people = gr.BarPlot(people_df, x="who", y="facts", sort="-y",
                                x_label_angle=-40, x_title=None, y_title=None, **PLOT)
            sec("every fact")
            with gr.Row():
                forget_f = gr.Button("forget ticked facts", variant="stop")
                dl_facts = gr.DownloadButton("facts (csv)")
                dl_all = gr.DownloadButton("everything (json)")
            facts = gr.Dataframe(facts_df, datatype=["number", "bool"] + ["str"] * 5,
                                 static_columns=[0, 2, 3, 4, 5, 6], interactive=True,
                                 # no add-row button: a row with no id is not a fact
                                 row_count=(1, "fixed"),
                                 show_search="filter", wrap=True, max_height=520,
                                 show_label=False,
                                 column_widths=["5%", "8%", "13%", "15%", "20%",
                                                "27%", "12%"])
            sec("episodes", "things that mattered enough to keep whole")
            with gr.Row():
                forget_e = gr.Button("forget ticked episodes", variant="stop")
                dl_eps = gr.DownloadButton("episodes (csv)")
            episodes = gr.Dataframe(episodes_df, show_label=False, interactive=True,
                                    datatype=["number", "bool"] + ["str"] * 3,
                                    static_columns=[0, 2, 3, 4], row_count=(1, "fixed"),
                                    show_search="filter",
                                    wrap=True, max_height=420,
                                    column_widths=["5%", "8%", "15%", "12%", "60%"])
            forget_f.click(forget_facts, facts, facts)
            forget_e.click(forget_episodes, episodes, episodes)
            dl_facts.click(export_facts, None, dl_facts)
            dl_all.click(export_memory, None, dl_all)
            dl_eps.click(export_episodes, None, dl_eps)
            with gr.Accordion("danger", open=False):
                sure_mem = gr.Checkbox(False, label="yes, really")
                with gr.Row():
                    gr.Button("forget every fact", variant="stop").click(
                        lambda s: wipe("facts", s), sure_mem, None
                    ).then(facts_df, None, facts)
                    gr.Button("forget every episode", variant="stop").click(
                        lambda s: wipe("episodes", s), sure_mem, None
                    ).then(episodes_df, None, episodes)
            tab_mem.select(lambda: (people_df(), facts_df(), episodes_df()), None,
                           [people, facts, episodes], show_progress="hidden")

        with gr.Tab("Model calls") as tab_llm:
            gr.Markdown(
                "Every turn is up to three calls: **thinking** (picks tools, writes "
                "her a private brief), **her reply** (the words you see), and "
                "**remembering** (decides what to keep, runs after she answers). "
                "Click any row to read the exact prompt and the exact reply.",
                elem_classes="intro")
            with gr.Row():
                with gr.Column():
                    sec("where the calls go")
                    by_pass = gr.BarPlot(pass_df, x="pass", y="calls", sort="-y",
                                         x_title=None, y_title=None, **PLOT)
                with gr.Column():
                    sec("how long each one takes", "median seconds")
                    by_time = gr.BarPlot(pass_df, x="pass", y="median s", sort="-y",
                                         x_title=None, y_title=None, **PLOT)
            sec("every call", "newest first")
            with gr.Row():
                refresh_llm = gr.Button("refresh")
                dl_llm = gr.DownloadButton("model calls (json)")
            calls = gr.Dataframe(llm_df, show_label=False, pinned_columns=1,
                                 column_widths=["4%", "10%", "9%", "8%", "17%",
                                                "6%", "6%", "40%"], **READONLY)
            sec("one call, in full", "click a row above")
            with gr.Row():
                asked = gr.Textbox(label="what she was asked", lines=16,
                                   max_lines=16, interactive=False)
                came = gr.Textbox(label="what came back", lines=16, max_lines=16,
                                  interactive=False)
            calls.select(open_call, None, [asked, came])
            refresh_llm.click(lambda: (llm_df(), pass_df(), pass_df()), None,
                              [calls, by_pass, by_time])
            dl_llm.click(export_llm, None, dl_llm)
            with gr.Accordion("danger", open=False):
                sure_llm = gr.Checkbox(False, label="yes, really")
                gr.Button("clear the model log", variant="stop").click(
                    lambda s: wipe("llm", s), sure_llm, None
                ).then(lambda: (llm_df(), pass_df(), pass_df()), None,
                       [calls, by_pass, by_time])
            tab_llm.select(lambda: (llm_df(), pass_df(), pass_df()), None,
                           [calls, by_pass, by_time], show_progress="hidden")

        with gr.Tab("Activity log") as tab_log:
            gr.Markdown("One line for everything she did. "
                        + " · ".join(f"**{k}** {v}" for k, v in KIND_WORDS.items()),
                        elem_classes="intro")
            with gr.Row():
                refresh_log = gr.Button("refresh")
                dl_log = gr.DownloadButton("activity (csv)")
            activity = gr.Dataframe(log_df, show_label=False,
                                    column_widths=["12%", "9%", "72%", "7%"],
                                    **READONLY)
            refresh_log.click(log_df, None, activity)
            dl_log.click(export_log, None, dl_log)
            with gr.Accordion("danger", open=False):
                sure_log = gr.Checkbox(False, label="yes, really")
                gr.Button("clear the activity log", variant="stop").click(
                    lambda s: wipe("log", s), sure_log, None
                ).then(log_df, None, activity)
            tab_log.select(log_df, None, activity, show_progress="hidden")


# Gradio gives every stacked block the same 16px gap, so a heading sits as far from
# its own table as from the section above it and nothing reads as a group. The whole
# point of this sheet is the rhythm: 34px above a heading, 6px below it.
CSS = """
/* width:100% matters — the container is a flex item, and auto margins on a flex item
   swallow the free space before flex-grow ever runs, so it stays at content width.
   clamp, not a media query: gradio rewrites the selectors it is handed, and a
   :root or .gradio-container rule inside @media quietly stops matching. */
.gradio-container{width:100%!important;max-width:1400px!important;margin:0 auto!important;
  padding:10px clamp(12px,3vw,34px) 72px!important}
/* gradio pads .fillable too; two gutters stacked left 66px of dead rail on a phone */
.fillable{padding-left:0!important;padding-right:0!important}
.hdr h3{margin:0 0 3px;font-weight:650;letter-spacing:-.01em}
.hdr span,.sec span{display:block;font-size:13px;font-weight:400;
  color:var(--body-text-color-subdued);letter-spacing:0}
.block.sec{margin:30px 0 -18px!important}
.sec h5{margin:0;font-size:12px;font-weight:600;letter-spacing:.10em;
  text-transform:uppercase;color:var(--body-text-color-subdued)}
.sec span{margin-top:3px;text-transform:none;letter-spacing:0}
.intro{max-width:74ch;margin-bottom:6px!important;color:var(--body-text-color-subdued)}
.live{margin:-6px 0 -10px;display:flex;justify-content:flex-end}
.live,.live *{border:0!important;background:none!important;box-shadow:none!important}
.live label{font-size:12.5px;color:var(--body-text-color-subdued)}

/* right now — one panel, because mode, spend and totals answer one question */
.card{border:1px solid var(--border-color-primary);border-radius:14px;
  background:var(--block-background-fill);overflow:hidden}
.card.pad{padding:20px 24px}
.status{display:flex;flex-wrap:wrap;gap:12px 46px;justify-content:space-between;
  padding:22px 26px}
.status .mode{flex:1 1 430px;min-width:0}
.status .spend{flex:0 1 400px;min-width:0}
.lead{margin:0 0 5px;font-size:16.5px;max-width:78ch}
.lead b{font-weight:650}
.sub{margin:0;font-size:13px;max-width:82ch;color:var(--body-text-color-subdued)}
.sub i{font-style:normal;padding:0 9px;opacity:.45}
.status code{font-size:12.5px;padding:1px 6px;border-radius:5px;
  background:var(--background-fill-secondary)}
.meter-top{display:flex;align-items:baseline;gap:7px;font-size:13px;
  color:var(--body-text-color-subdued)}
.meter-top b{font-size:15px;color:var(--body-text-color);
  font-variant-numeric:tabular-nums}
.bar{height:6px;margin:7px 0 6px;border-radius:3px;overflow:hidden;
  background:var(--background-fill-secondary)}
/* scaleX, not width: animating width relayouts the page every tick */
.bar i{display:block;height:100%;width:100%;border-radius:3px;transform-origin:left;
  background:var(--color-accent);transition:transform .4s cubic-bezier(.2,.8,.2,1)}
.nums{display:flex;flex-wrap:wrap;
  border-top:1px solid var(--border-color-primary)}
.nums div{flex:1 1 128px;padding:18px 22px 4px}
.nums div+div{border-left:1px solid var(--border-color-primary)}
.nums b{display:block;font-size:25px;font-weight:600;line-height:1.15;
  font-variant-numeric:tabular-nums}
.nums span{font-size:12.5px;color:var(--body-text-color-subdued)}

/* one turn, three passes */
.passes{display:flex;align-items:stretch;gap:4px;flex-wrap:wrap;max-width:1500px}
.pass{flex:1 1 210px;padding:2px 4px}
.pass b{display:block;font-size:14.5px;font-weight:650}
.pass>span{display:block;margin:4px 0 16px;font-size:12.5px;
  color:var(--body-text-color-subdued)}
.pass em{display:inline-block;margin-right:20px;font-style:normal;font-size:17px;
  font-weight:600;font-variant-numeric:tabular-nums}
.pass em i{display:block;font-style:normal;font-size:11.5px;font-weight:400;
  letter-spacing:.05em;text-transform:uppercase;color:var(--body-text-color-subdued)}
.flow{width:34px;align-self:center;flex:0 0 auto;opacity:.32;
  margin-bottom:26px}
.flow path{fill:none;stroke:currentColor;stroke-width:1.2;stroke-linecap:round;
  stroke-linejoin:round}
.aside{margin:12px 2px 0;font-size:12.5px;color:var(--body-text-color-subdued)}
/* charts sit between borderless sections; a framed one reads as the odd block out */
.plot{border:1px solid var(--border-color-primary)!important;border-radius:14px;
  background:var(--block-background-fill)!important;padding:16px 14px 6px!important}

/* health — six things that are true or not, so the false one has to be loud */
/* six across, not six down: a 1300px page reading a 6-row list down one side
   wastes the width and buries the one row that is red */
.health{display:flex;flex-wrap:wrap;
  border-top:1px solid var(--border-color-primary)}
.hrow{flex:1 1 190px;min-width:0;display:grid;grid-template-columns:auto 1fr;
  gap:3px 9px;align-items:center;padding:16px 20px 18px}
.hrow+.hrow{border-left:1px solid var(--border-color-primary)}
.hrow b{font-weight:550}
.hrow .why{grid-column:2;font-size:12px;line-height:1.45;
  color:var(--body-text-color-subdued)}
.hrow .why code{font-size:11.5px;padding:1px 5px;border-radius:4px;
  background:var(--background-fill-secondary)}
.dot{width:8px;height:8px;border-radius:50%;transform:translateY(-1px)}
.ok .dot{background:#3fd08a;box-shadow:0 0 0 3px rgba(63,208,138,.15)}
.soft .dot{background:#f5c451;box-shadow:0 0 0 3px rgba(245,196,81,.15)}
.bad .dot{background:#ff6f6f;box-shadow:0 0 0 3px rgba(255,111,111,.18)}
.bad b{color:#ff8f8f}

/* browser surfaces gradio leaves at the useragent default */
::selection{background:color-mix(in srgb,var(--color-accent) 32%,transparent)}
:focus-visible{outline:2px solid var(--color-accent);outline-offset:2px}
*{scrollbar-width:thin;
  scrollbar-color:var(--border-color-primary) transparent}
table td,.cell-wrap{font-variant-numeric:tabular-nums}

@media(max-width:760px){
  .nums div+div{border-left:0}
  .nums div{flex:1 1 42%;padding:15px 14px 4px}
  .hrow{flex:1 1 100%}
  .hrow+.hrow{border-left:0;border-top:1px solid var(--border-color-primary)}
  /* one turn reads top to bottom when there is no room for left to right.
     nowrap matters: a wrapping column flex builds columns, not rows */
  .passes{flex-direction:column;flex-wrap:nowrap}
  .pass{flex:0 0 auto}
  .flow{transform:rotate(90deg);margin:-2px auto}
  .block.sec{margin-top:26px!important}
}
"""

if __name__ == "__main__":
    print(f"control panel: http://127.0.0.1:{PORT}   (ctrl-c to stop)")
    demo.launch(server_name="127.0.0.1",  # localhost: it edits .env and wipes memory
                server_port=PORT, share=False, inbrowser="--open" in sys.argv,
                quiet=True, css=CSS,
                theme=gr.themes.Ocean(primary_hue="violet", neutral_hue="slate",
                                      spacing_size="lg", radius_size="lg"))
