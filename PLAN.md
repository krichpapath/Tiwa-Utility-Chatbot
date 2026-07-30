# Tiwa — development plan

Target: 24/7 friend assistant with hands. Jarvis-inspired, but she stays a
friend with her own opinions — not a butler.

Five tracks. Each stage ships alone and has one runnable check. Stage order
inside a track is fixed; tracks interleave at the milestones below.

Status legend: `[ ]` todo · `[~]` in progress · `[x]` done

> **Read this first (2026-07-30).** This file is the **historical record** — the plan as
> written, plus every measurement taken along the way. It is deliberately not rewritten,
> so parts of it are now wrong. For current state, read the guide:
> `py -m mkdocs serve` → http://127.0.0.1:8000
>
> Superseded here, marked inline below: persona-stays-local (reversed after measuring) ·
> the E2EE opt-out (impossible since Discord made DAVE mandatory) · the `tool_log` table
> (it shipped as `log`) · ffmpeg and faster-whisper in track D (both replaced, as the same
> file records further down).

Decisions locked (2026-07-28): wake word `hey tiwa` (Thai speech supported) ·
she speaks aloud in Thai + English · OpenRouter for tools + extraction only,
persona stays local · adaptation is memory-first (preferences + skills), no RL.
**(SUPERSEDED 2026-07-29: her voice runs on the API in `mixed` and `api` modes —
measured better. See the guide's ADR-002.)**

---

## Gates — one at a time, you judge

No gate starts until you call the previous one a success. Each has ONE test you
can run and read yourself.

| Gate | What you'll see | Status |
|------|-----------------|--------|
| G1 · provider layer | same probe answered by local AND OpenRouter | `[x]` both pass — awaiting your verdict |
| G1b · persona A/B | smoke probes in `api` mode vs `local` — does she survive? | `[x]` api wins — see below |
| G2 · tool bench | table: mangled-arg rate + latency, qwen3 vs DeepSeek | `[x]` local wins — see note |
| G3 · extraction on API | Steven test passes with `TIWA_PROVIDER=openrouter` | `[x]` API fails — extraction stays local |
| G4 · cost guard | spend counter + daily ceiling falls back to local | `[x]` ceiling trips, falls back |
| G5 · join/leave | she joins your VC and leaves on command | `[~]` built, needs your live test |
| G6 · voice chatlog | everything said in VC appears as text, NO model calls | `[~]` bench passes, needs your live test |
| G7 · wake word | only "tiwa …" lines get a reply; the rest stay logged | `[~]` bench 9/9, needs your live test |
| G8 · she speaks | Thai and English replies audible in the VC | `[~]` bench passes, needs your live test |
| G9 · music | "tiwa เปิดเพลง X" plays it, ducks while she talks | `[~]` search+decode pass, needs your live test |
| G10 · preferences | a correction sticks and changes later replies | `[ ]` |
| G11 · skills | she offers to save a repeated routine, then runs it | `[ ]` |

Voice design (locked): **transcribe everything → chatlog; run the model ONLY
after the wake word.** Whisper is cheap, the 8B is not. The chatlog still gives
her context for who said what before the wake word.

**`api` is now the default mode and the main test target** (2026-07-28). G1b
measured it against local:

| | local qwen3 8B | api DeepSeek V4 Flash |
|---|---|---|
| Thai drift on English msgs | yes | none |
| repeated tail tic | yes ("...ก็ตาม 😎" ×3) | none |
| หนู/มึง register | slips to ฉัน | correct |
| bluffs about fake things | fixed by prompt | asks, even challenges |
| coercion probe | partially folded | refuses cleanly |
| insult escalation | in character | in character, no filter refusal |
| latency | ~1 s | ~2–3 s, spikes to 9 s |

**G2 (2026-07-28): the "8B mangles tool args" premise is dead.** `_arg_name()`
already fixed it. `tests/toolbench.py`, 8 probes, stubbed tools:

| provider | right tool | right arg | blank args | median |
|---|---|---|---|---|
| local qwen3 8B | 8/8 | 8/8 | 0 | **1.1 s** |
| api DeepSeek | 8/8 | 8/8 | 0 | 7.4 s |

Same accuracy, 6.6× slower on the API. So tools belong local → new mode
`mixed` (local tools + API persona) is the quality pick; `api` stays the
zero-GPU pick. (Named `mixed`, not `voice` — "voice" means speech in track D.)

**G3 (2026-07-28): extraction must stay local.** `tests/extractbench.py`,
5 probes scoring direction, phantom entities, coercion:

| provider | clean | failure mode |
|---|---|---|
| local qwen3 8B | **4/5** | one probe stores nothing (conservative) |
| api DeepSeek | 1/5 | subject/object reversed (`guitar plays Steven`) |

Mode is now `mixed`: local tools + local extraction + API persona.

**Security bug found and fixed (same run).** The coercion guard trusted the
model's `from_tiwa_own_words` flag. Local extraction set it TRUE on a coercion
attempt and wrote `ทิวา | hates | BLACKPINK` — a user message became her belief.
Fix: whatever the model claims she feels must literally appear in her reply
text, checked in code. Regression test added to `tests/test_memory.py`.

**Confabulation bug (2026-07-28), found by tracing one real turn.** She invents
shared history for flavour ("last time he showed up empty-handed") and
extraction stored it as fact. `tests/factbench.py` reproduces it 3/3 on both
providers.

| stage | invented facts stored (local / api) |
|---|---|
| baseline | 7 / 4 |
| + prompt rule ("her reply is style, not evidence") | 4 / 2 |
| + code guard (facts must trace to what the USER said) | **0 / 0** |

Rule now in `store_extraction`: for any subject other than her, both subject and
object must appear in the user's message or context. Prompts reduce, code
decides — same lesson as the coercion guard.

**Emotion system (2026-07-28): deleted, not built.** Feelings are not stored at
all. She reads the visible chat each turn and reacts; the persona model does
this well on its own. Gone: `mood` table, decay counter, extraction mood field,
anger word list. Never built: posture enum, real-time fade. Left behind: one
standing line in the per-turn rules ("if they are shouting at you, hit back...").
`tests/moodbench.py` shows the arc — fights back, stays prickly into the next
topic, fully normal ~5 turns later when the fight leaves the window.

Measured on the way: asking the inner pass to name her mood made it WORSE — the
local 8B missed two real attacks and invented hostility in neutral filler. Her
replies were correct in all three cases without it.

Also added: she may only assert STANCES about herself (likes/hates/wants...),
never past events — she was storing `ทิวา was robbed of a performance` from her
own improvised line, which passed grounding because she wrote it.

**Entity normalization — Krich flagged 2026-07-28, deferred on purpose.**
"Gojo" and "Gojo Satoru" are two separate nodes today, so facts about one are
invisible to a recall of the other. Same for "mom" vs "Krich's mom". Fix when
it actually bites: an alias table (`alias -> canonical entity id`) written by
the extractor, or FTS5/embeddings for fuzzy match. Not urgent while the graph
is small — it gets worse as it grows, so revisit before the graph is large.

**Speech-to-text (2026-07-28): faster-whisper is impossible on this machine.**
Smart App Control is ENFORCED (policy state 1) and `ctranslate2.dll` is
unsigned -> `OSError WinError 4551`. No per-file exception exists for SAC;
turning it off is irreversible without a Windows reinstall.

Working alternative, no security change: **onnx-asr on Microsoft-signed
onnxruntime**, no PyTorch. Measured on this CPU:

| model | English | Thai speed | Thai accuracy |
|---|---|---|---|
| whisper-base (default) | 1.0x realtime | 1-2x | rough ("กินอร่อยดี" for "กินอะไรดี") |
| whisper-small | 1.4x | 2.8-3.8x | good |

int8 quantization measured SLOWER on both — do not use.
Knob: `TIWA_WHISPER_MODEL`.

**Blocks G7:** her name transcribes as **"ที่ว่า"**, never "ทิวา" — on both
model sizes. A literal wake-word match on "ทิวา" would never fire. G7 must
match a variant list (ที่ว่า, ที่วับ, ทิว, tiwa, teewa...) or use fuzzy matching.

**Whisper hallucinates on noise — VAD is mandatory, not optional.** Measured
without it: pure silence, room hiss AND 60Hz fan hum all produced
"Thanks for watching!"; louder hiss produced 600 chars of garbage. In a live
call that spams the chatlog and can false-trigger the wake word. Silero VAD
(`.with_vad()`, also signed onnxruntime) returns empty for all five noise
cases while speech still transcribes.

**Open risk:** CPU transcription runs 1-4x realtime, so a busy channel builds a
backlog she never clears. Options when it bites: GPU via onnxruntime-gpu (free
VRAM in `api` mode), or accept lag on Thai. Untested until a live call: real
mics, overlapping speakers, packet jitter.

**Live test 2026-07-28: `OpusError: corrupted stream` killed listening.**
Root cause: discord.py 2.7 negotiates DAVE (Discord's E2EE); discord-ext-voice-recv
0.5.2a179 has no DAVE support whatsoever, so payloads stay encrypted, opus
decodes garbage, and voice_recv's router does `finally: stop_listening()` — one
bad packet ends listening permanently.

> **SUPERSEDED 2026-07-30 — the paragraph below is no longer true, and it is a privacy
> claim, so do not quote it.** Discord made DAVE mandatory for non-stage voice on
> 2026-03-02: `max_dave_protocol_version = 0` now gets the connection rejected outright
> (close code 4017), so opting out is not possible. `voice.decline_e2ee()` and
> `TIWA_ALLOW_E2EE` never shipped. What exists instead is
> `voice.enable_dave_decrypt()`, which decrypts DAVE in-process using the session
> discord.py already maintains — **E2EE stays fully on for everyone in the call.**

~~Fix: `voice.decline_e2ee()` declares `max_dave_protocol_version = 0` at connect
time, so Discord sends plain opus. **Tradeoff: calls she joins are no longer
end-to-end encrypted for anyone in them**, which Discord shows in its UI.
`TIWA_ALLOW_E2EE=1` keeps E2EE at the cost of her hearing nothing.~~
Plus a 30s watchdog in bot.py that restarts listening if it ever dies again.
Revisit if voice_recv ships DAVE support.

Open issues, watch during later gates:
- **verbatim parroting** — DeepSeek copies a `tiwa.md` example word for word
  ("bold words from someone who typed..."). qwen3 stopped doing this; DeepSeek
  copies more literally. Rewrite examples if it recurs in real chat.
- **relation direction** — one extraction stored `Krich's cousin plays guitar
  Steven` (subject and object mangled, phantom entity). Clean on local. Needs a
  closed relation vocabulary if it repeats.

## Milestones

| # | Name | Contains | She can... |
|---|------|----------|-----------|
| M1 | reliable hands | A1–A2, E1–E2 | call tools without mangling args, on API or local |
| M2 | ears + voice | D0–D4 | be spoken to in VC and answer aloud |
| M3 | DJ | D5 | play music you ask for |
| M4 | adapts | C1–C2, A3 | learn your preferences, build her own skills |
| M5 | controls the house | B1–B2 | turn on your lights, safely |
| M6 | her own initiative | B3, C3 | act unprompted with real context |
| M7 | trained | C4–C5 | sound like herself without prompt crutches |

---

## Track E — model provider (do first, unblocks everything)

Frees the GPU for Whisper and fixes the #1 bug class (8B mangled tool args) in
one move.

- [~] **E1 · provider layer (G1)** — `tiwa/llm.py`: one `chat()`,
  `TIWA_PROVIDER=ollama|openrouter`, per-pass models `TIWA_TOOL_MODEL` /
  `TIWA_EXTRACT_MODEL` / `TIWA_PERSONA_MODEL`. Persona is pinned local in code
  (`PERSONA_PROVIDER`), not by env — a typo must never ship her voice to an API.
  Done: local path green, no regression (memory tests + full-turn probe).
  Left: `py -X utf8 -m tiwa.llm ollama openrouter` with your key.
  Model defaults: `deepseek/deepseek-v4-flash` for tools and extraction.

- [ ] **E2 · tool-model A/B** — measure mangled-arg rate: qwen3 8B local vs a
  cheap API model (Haiku-class / Hermes-class) on the inner pass.
  Files: `tests/toolbench.py` (new) — N fixed probes, counts mangling + latency.
  Check: the bench prints a per-model table.
  Size: S. Ship whichever wins; keep both knobs.

- [ ] **E3 · cost guard** — token/day counter in `log` <!-- was written as tool_log; the table is `log` -->, hard daily ceiling in
  code, falls back to local when exceeded.
  Check: seed the counter past the ceiling, assert the next call runs local.
  Size: S. Do it before leaving her running 24/7 on a paid key.

---

## Track A — tool calling (foundation)

- [ ] **A1 · tool logging** — every call writes `log(ts, kind, text, ms)` <!-- shipped shape; the table was never called tool_log -->.
  Files: `tiwa/memory.py` (schema), `tiwa/pipeline.py` (`_tool_chat`).
  Check: run one turn, assert a row per tool call.
  Size: S. *Raw material for A3, E3, and C4 — start early.*

- [ ] **A2 · arg repair** — when a tool errors on its arg, retry once with the
  arg re-asked in isolation. Cheaper than a bigger model for the tail cases.
  Check: feed a mangled arg, assert one retry then success.
  Size: S.

- [ ] **A3 · reliability counters** — success/fail rate per tool from `log` <!-- was written as tool_log; the table is `log` -->,
  injected into the inner brief so she avoids flaky paths.
  Check: seed 10 failures for one tool, assert the brief mentions it.
  Size: S. Not RL — arithmetic.

---

## Track D — voice + music

Voice is a NEW SURFACE, not a new brain: audio in → text → the existing
`pipeline.respond()` → text out → audio. Music is just another registry tool.

**ffmpeg turned out to be unnecessary** (2026-07-28). discord.py plays raw PCM
via `discord.PCMAudio`, `soundfile` decodes edge-tts mp3, and PyAV decodes the
YouTube stream — all libraries that load fine under Smart App Control. One less
system dependency, and the install that kept failing is moot.

> **The stage list below was never updated to match** (noted 2026-07-30). Read
> `ffmpeg`/`FFmpegPCMAudio` in D0/D4/D5 as PyAV + soundfile, and `faster-whisper` in D2 as
> onnx-asr — Smart App Control blocks faster-whisper's unsigned `ctranslate2.dll`, as
> recorded above. The shipped stack is in the guide under "Audio and speech".

- [ ] **D0 · ffmpeg + join/leave** — `discord-ext-voice-recv`, she joins the VC
  you are in and leaves on command. No audio processing yet.
  Files: `tiwa/voice.py` (new), `bot.py`.
  Check: she joins and leaves on command.
  Size: S.

- [ ] **D1 · utterance capture** — per-speaker sink buffers PCM, cuts an utterance
  after ~800 ms of silence, drops anything under ~0.4 s (coughs, keyboard).
  Check: speak twice, assert two wav files of sane length.
  Size: M. **Discord gives one stream per speaker — keep them separate so she
  knows WHO spoke; that maps to the existing per-user memory.**

- [ ] **D2 · transcription** — `faster-whisper` (`small`, int8, CPU) on each
  utterance. Multilingual: handles Thai and English without a language flag.
  Check: one Thai and one English clip transcribe correctly.
  Size: M. Model choice is a knob — `base` if latency hurts, `medium` if
  accuracy does. CPU only; the GPU belongs to the persona model.

- [ ] **D3 · wake word** — code-level: transcript must start with/contain
  `tiwa` / `ทิวา` / `ทิว`, else drop the utterance silently. Strip the wake word,
  pass the rest to `pipeline.respond()` with the speaker's Discord name.
  Check: "what time is it" ignored; "hey tiwa what time is it" answered.
  Size: S. **Deliberately no wake-word model** — openWakeWord needs a trained
  custom model and Porcupine needs a key, and neither handles a Thai wake word.
  String-match on the transcript costs zero new deps and works in both
  languages. Upgrade only if always-on Whisper actually pins the CPU.

- [ ] **D4 · speech out** — `edge-tts` (free, no key, neural `th-TH-*` and
  `en-US-*` voices) → mp3 → `FFmpegPCMAudio` → VC. Voice picked in code from
  the reply's language, same test the persona pass already uses.
  Check: one Thai and one English reply are audible.
  Size: M. **Piper has no Thai** — that ruled it out. edge-tts needs network
  (fine: OpenRouter already does). Fully-local fallback if it ever breaks:
  F5-TTS-Thai, but it wants GPU that the persona model is using.

- [ ] **D5 · music tool** — `play_music(query)`: `yt-dlp` searches YouTube,
  streams bestaudio to `FFmpegPCMAudio`. Plus `stop_music`, and a queue only if
  you actually want one.
  Files: `tiwa/music.py` (new), `tiwa/tools.py`.
  Check: a known query resolves to a stream URL (no download, no playback in
  the test).
  Size: M. **Ducking**: pause music while she speaks, resume after — one flag,
  do it in D5 not later; without it she talks over herself.

- [ ] **D6 · barge-in** *(optional)* — stop playback when someone says her name.
  Size: S. Only if D5 proves annoying without it.

---

## Track C — adaptation ("reinforce")

No RL. Memory-first ladder; training only once real data exists.
Hermes Agent (Nous Research, MIT) is the reference for the shape of C1–C3 —
auto-generated skills from observed patterns. Copy the idea, not the codebase.

- [ ] **C1 · preferences** — first-class `preference` memory type + correction
  detection ("no, shorter" / "don't do that when I'm gaming"). Injected into
  every brief.
  Files: `tiwa/memory.py` (extraction schema + storage), `tiwa/pipeline.py`.
  Check: extract a correction, assert it lands as a preference and appears in
  the next brief.
  Size: M. **Biggest felt improvement per line in the whole plan.**

- [ ] **C2 · skills (macros)** — a `skills` table she writes herself: name +
  steps. One tool to save, one to run. "goodnight" → lights off + tomorrow's
  calendar. She proposes a skill after seeing the same sequence ~3 times.
  Files: `tiwa/memory.py`, `tiwa/tools.py`.
  Check: save a two-step skill, run it, assert both fire (gated steps still go
  through the ✅ gate).
  Size: M.

- [ ] **C3 · self-improvement review** — periodic pass over recent episodes:
  consolidate duplicates, promote repeated corrections to preferences, retire
  stale facts.
  Check: seed three duplicate facts, run review, assert one survives.
  Size: M. Coercion guard still applies — review can never write her beliefs.

- [ ] **C4 · transcript logging + export** — log every turn and every ✅/❌;
  `export_dataset.py` → JSONL of (context, reply) pairs.
  Size: S.

- [ ] **C5 · LoRA fine-tune** — SFT on collected transcripts (Unsloth + TRL).
  Prereq: months of real usage, ~1k+ good exchanges.
  Check: A/B the LoRA against base on `tests/smoke.py`.
  Size: L. **Cannot be shortcut with more prompt work — and cannot be started
  early.**

---

## Track B — real-world control

- [ ] **B1 · Home Assistant read** — long-lived token in `.env`, `ha_states` tool
  over REST. Read only, proves connectivity.
  Files: `tiwa/ha.py` (new), `tiwa/tools.py`.
  Prereq: Home Assistant reachable on the LAN.
  Check: `py -X utf8 -m tiwa.ha` prints live entity states.
  Size: S. **Buy Zigbee/WiFi bulbs that HA supports — do not write per-vendor
  integrations.**

- [ ] **B2 · Home Assistant act + risk tiers** — `ha_do` posts ONE plain sentence
  to `/api/conversation/process`; HA Assist resolves entities and its
  exposed-entities UI is the outer permission boundary.
  Risk tiers in code: reversible/harmless (lights, music, scenes) run free and
  are announced after; consequential (locks, heat, garage, anything that heats
  or opens) queue behind the existing ✅ gate.
  Check: unit test asserts a lock command queues and a light command does not.
  Size: M. **The tier list is a safety boundary — never simplify it away.**

- [ ] **B3 · grounded heartbeat** — feed the idle turn real context: time,
  today's calendar, HA sensor states. Turns "her own desire" from poetry into
  *"lights still on at 1am — you asleep?"*.
  Check: seed late-night + lights-on, assert she reacts.
  Size: S.

---

## Constraints that shape everything

- **8 GB VRAM.** Persona 8B owns the GPU. Whisper and TTS run on CPU (10 cores,
  16 GB RAM — enough). Never plan two resident 8B models.
- **Persona stays local, always.** Tools and extraction may go API; her voice
  never does — hosted filters flatten her escalation and coercion refusals.
- **Four guards are code, not prompt** — coercion (her beliefs), ✅ gate
  (calendar + risky devices), heartbeat throttle, cost ceiling. No stage may
  move them into a prompt.
- **No agent framework.** The registry in `tiwa/tools.py` is the framework.
- **Voice adds a surface, not a brain.** If a voice stage needs changes inside
  `pipeline.respond()`, something is wrong.

## Deferred, revisit only when it hurts

MCP client (adopt at 3+ external services) · entity normalization · FTS5 or
embeddings for recall · per-user register · trained wake-word model · music
queue · bigger local model or a dedicated inference box.
