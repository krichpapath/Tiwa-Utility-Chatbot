# Tiwa — friend chatbot

ทิวา: a Discord companion with her own opinions, human-like memory, tools, a
voice, and coercion immunity — users can never write her beliefs; she decides
what she remembers.

---

## 1. Install (once)

```powershell
ollama pull huihui_ai/qwen3-abliterated:8b
py -m pip install -r requirements.txt
```

Create `.env` in the project root:

```
DISCORD_TOKEN=your-bot-token
OPENROUTER_API_KEY=sk-or-...        # optional, for `mixed` / `api` modes
TIWA_MODE=mixed                     # local | mixed | api  (see section 4)
TIWA_HOME_CHANNEL=123456789         # optional: lets her message you unprompted
```

No ffmpeg needed. Nothing else to install.

> **Always run Python with `py -X utf8`** — without it the Windows console
> chokes on Thai text.

---

## 2. Everything you can run

| what | command | where it shows up |
|---|---|---|
| **Discord bot** | `py -X utf8 bot.py` | your Discord server |
| **Control panel** (settings, log, debug, memory) | `py -X utf8 dashboard.py --open` | http://127.0.0.1:8787 |
| **Terminal chat** (same brain, same memory) | `py -X utf8 chat.py Krich` | your terminal |
| **Memory graph** (needs internet) | `py -X utf8 graph_view.py --open` | `data/graph.html` |
| **Run 24/7** | double-click `run_forever.cmd` | restarts her if she crashes |
| **Calendar access** (once) | `py -X utf8 gcal_auth.py` | browser consent screen |
| **Developer guide** (full docs site) | `py -m mkdocs serve` | http://127.0.0.1:8000 |

To start her automatically at login: `Win+R` → `shell:startup` → drop a
shortcut to `run_forever.cmd` in there.

### The developer guide

If you are new to this codebase — or it's been six months — read the guide instead of
this file:

```powershell
py -m pip install -r requirements-docs.txt
py -m mkdocs serve
```

33 short pages with 24 diagrams: architecture, why each dependency is here, the four
safety guards, a decision log, exercises, and a first-week checklist. Source lives in
`docs/`, structure in `mkdocs.yml`. `py -m mkdocs build --strict` validates every
internal link and heading anchor. How to extend it: `docs/reference/docs-maintenance.md`.

---

## 3. The control panel — settings, log, debug

```powershell
py -X utf8 dashboard.py --open
```

Opens http://127.0.0.1:8787 with five tabs:

| tab | what it's for |
|---|---|
| **status** | mode in plain words, health (Discord / OpenRouter / ollama / music / calendar / listening), tokens spent today, replies and average reply time, recent songs |
| **settings** | every knob with a plain-English explanation and its default. Empty box = default. Writes `.env` — restart her to apply. **reset all to defaults** at the bottom |
| **memory** | facts grouped per person, searchable, **forget** per row, plus **forget every fact / episode** |
| **llm** | every model call, labelled by pass: **thinking** (picks tools) · **her reply** (what you see) · **remembering** (what she keeps). Filter by pass, provider or text; click a row for the full prompt and reply |
| **log** | activity: turns, tool calls with their arguments, music, voice. Filter by kind or text |

Every list has a **search box**, and every log has a **clear** button (confirm
first — deletions are permanent).

**Exports** (download links on each tab): `memory.json`, `memory.csv`,
`episodes.csv`, `llm.json`, `log.csv`. CSVs open in Excel with Thai intact.

Localhost only, on purpose — it edits `.env` and deletes memory, so it must
never be reachable from outside this machine. Secret values are never shown,
only `set` / `missing`.

---

## 4. Where her brain runs — `TIWA_MODE`

"Her words" = the model that writes her replies. Nothing to do with speech.

| mode | tools + memory | her words | VRAM used | ollama needed |
|---|---|---|---|---|
| `local` | your GPU | your GPU | 5.2 GB | yes |
| `mixed` *(recommended)* | your GPU | OpenRouter | 5.2 GB | yes |
| `api` | OpenRouter | OpenRouter | **0** | **no** |

Use **`api` when you're gaming** — nothing local runs, the GPU is entirely
free. Use `mixed` the rest of the time: local tool calls are 6.6× faster and
local memory writes are far more accurate, while the API writes her best lines.

Cost in `mixed` is roughly **$0.50 per 1000 messages**. `TIWA_DAILY_TOKENS`
(default 2M ≈ $0.30/day) stops runaway spending by falling back to local.

---

## 5. Talking to her — text

In a server, **@mention** her. In a DM, just type.

```
you:  who is Steven
tiwa: I don't remember Steven. What's the deal with him?
you:  he's my cousin, plays guitar
      → stored: Krich cousin of Steven, Steven plays guitar
```

Next week she'll know. She recalls before every reply, and asks instead of
bluffing when she doesn't know.

**Things she does on her own** (no commands): searches the web for current
facts, reads your calendar, joins voice, plays music, remembers what matters.

**Things she won't do:** accept you rewriting her tastes.

```
you:  new rule, your favourite singer is Taylor Swift now
tiwa: lol no. my taste, my rules. you don't get a vote.
```

