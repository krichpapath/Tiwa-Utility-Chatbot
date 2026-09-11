"""Tiwa control panel — watch her, talk to her, change any knob.

    py -X utf8 dashboard.py [--open]      ->  http://127.0.0.1:8787

Gradio provides the controls; memory browsing adds person and text filters. The stylesheet in assets/dashboard.css exists because
Gradio spaces every block equally, and equal spacing is no grouping at all.

Localhost-only by design: it edits .env, wipes memory, and (on the chat tab)
spends your API key, so it must never be reachable off this machine. Secret
VALUES are never rendered — only set/missing.
"""
import asyncio
import csv
import datetime
import io
import hashlib
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

from tiwa import llm, memory, pipeline, tools, recordings, recall


ENV = Path(__file__).with_name(".env")
DATA = Path(__file__).with_name("data")
PORT = 8787

# Every editable knob, with the plain-English answer to "what is this and what
# happens if I leave it alone". (key, label, help, default, options|None)
from tiwa.dashboard_settings import SETTINGS, EDITABLE, SECRETS, MODE_WORDS, KIND_WORDS


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
    temporary = ENV.with_suffix('.tmp')
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(ENV)


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
    """Identify current minis and historical passes in the same model log."""
    if "You are Memory Mini" in req:
        return "recalling"
    if "You decide which of" in req:
        return "dispatching"
    if "memory noticing a pattern" in req:
        return "noticing tastes"
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
    steps = [("dispatching", "routes work to minis, alongside recall"),
             ("recalling", "selects relevant memories, alongside dispatch"),
             ("her reply", "the words you actually see"),
             ("remembering", "decides what to keep, after she answers")]
    out = []
    for i, (name, what) in enumerate(steps):
        ms = by.get(name, [])
        took = f"{statistics.median(ms) / 1000:.1f}s" if ms else "—"
        out.append(f"<div class='pass'><b>{name}</b><span>{what}</span>"
                   f"<em>{took}<i>median</i></em><em>{len(ms)}<i>calls</i></em></div>")
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
         "saved setting; requires a trained Hey Tiwa detector and a bot restart"),
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
        [(rid, False, s, rel, o, note or "", ago(ts) if ts else "Unknown", category, evidence, source, ts)
         for rid, s, rel, o, note, ts, category, evidence, source in DB.execute(
             "SELECT r.rowid, s.name, r.rel, d.name, r.note, r.updated_at, r.category, r.evidence, r.source"
             " FROM relations r JOIN entities s ON s.id = r.src"
             " JOIN entities d ON d.id = r.dst ORDER BY r.updated_at DESC")],
        columns=["#", "forget", "who", "what", "about", "note", "updated", "category", "evidence", "source", "timestamp"])


def episodes_df() -> pd.DataFrame:
    # text != '': blank rows are reflection watermarks, not events (memory.reflect)
    return pd.DataFrame(
        [(i, False, when(ts) if ts else "Unknown", u, t, e, s, ts) for i, u, t, ts, e, s in DB.execute(
            "SELECT id, user, text, ts, evidence, source FROM episodes WHERE text != ''"
            " ORDER BY ts DESC")],
        columns=["#", "forget", "when", "with", "what", "evidence", "source", "timestamp"])



EVIDENCE_LABELS = {'legacy': 'Legacy · unverified', 'explicit': 'Stated', 'observed': 'Observed pattern',
                   'stance': 'Tiwa stance', 'inferred': 'Inferred', 'reported': 'Reported by someone else'}
MEMORY_HINT = 'Select a memory from the list to read its full details.'


def history_df():
    return pd.DataFrame([
        (i, s, r, o, n or '', when(t) if t else 'Unknown', e, q, t)
        for i, s, r, o, n, t, e, q in DB.execute('SELECT * FROM memory_history ORDER BY ended_at DESC, id DESC')
    ], columns=['#', 'who', 'what', 'about', 'note', 'updated', 'evidence', 'source', 'timestamp'])


def memory_frame(kind):
    readers = {'Facts': facts_df, 'Conversations': episodes_df, 'History': history_df}
    if kind not in readers:
        raise gr.Error('Choose a valid memory type.')
    return readers[kind]()


def memory_browser(query="", person="Everyone", kind="Facts", category="All", evidence="All"):
    df = memory_frame(kind)
    who_column = 'with' if kind == 'Conversations' else 'who'
    if person and person != "Everyone":
        df = df[df[who_column] == person]
    if category != 'All':
        df = df[df['category'] == category] if 'category' in df else df.iloc[:0]
    if evidence != 'All':
        df = df[df['evidence'] == evidence] if 'evidence' in df else df.iloc[:0]
    if query and query.strip():
        mask = df.astype(str).apply(lambda col: col.str.contains(query.strip(), case=False, regex=False)).any(axis=1)
        df = df[mask]
    rows = []
    for _, row in df.head(LIVE).iterrows():
        text = row['what'] if kind == 'Conversations' else f"{row['what']} {row['about']}"
        rows.append((int(row['#']), row[who_column], text[:160], row.get('category', '—'),
                     EVIDENCE_LABELS.get(row.get('evidence', 'legacy'), row.get('evidence', 'legacy')),
                     row['when' if kind == 'Conversations' else 'updated']))
    status = (f"**{len(rows)} {'memory' if len(rows) == 1 else 'memories'}** · Select a row to read it in full."
              if rows else "**No matching memories.** Try another search or clear the filters.")
    if len(df) > LIVE:
        status = f'**Showing {LIVE} of {len(df)} memories.** Narrow the search to find older records.'
    if kind == 'History':
        status += ' Superseded records are not current beliefs.'
    return pd.DataFrame(rows, columns=["ID", "Person", "Memory", "Category", "Evidence", "Updated"]), status


def memory_people(current="Everyone"):
    names = set(facts_df()["who"]) | set(episodes_df()["with"]) | set(history_df()['who'])
    return gr.Dropdown(choices=["Everyone"] + sorted(names), value=current if current in names else "Everyone", label='Person')


def memory_signature(row):
    stable = row.drop(labels=['forget', 'updated', 'when'], errors='ignore').to_dict()
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()


def clear_memory_selection():
    return None, MEMORY_HINT, False


def memory_detail(kind, evt: gr.SelectData):
    ident = int(evt.row_value[0])
    df = memory_frame(kind)
    row = df[df["#"] == ident]
    if row.empty:
        return None, "This memory is no longer available.", False
    r = row.iloc[0]
    text = (f"{r['who']} — {r['what']} {r['about']}\n\n{r['note']}" if kind == "Facts"
            else (f"Superseded · {r['updated']}\n{r['who']} {r['what']} {r['about']}\n\n{r['note']}"
                  if kind == 'History' else f"{r['with']} · {r['when']}\n\n{r['what']}"))
    text += (f"\n\nCategory: {r.get('category', 'Not recorded')}"
             f"\nEvidence: {EVIDENCE_LABELS.get(r.get('evidence', 'legacy'), r.get('evidence', 'legacy'))}"
             f"\n\nSource evidence:\n{r.get('source', '') or 'No source recorded. This is not verified evidence.'}")
    if kind == 'Facts':
        text += '\n\nDeleting this record also removes superseded history for the same person and subject.'
    return (kind, ident, memory_signature(r)), text, False


def forget_selected(selection, confirmed, query, person, kind, category="All", evidence="All"):
    if not isinstance(selection, (list, tuple)) or len(selection) != 3 or not confirmed:
        raise gr.Error("Select a memory and confirm deletion first.")
    selected_kind, ident, signature = selection
    df = memory_frame(selected_kind)
    row = df[df['#'] == ident]
    if selected_kind != kind or row.empty or memory_signature(row.iloc[0]) != signature:
        raise gr.Error('This memory changed or is no longer selected. Refresh and select it again.')
    if selected_kind == 'History':
        DB.execute('DELETE FROM memory_history WHERE id=?', (ident,))
        DB.commit()
    else:
        (memory.delete_relation if selected_kind == "Facts" else memory.delete_episode)(DB, ident)
    table, status = memory_browser(query, person, kind, category, evidence)
    return table, status, None, "Memory deleted. Select another row to read it.", False