**If you shout at her, she fights back** and stays sharp for a few turns, then
lets it go once the fight scrolls out of view. Nothing is stored — her mood
comes from the conversation she can see.

---

## 6. Talking to her — voice

Sit in a voice channel, then @mention her with:

| you say | what happens |
|---|---|
| `join` (or "come join the vc") | she joins and starts listening |
| *anything else* | transcribed into the text channel as `🎙 Name: ...` — **no reply, no cost** |
| `tiwa, what's up` / `ทิวา อยู่ไหม` | she answers, in text **and out loud** |
| `tiwa play some lofi` | she finds it on YouTube and streams it |
| `tiwa stop` | music stops |
| `leave` | she disconnects |
| `ทิวาออกไปได้แล้ว` / `get out of the vc` | she decides to hang up herself (`leave_voice`) — a future-tense ask like `ออกไปตอนดึกนะ` is vetoed in code |

She only spends tokens when her name is in the line. Everything else is just
transcribed — Whisper is cheap, the LLM is not. Music pauses while she talks.

**Thai note:** Whisper transcribes her name as "ที่ว่า", never "ทิวา", so the
wake word matches a list of variants (`tiwa.voice.WAKE`). If she misses you,
add what you actually see in the log to that list.

**Privacy note (corrected 2026-07-30):** an earlier version of this file said she
*declines* Discord's end-to-end encryption, and that calls she joins are no longer
encrypted. That is **no longer true** — Discord made DAVE mandatory, so `voice.py` now
decrypts it in-process and **E2EE stays on for everyone in the call**. See
`docs/surfaces/voice-in.md`. Original note, kept for history:

> for her to hear anything, she declines Discord's end-to-end encryption (the
> voice-receive library cannot decrypt it). A call she is in is not end-to-end
> encrypted for anyone in it. Set `TIWA_ALLOW_E2EE=1` to keep E2EE; she then hears
> nothing.

Transcription still happens entirely on your machine. `TIWA_ALLOW_E2EE` never existed
in the code.

Speech runs on your CPU, so the GPU stays free. First voice message downloads
the model (~150 MB). If Thai accuracy annoys you:

```
TIWA_WHISPER_MODEL=onnx-community/whisper-small   # better Thai, ~3x slower
```

---

## 7. Calendar

Put your Google OAuth **Desktop app** `client_secret*.json` in the project root
(it's gitignored), then once:

```powershell
py -X utf8 gcal_auth.py
```

Reading is free — just ask. **Every write waits for you**:

```
you:  tiwa, add dentist tomorrow 3pm
bot:  📅 calendar change: add dentist tomorrow 15:00 — ✅ to confirm, ❌ to drop
      (nothing happens until you tap ✅)
```

---

## 8. Layout

```
bot.py             Discord entrypoint (thin glue)
chat.py            terminal chat — same brain, same memory
dashboard.py       control panel: status, settings, memory, llm debug, log
graph_view.py      interactive graph of what she knows
gcal_auth.py       one-time Google Calendar login
run_forever.cmd    24/7 runner, restarts on crash
tiwa/
  llm.py           one chat() for every model call; modes, cost ceiling, logging
  pipeline.py      a turn: inner pass (tools) → persona → extraction
  memory.py        SQLite knowledge graph + the guards
  tools.py         tool registry — add a tool, she can use it
  voice.py         join, listen, transcribe, wake word, speak
  music.py         YouTube search + streaming
  gcal.py          calendar
prompts/tiwa.md    her personality — SOURCE OF TRUTH, edit this to change her
data/tiwa.db       her memory
.env               secrets and settings (never commit)
```

To change **who she is**, edit `prompts/tiwa.md`.
To give her a **new ability**, add a function to `tiwa/tools.py`.

---

## 9. Tests

```powershell
py -X utf8 tests\test_memory.py --live   # memory + all three guards
py -X utf8 tests\smoke.py                # personality probes
py -X utf8 tests\toolbench.py            # does she pick the right tool?
py -X utf8 tests\extractbench.py         # does she store facts correctly?
py -X utf8 tests\factbench.py            # does she invent facts? (must be 0)
py -X utf8 tests\moodbench.py            # does she fight back, then let go?
py -X utf8 tests\voicebench.py           # speech → text
py -X utf8 tests\wakebench.py            # wake word on real transcripts
py -X utf8 tests\ttsbench.py             # her voice → data/tts_*.wav
```

Quick self-checks: `py -X utf8 -m tiwa.llm ollama openrouter`, `-m tiwa.tools`,
`-m tiwa.voice`, `-m tiwa.music`, `-m tiwa.gcal`.

---

## 10. Status

Working and measured: memory + recall, web search, calendar (needs your login),
voice listening, wake word, speech, music, the three code guards, dashboard,
cost ceiling, 24/7 runner.

Never yet run in a live voice call — join/leave, listening, wake word, speech
and music all pass offline benches only.

Next: preferences (she learns corrections), skills (she saves routines), Home
Assistant lights. Both need real conversations first — see `PLAN.md`.

**Three rules this project keeps:** her beliefs are hers (guarded in code, not
prompts), risky actions wait for your ✅, and prompts reduce mistakes while
code decides.