async def preview_recall(person, question, recent=''):
    if not person or not person.strip() or not question or not question.strip():
        raise gr.Error('Enter a speaker and a message to preview recall.')
    before = DB.execute("SELECT COALESCE(MAX(id),0) FROM log WHERE kind='recall'").fetchone()[0]
    block = await recall.retrieve(DB, person.strip(), question.strip(), recent or '')
    size = len(block.encode('utf-8'))
    count = max(0, len(block.splitlines()) - 1)
    status = f'**{count}/{recall.MAX_RECORDS} records · {size}/{recall.CONTEXT_BYTES} bytes** sent as memory context.'
    log = DB.execute("SELECT text FROM log WHERE kind='recall' AND id>? ORDER BY id DESC LIMIT 1", (before,)).fetchone()
    if log and log[0].startswith('fallback:'):
        status += ' Memory Mini was unavailable or timed out; only interaction preferences and exact relationship lookups are eligible.'
    return block or 'No memory selected for this message.', status


MODEL_NAMES = {
    "openai/gpt-transcribe": "GPT Transcribe · OpenAI",
    "qwen/qwen3-asr-1.7b": "Qwen3 ASR 1.7B · Qwen",
    "openai/whisper-large-v3-turbo": "Whisper Large v3 Turbo · OpenAI",
    "fish-audio/transcribe-1": "Transcribe 1 · Fish Audio",
    "deepseek/deepseek-v4-flash": "DeepSeek V4 Flash",
}


# Public OpenRouter names verified 2026-09-09; custom IDs remain supported.
CHAT_MODELS = {
    "deepseek/deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek/deepseek-v4-pro": "DeepSeek V4 Pro",
    "google/gemini-3.1-flash-lite": "Google Gemini 3.1 Flash Lite",
    "openai/gpt-5.4-mini": "OpenAI GPT-5.4 Mini",
    "anthropic/claude-sonnet-4.6": "Anthropic Claude Sonnet 4.6",
    "qwen/qwen3.5-plus-20260420": "Qwen3.5 Plus",
}
MODEL_NAMES.update(CHAT_MODELS)


def model_choices(options, current, default):
    values = list(dict.fromkeys([*(options or []), default, current]))
    return [(MODEL_NAMES.get(v, v.split("/")[-1].replace("-", " ").title()), v)
            for v in values if v]

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
                   r["updated_at"], r['category'], r['evidence'], r['source']]
                  for r in memory.export_all(DB)["relations"]],
                 ["subject", "relation", "object", "note", "updated_at", "category", "evidence", "source"])


def export_episodes():
    return _dump("episodes.csv",
                 [[when(e["ts"]), e["user"], e["text"], e['evidence'], e['source']]
                  for e in memory.export_all(DB)["episodes"]],
                 ["when", "with", "what", "evidence", "source"])


def export_history():
    rows = memory.export_all(DB)['history']
    columns = ['id', 'subject', 'relation', 'object', 'note', 'ended_at', 'evidence', 'source']
    return _dump('memory-history.csv', [[r[c] for c in columns] for r in rows], columns)


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
DEFAULTS = [default or None for _, rows in SETTINGS for _, _, _, default, _ in rows]


def save_settings(*vals):
    values = {k: str(v or "").strip() for k, v in zip(KEYS, vals)}
    if any('\n' in v or '\r' in v for v in values.values()):
        raise gr.Error("Each setting must be a single line")
    for key, low, high in (("TIWA_SILENCE_S", .3, 5),
                           ("TIWA_RECORD_MAX_S", 2, 30),
                           ("TIWA_WAKE_THRESHOLD", .01, 1)):
        if values.get(key):
            try:
                valid = low <= float(values[key]) <= high
            except ValueError:
                valid = False
            if not valid:
                raise gr.Error(f"{key} must be a number between {low} and {high}")
    write_env(values)
    gr.Info("saved to .env — stop and start her before it takes effect")
    return "**Saved. Restart Tiwa to apply these settings.** Recording samples does not require a restart."


def reload_settings():
    env = read_env()
    return [env.get(k) or b for k, b in zip(KEYS, DEFAULTS)]


def reset_settings(sure):
    if not sure:
        raise gr.Error("tick 'yes, really' first — every setting goes back to default")
    write_env(dict.fromkeys(EDITABLE, ""))
    gr.Info("back to defaults. Your secrets were not touched.")
    return DEFAULTS


def secrets_md() -> str:
    env = read_env()
    return "| secret | |\n|---|---|\n" + "\n".join(
        f"| `{s}` | {'✅ set' if env.get(s) else '❌ missing'} |" for s in SECRETS)


def sample_action(action, ident=None, audio=None, label='', kind='Hey Tiwa', notes='', confirm=False):
    try:
        if action == 'save':
            ident = recordings.save(audio, label, kind, notes)
        elif action == 'edit':
            recordings.edit(ident, label, kind, notes)
        elif action == 'delete':
            if not confirm:
                raise ValueError("Tick Delete this recording before deleting")
            recordings.delete(ident)
            ident = None
        choices = recordings.listing()
        return gr.Dropdown(choices=choices, value=ident), f"{len(choices)} recordings saved locally."
    except (ValueError, OSError, RuntimeError) as error:
        raise gr.Error(str(error)) from None


def sample_load(ident):
    if not ident:
        return None, '', recordings.KINDS[0], '', False
    try:
        return (*recordings.load(ident), False)
    except (ValueError, OSError, KeyError):
        raise gr.Error("Recording unavailable. Refresh the library and select another sample.") from None


def sample_trim(ident, start, end):
    try:
        ident = recordings.trim(ident, start, end)
        return gr.Dropdown(choices=recordings.listing(), value=ident), "Trimmed copy saved. Original kept."
    except (ValueError, OSError, TypeError):
        raise gr.Error("Select a recording and valid start/end seconds; keep at least 0.3 seconds.") from None


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
    gr.HTML("<header class=brand><div class=brand-mark>ท</div><div><h1>Tiwa <span>ทิวา</span></h1><p>Your companion, your settings.</p></div><small>Local control panel</small></header>")

    with gr.Tabs(elem_id="main-nav"):
        with gr.Tab("Overview"):
            live = gr.Checkbox(True, label="live — refreshes every 4s",
                               container=False, elem_classes="live")
            # what she runs on, what is broken, what she holds — no heading, because
            # the unlabelled block is the one that leads
            now = gr.HTML(now_html)

            sec("routing, recall & replies", "median over the last 400 model calls")
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
            gr.Markdown("## Make Tiwa work your way\nStart with **Basics**. Set up listening in **Voice chat** when your wake detector is ready.", elem_classes="settings-lead")
            settings_status = gr.Markdown("**Changes apply after restart.** Empty fields use the default shown below each setting.", elem_classes="settings-notice")
            with gr.Row(elem_classes="settings-actions"):
                save_btn = gr.Button("Save settings", variant="primary")
                reload_btn = gr.Button("Discard unsaved changes")
            fields, env0 = {}, read_env()
            sections = [
                ("Basics", ["Where she thinks", "Discord"], "Choose where responses run and which Discord channel Tiwa uses. API mode keeps your GPU free for games."),
                ("Voice chat", ["Voice chat — activation and transcription", "Her speaking voice"], "1. Collect samples in Wake recordings. 2. Train and select a Hey Tiwa detector. 3. Turn listening on. Start with transcript-only mode to check accuracy."),
                ("Features", ["Music", "Memory", "Eyes"], "Control music, remembering, and image understanding. Leave tuning values at their defaults unless a feature needs adjustment."),
                ("Advanced", ["Logging", "Legacy local speech tests — not used by the wake listener"], "Diagnostics and older local speech experiments. These legacy speech options do not change the new wake-word listener."),
            ]
            with gr.Tabs():
                for title, groups, description in sections:
                    with gr.Tab(title):
                        gr.Markdown(description, elem_classes="intro")
                        for group, rows in SETTINGS:
                            if group not in groups:
                                continue
                            with gr.Group(elem_classes="settings-section"):
                                gr.Markdown(f"### {group}")
                                for start in range(0, len(rows), 2):
                                    with gr.Row():
                                        for key, label, help_, default, opts in rows[start:start+2]:
                                            cur = env0.get(key) or None
                                            info = f"{help_} Default: {default or '(none)'}."
                                            choices = [("On" if o == "1" else "Off", o) for o in opts] if opts and set(opts) == {"0", "1"} else opts
                                            if key == "TIWA_MODE":
                                                choices = [("API — keep GPU free", "api"), ("Local — run on this PC", "local"), ("Mixed — local tools, API replies", "mixed")]
                                            if key == "TIWA_VOICE":
                                                choices = [("Off — music only", "dj"), ("On — speak replies", "full")]
                                            if "MODEL" in key and (opts is not None or key.endswith("API_MODEL") or key in {"TIWA_TOOL_MODEL", "TIWA_EXTRACT_MODEL"}):
                                                choices = model_choices(opts if opts is not None else list(CHAT_MODELS), cur, default)
                                                opts = choices
                                            fields[key] = (
                                                gr.Dropdown(choices=choices, value=cur or default or None, label=label, info=info, allow_custom_value=not (opts and set(opts) == {"0", "1"}))
                                                if opts is not None else gr.Textbox(cur or default, label=label, info=info))
            boxes = [fields[k] for k in KEYS]
            save_btn.click(save_settings, boxes, settings_status)
            reload_btn.click(reload_settings, None, boxes).then(lambda: "**Reloaded saved settings.** Unsaved edits discarded.", None, settings_status)
            for field in boxes:
                field.input(lambda: "**Unsaved changes.** Save, then restart Tiwa to apply.", None, settings_status, show_progress="hidden")
            with gr.Accordion("Connections & troubleshooting", open=False):
                gr.Markdown(secrets_md)
                gr.Markdown("Missing a key? Add it to the local `.env` file. Keys are never displayed here.\n\nListening will remain off without a trained wake model. Recording samples does **not** train or enable the detector.")
            with gr.Accordion("Reset settings", open=False):
                sure_reset = gr.Checkbox(False, label="Reset all settings to defaults; keep API keys")
                gr.Button("Reset to defaults", variant="stop").click(reset_settings, sure_reset, boxes).then(lambda: "**Defaults restored. Restart Tiwa to apply.**", None, settings_status)

        with gr.Tab("Wake recordings") as tab_recordings:
            gr.Markdown("## Teach Tiwa how you call her\nBuild a local library of **Hey Tiwa** samples. Record one phrase per clip, then listen back and label it.", elem_classes="settings-lead")
            gr.Markdown("**Samples only — no training or cloud upload.** Include normal, quiet, and excited speech. Also record background conversation that should not wake her.", elem_classes="settings-notice")
            with gr.Row(equal_height=False):
                with gr.Column(scale=1, min_width=300):
                    gr.Markdown("### 1. Record a sample")
                    mic = gr.Audio(sources=["microphone", "upload"], type="numpy", label="Record Hey Tiwa (up to 60 seconds)", buttons=["download"])
                    gr.Markdown("Use the microphone’s record button, say **Hey Tiwa**, then stop. Allow microphone access when your browser asks. You can also upload an existing clip.")
                    gr.Markdown("No microphone found? Open this local panel in Chrome or Edge and check the browser's microphone permission.")
                    sample_name = gr.Textbox(label="Recording name", placeholder="Krich — normal voice, take 1")
                    sample_kind = gr.Radio(recordings.KINDS, value=recordings.KINDS[0], label="What is in this clip?")
                    sample_notes = gr.Textbox(label="Notes (optional)", placeholder="Quiet room, headset microphone", lines=2)
                    save_sample = gr.Button("Save recording", variant="primary")
                with gr.Column(scale=1, min_width=300):
                    gr.Markdown("### 2. Review your library")
                    library = gr.Dropdown(choices=recordings.listing(), label="Saved recordings", info="Select a sample to play it or edit its details.")
                    library_status = gr.Markdown(f"{len(recordings.listing())} recordings saved locally.")
                    refresh_samples = gr.Button("Refresh library")
                    playback = gr.Audio(label="Playback", interactive=False, type="filepath", buttons=["download"])
                    edit_name = gr.Textbox(label="Recording name")
                    edit_kind = gr.Radio(recordings.KINDS, label="Sample type")
                    edit_notes = gr.Textbox(label="Notes", lines=2)
                    update_sample = gr.Button("Save details")
                    with gr.Accordion("Trim audio — keep a shorter copy", open=False):
                        gr.Markdown("Remove silence or surrounding conversation. Your original stays in the library.")
                        with gr.Row():
                            trim_start = gr.Number(0, label="Start (seconds)")
                            trim_end = gr.Number(label="End (seconds)")
                        trim_sample = gr.Button("Save trimmed copy")
                    with gr.Accordion("Delete selected recording", open=False):
                        delete_confirm = gr.Checkbox(False, label="Delete this recording and its details permanently")
                        delete_sample = gr.Button("Delete recording", variant="stop")
            save_sample.click(lambda a, n, k, d: sample_action('save', audio=a, label=n, kind=k, notes=d), [mic, sample_name, sample_kind, sample_notes], [library, library_status])
            update_sample.click(lambda i, n, k, d: sample_action('edit', ident=i, label=n, kind=k, notes=d), [library, edit_name, edit_kind, edit_notes], [library, library_status])
            trim_sample.click(sample_trim, [library, trim_start, trim_end], [library, library_status])
            delete_sample.click(lambda i, c: sample_action('delete', ident=i, confirm=c), [library, delete_confirm], [library, library_status])
            refresh_samples.click(lambda: sample_action('refresh'), None, [library, library_status])
            tab_recordings.select(lambda: sample_action('refresh'), None, [library, library_status])
            library.change(sample_load, library, [playback, edit_name, edit_kind, edit_notes, delete_confirm])

        with gr.Tab("Memory") as tab_mem:
            gr.Markdown("## Memory\nRead current beliefs, their evidence, and what changed. Tiwa recalls only the details relevant to each message.", elem_classes="settings-lead")
            with gr.Row():
                memory_query = gr.Textbox(label="Search memories", placeholder="Search a name, game, preference, or moment…", scale=3)
                memory_person = memory_people()
                memory_kind = gr.Radio([('Current memories', 'Facts'), ('Episodes', 'Conversations'), ('Superseded history', 'History')], value="Facts", label="Memory type", scale=2)
            with gr.Row():
                memory_category = gr.Dropdown(['All', 'general', 'music', 'games', 'food', 'interaction'], value='All', label='Category', info='Categories are available for current memories.')
                memory_evidence = gr.Dropdown([('All', 'All')] + [(label, value) for value, label in EVIDENCE_LABELS.items()], value='All', label='Evidence')
            memory_status = gr.Markdown(memory_browser()[1])
            selected_memory = gr.State(None)
            with gr.Row(equal_height=False):
                with gr.Column(scale=3, min_width=320):
                    memory_table = gr.Dataframe(memory_browser()[0], interactive=False, wrap=True, show_label=False, max_height=580)
                with gr.Column(scale=2, min_width=280):
                    memory_text = gr.Textbox(value=MEMORY_HINT, label="Memory and evidence", lines=15, interactive=False)
                    with gr.Accordion("Delete this memory", open=False):
                        memory_confirm = gr.Checkbox(False, label="Permanently delete the selected memory")
                        delete_memory = gr.Button("Delete selected memory", variant="stop")
            memory_inputs = [memory_query, memory_person, memory_kind, memory_category, memory_evidence]
            memory_kind.change(lambda k: gr.Dropdown(value='All', interactive=k == 'Facts'), memory_kind, memory_category)
            for control in memory_inputs:
                control.change(memory_browser, memory_inputs, [memory_table, memory_status]).then(
                    clear_memory_selection,
                    None, [selected_memory, memory_text, memory_confirm])
            memory_table.select(memory_detail, memory_kind, [selected_memory, memory_text, memory_confirm])
            delete_memory.click(forget_selected, [selected_memory, memory_confirm, *memory_inputs],
                                [memory_table, memory_status, selected_memory, memory_text, memory_confirm])
            with gr.Row():
                refresh_memory = gr.Button("Refresh memories")
                dl_all = gr.DownloadButton("Export all memories · JSON")
            for trigger in (refresh_memory.click, tab_mem.select):
                trigger(memory_people, memory_person, memory_person).then(memory_browser, memory_inputs, [memory_table, memory_status]).then(
                    clear_memory_selection, None, [selected_memory, memory_text, memory_confirm])
            with gr.Accordion('Preview what Tiwa would recall', open=False):
                gr.Markdown(f'Runs Memory Mini with the configured model. It does not send a reply or save new memories; it may log the model call. '
                            f'The returned memory block is limited to {recall.MAX_RECORDS} records / {recall.CONTEXT_BYTES:,} UTF-8 bytes.')
                with gr.Row():
                    recall_person = gr.Textbox(label='Speaker', placeholder='Krich', scale=1)
                    recall_question = gr.Textbox(label='Message to Tiwa', placeholder='What do you think of Gojo Satoru?', scale=3)
                recall_recent = gr.Textbox(label='Recent conversation (optional)', placeholder='Only needed to resolve references such as “him” or “that song”.', lines=2)
                run_recall = gr.Button('Preview recall')
                recall_status = gr.Markdown()
                recall_text = gr.Textbox(label='Exact memory block', interactive=False, lines=8)
                run_recall.click(preview_recall, [recall_person, recall_question, recall_recent], [recall_text, recall_status])
            with gr.Accordion("Export & reset", open=False):
                gr.Markdown("Download a backup before clearing memories. Clearing current memories also clears all superseded history. Deletion cannot be undone.")
                with gr.Row():
                    dl_facts = gr.DownloadButton("Export facts · CSV")
                    dl_eps = gr.DownloadButton("Export episodes · CSV")
                    dl_history = gr.DownloadButton('Export history · CSV')
                sure_mem = gr.Checkbox(False, label="I understand this permanently clears the selected category")
                for title, category in [("Clear current memories & history", "facts"), ("Clear all episodes", "episodes")]:
                    gr.Button(title, variant="stop").click(lambda sure, c=category: wipe(c, sure), sure_mem, None).then(memory_browser, memory_inputs, [memory_table, memory_status]).then(
                        lambda: (*clear_memory_selection(), False), None, [selected_memory, memory_text, memory_confirm, sure_mem])
            dl_all.click(export_memory, None, dl_all)
            dl_facts.click(export_facts, None, dl_facts)
            dl_eps.click(export_episodes, None, dl_eps)
            dl_history.click(export_history, None, dl_history)

        with gr.Tab("Model calls") as tab_llm:
            gr.Markdown(
                "**Dispatching** routes work while **recalling** selects relevant memory. "
                "**Her reply** uses that context; **remembering** extracts new memories afterward. "
                "Recall can skip a model call when no topical memories are available. "
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
CSS = (Path(__file__).parent / "assets" / "dashboard.css").read_text(encoding="utf-8")

def dashboard_theme():
    theme = gr.themes.Base(primary_hue="red", neutral_hue="gray", spacing_size="md", radius_size="md")
    tokens = theme.to_dict()["theme"]
    theme.set(**{key: tokens[key.removesuffix("_dark")] for key in tokens
                 if key.endswith("_dark") and key.removesuffix("_dark") in tokens})
    return theme


if __name__ == "__main__":
    print(f"control panel: http://127.0.0.1:{PORT}   (ctrl-c to stop)")
    demo.launch(server_name="127.0.0.1",  # localhost: it edits .env and wipes memory
                server_port=PORT, share=False, inbrowser="--open" in sys.argv,
                quiet=True, css=CSS,
                theme=dashboard_theme())
