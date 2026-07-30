# Decision log

Lightweight ADRs for the choices you'd otherwise have to reverse-engineer. Each is
**context · options · decision · consequences**.

Most numbers here come from `PLAN.md`, where they were recorded when the measurement was
taken. Where no measurement exists, it says so.

---

## ADR-001 · The tool registry is the framework

**Context.** She needs to call tools. Every tutorial reaches for an agent framework.

**Options.** LangChain / LlamaIndex · a custom orchestration layer · a decorator and a dict.

**Decision.** A decorator and a dict — about 30 lines in `tiwa/tools.py`.

**Consequences.** No indirection between "she called a tool" and the function that ran.
Tools are testable without any framework mocking. The cost: no free multi-step planning,
retries, or streaming — all of which would have to be hand-written if ever needed.
Currently the tool loop is capped at 3 rounds and that has been enough.

---

## ADR-002 · Her voice may run on a hosted API {#adr-002}

**Context.** The original rule was absolute: *tools and extraction may go to an API, her
voice never does* — hosted filters would flatten her escalation and coercion refusals.
Then it was measured.

**Options.** Persona local always · persona on API always · one knob.

**Decision.** One knob (`TIWA_MODE`). `mixed` and `api` both put her voice on the API.

**Measured** (`tests/smoke.py`, G1b):

| | local qwen3 8B | API DeepSeek V4 Flash |
|---|---|---|
| Thai drift on English messages | yes | none |
| Repeated tail tic | yes | none |
| หนู/มึง register | slips | correct |
| Coercion probe | partially folded | refuses cleanly |
| Latency | ~1 s | ~2–3 s, spikes to 9 s |

**Consequences.** Better writing and correct register, at the cost of money, latency
spikes, and *some* softening. The API model has also been caught parroting an example
from `tiwa.md` verbatim. `PLAN.md`'s "Constraints" section still states the old rule and
is **superseded by this ADR**.

---

## ADR-003 · Tools and extraction stay local in `mixed`

**Context.** If the API writes better prose, maybe it should do everything.

**Options.** All API · all local · split by pass.

**Decision.** Split. Tools and extraction local, persona API.

**Measured.** Tool calling (`tests/toolbench.py`, 8 probes): both providers 8/8 correct
tool *and* argument, 0 blank args — but **1.1 s local vs 7.4 s API**, 6.6× slower from
Thailand. Extraction (`tests/extractbench.py`, 5 probes): local 4/5, API 1/5, the API
reversing subject and object into `guitar plays Steven`.

**Consequences.** `mixed` is the quality pick. A `hybrid` mode (API tools + local voice)
was implemented and **dropped** as strictly worse. Extraction has since improved on the
API after prompt work, so `api` mode is viable — but `mixed` remains better where a GPU
exists.

---

## ADR-004 · The coercion guard lives in code {#adr-004}

**Context.** Users must never be able to write her beliefs. First implementation asked the
extractor to flag whether she said something herself.

**Options.** Trust the model's flag · verify in code · store nothing about her at all.

**Decision.** Verify in code, three stacked checks — see [the guards](../concepts/guards.md).

**Why.** The prompt-only version **leaked**. The local extractor set
`from_tiwa_own_words: true` on a coercion attempt and stored `ทิวา | hates | BLACKPINK`
from a reply that never mentioned BLACKPINK.

**Consequences.** She can only ever assert *stances* about herself, never past events —
which also killed `ทิวา was robbed of a performance`, invented in her own reply. Regression
test in `tests/test_memory.py`. **Do not move this back into a prompt.**

---

## ADR-005 · Facts must trace to what the user said

**Context.** She invents shared history for flavour. Extraction believed it.

**Options.** Prompt rule only · code grounding check · stop extracting from her replies.

**Decision.** Both prompt and code. For any subject other than her, subject and object
must appear in what the user said.

**Measured** (`tests/factbench.py`):

| stage | invented facts stored (local / api) |
|---|---|
| baseline | 7 / 4 |
| + prompt rule | 4 / 2 |
| + code check | **0 / 0** |

**Consequences.** The clearest evidence for *prompts reduce, code decides*. Some real
facts stated only by her are now lost — accepted, because a false memory is worse than a
missing one.

---

## ADR-006 · The emotion system was deleted, not built

**Context.** She should react to being insulted. Built: a `mood` table, a decay counter,
an extraction mood field, an anger word list. Planned: a posture enum and real-time fade.

**Options.** Finish the mood system · simplify it · delete it.

**Decision.** Delete all of it. Feelings are re-derived each turn from the visible chat.

**Why.** Asking the inner pass to name her mood made things **worse** — the local 8B
missed two real attacks and invented hostility in neutral filler, while her replies were
correct in all three cases without it.

**Consequences.** Her emotional range is whatever the persona model produces, not five
enum values. A fight lasts exactly as long as it's on screen — about five turns
(`tests/moodbench.py`). Nothing to migrate, nothing to decay, no state to corrupt. One
line of standing per-turn rules is all that remains.

---

## ADR-007 · No ffmpeg binary

**Context.** Discord audio conventionally needs ffmpeg. Installing it kept failing here.

**Options.** Fix the ffmpeg install · bundle it · decode in-process.

**Decision.** In-process: PyAV for the YouTube stream, soundfile for TTS mp3,
`discord.PCMAudio` for playback.

**Consequences.** No system dependency, everything installs from pip, and it works under
Smart App Control. PyAV's API is now load-bearing in `music.Stream` — including its
libavformat reconnect options, which a subprocess would not have exposed as cleanly.

---

## ADR-008 · onnx-asr instead of faster-whisper

**Context.** Speech-to-text on Windows with Smart App Control **enforced**.

**Options.** faster-whisper · openai-whisper + PyTorch · onnx-asr on onnxruntime.

**Decision.** onnx-asr.

**Why.** `faster-whisper` ships an unsigned `ctranslate2.dll` → `OSError WinError 4551`.
Smart App Control has no per-file exception and disabling it is irreversible without a
Windows reinstall. `onnxruntime` is Microsoft-signed and loads. `openai-whisper` would
drag PyTorch in for no benefit on CPU.

**Consequences.** Speech works at all — but CPU-only, 1–4× realtime, and Thai accuracy is
the reason the feature is currently off. `int8` quantization measured *slower*.

---

## ADR-009 · Voice activity detection is mandatory

**Context.** Whisper transcribing an open mic.

**Decision.** Silero VAD in front of Whisper, not optional.

**Why.** Without it: pure silence, room hiss, and 60 Hz fan hum **all** produced
*"Thanks for watching!"*; louder hiss produced 600 characters of garbage. In a live call
that spams the chat log and can false-trigger the wake word.

**Consequences.** One more model in the chain (also signed onnxruntime). All five noise
cases return empty while real speech still transcribes.

---

## ADR-010 · The wake word is a string match

**Context.** Only run the expensive model when she's addressed.

**Options.** openWakeWord (needs a custom trained model) · Porcupine (needs a key) ·
match the transcript.

**Decision.** Match the transcript, with a variant list and fuzzy matching, requiring the
line to **start** with her name.

**Why.** Neither wake-word engine handles a Thai wake word, and string matching costs zero
new dependencies. Critically, Whisper transcribes her name as **"ที่ว่า"**, essentially
never as "ทิวา" — so a literal match would never have fired anyway.

**Consequences.** Tunable thresholds (`TIWA_WAKE_FUZZ`, `TIWA_WAKE_FUZZ_TH`) and 32 cases
in `tests/wakebench.py`. Revisit only if always-on Whisper pins the CPU.

---

## ADR-011 · One string argument per tool

**Context.** The local 8B mangled structured arguments — nested them, renamed keys.

**Options.** A bigger model · JSON-schema-constrained tool args · one flat string.

**Decision.** One string named `name`, plus `_arg_name()` to dig through whatever arrives.

**Consequences.** 0 blank arguments across 108 logged calls. Structured data is minted
later by schema-constrained calls (`gcal._EVENT_FORMAT`), never by the tool-calling model.
The premise that motivated this is now partly obsolete — both providers score 8/8 — but
the constraint costs nothing and removes a whole failure class.

---

## ADR-012 · Delete `now_playing`, inject action state instead

**Context.** She needed a tool call to learn what she was playing, and separately claimed
to be playing things she wasn't.

**Options.** Keep the tool and fix the lying · inject state every turn · both.

**Decision.** Inject live state every turn via `pipeline._doing()`, and delete the tool.

**Consequences.** One fewer round-trip, one fewer tool competing for attention (which
mattered — missing `play_music` was an active bug). Nine tools instead of ten.
`_doing()` is the extension point for every future capability: one `if` per subsystem.
It's still a prompt, so ~1 turn in 6 she'll still role-play an action she didn't take.

---

## ADR-013 · The local model is an abliterated build

**Context.** `local` mode needs a model that stays in character when a conversation gets
hostile.

**Decision.** `huihui_ai/qwen3-abliterated:8b`.

**Why.** Freedom of speech. She's meant to swear, insult back, and refuse *her own way*
rather than emit a policy notice. A stock instruct model breaks character exactly when she
should be most herself. Qwen3 also handles Thai well.

**Consequences.** No safety net from the model — the guards in code are the only limit,
which is consistent with the rest of the architecture. Fits 8 GB VRAM.

---

## ADR-014 · Decrypt DAVE rather than opt out of it

**Context.** Discord made DAVE end-to-end voice encryption mandatory on 2026-03-02.
`discord-ext-voice-recv` 0.5.2a179 has no DAVE support, so opus received garbage and its
router killed listening permanently after one bad packet.

**Options.** Declare `max_dave_protocol_version = 0` (the old fix) · wait for upstream ·
decrypt in-process.

**Decision.** Decrypt in-process, using the DAVE session discord.py already maintains.

**Why.** Opting out is no longer possible — the connection is rejected with close code
4017. Waiting means no voice feature at all.

**Consequences.** **E2EE stays fully on for everyone in the call** — better than the old
approach, which disabled it. Cost: a monkey-patch against an alpha library, to be deleted
when `voice_recv` ships its own. `README.md` and `PLAN.md` still describe the old
opt-out approach and are wrong.

---

## ADR-015 · Adaptation is memory-first, not reinforcement learning

**Context.** "Make her learn from me" invites an RL answer.

**Options.** RLHF / a reward model · copy Hermes Agent's auto-generated skills · a memory
ladder.

**Decision.** A memory ladder: preferences, then self-written skills, then a periodic
review pass. Fine-tuning (LoRA/SFT) only after months of real logs exist.

**Consequences.** Nothing to train, nothing to serve, and every step ships alone. Hermes
Agent (Nous Research, MIT) is a reference for the *shape*, not a dependency. The two
ladder steps are still unbuilt — [where to go next](next.md).

---

## ADR-016 · MkDocs Material for this guide

**Context.** The project is Python with no Node toolchain and no build step.

**Options.** VitePress · Docusaurus · Astro Starlight · MkDocs Material.

**Decision.** MkDocs Material.

**Why.** One `pip install`, same package manager as the code, no second language runtime
and no `node_modules`. Search, dark mode, grouped nav and Mermaid all ship built in. And
the project-specific reason: Smart App Control has already blocked two libraries for
unsigned native binaries — Node's toolchain is native binaries, MkDocs is pure Python.

**Consequences.** No Vue/React components inside pages. This guide needs diagrams and
prose, so that costs nothing.

---

## ADR-017 · Fix the query before replacing the search engine {#adr-017}

**Date.** 2026-07-30.

**Context.** Search results were poor: she called `web_search` rarely, passed the user's
whole sentence as the query, and got the same three pages back on a repeat. Two candidate
causes — a weak index, or weak queries.

**Options.**

1. Keep `ddgs`, fix the query and the call parameters.
2. Swap to a search API built for agents (Brave, Tavily) or self-host SearXNG.
3. Fan out: generate 2–3 query variants per search and merge.

**Decision.** Option 1, and only option 1 for now.

**Why.** The literature puts the win where the cheap fix is.
[Ma et al. (EMNLP 2023)](https://arxiv.org/abs/2305.14283) frame it as
*rewrite-retrieve-read* — "there is inevitably a gap between the input text and the needed
knowledge in retrieval." The
[query expansion survey (2025)](https://arxiv.org/pdf/2509.07794) puts raw → LLM-rewritten
at **+14.35 NDCG@10 / +23.43 Recall@10**, and then *"rapidly diminishing returns"*. The
first rewrite is nearly the whole benefit.

Option 3 is what those diminishing returns rule out: 1→2 queries is about **+6.7%** average
exact match and saturates by three, which does not justify tripling every search when she
already gets three tool rounds to try different angles herself.

Option 2 was measured by others, not by us —
[a 2026 benchmark of 8 search APIs](https://aimultiple.com/agentic-search) has Brave
highest (agent score 14.89, ~1 point clear of Tavily) and fastest (669 ms), with Tavily
"the cleanest default" for LLM-consumed retrieval and SearXNG free but only as stable as
your hosting. **Reviewed and deferred**, for two reasons: none of them fixes a sentence
passed in as a query, so the rewriting was needed either way; and Brave retired its
perpetual free tier in February 2026, so this is the first paid dependency in a project
that has none.

**Consequences.** The rewriter is the tool description, not a model — no training loop, no
second model in 8 GB of VRAM, no extra call per search. `web_search` stays one function, so
option 2 remains a cheap swap the day the index is the thing that's wrong.

**Caveat on the numbers.** They are reported by the cited sources on their own benchmarks,
not reproduced here. What *was* measured locally: region choice on one Thai query, and the
queries she picks across five asks (`tests/searchbench.py --live`).

## ADR-018 · Her eyes emit text, not answers {#adr-018}

**Status.** Accepted, testing state · July 2026

**Context.** She could not see images at all. Discord hands her screenshots, photos,
signs and memes, and she had nothing to say about any of them. One fact removes most of
the design space: her own model is text-only —
`deepseek/deepseek-v4-flash` reports `input_modalities: ['text']` — so vision is a
separate call whatever else is decided. The choice is what that call **outputs**.

**Options.**

1. Caption to text: an image becomes a description, the description joins the chat.
2. A native VLM answers the turn.
3. OCR for text-heavy images, plus a captioner for the picture.
4. Vision as a tool she calls with a question.

**Decision.** Option 1, with the caption conditioned on what they said.

**Why.** The goal is *react and comment*, not extract.

Option 2 loses her voice, which on a reaction turn is the entire product. A correct
observation in Qwen's register is still a failure.

Option 4 needs a trigger, and there isn't one — nobody asks a question when they drop a
meme. Her live log also shows she under-calls tools already (4 tool rows in 10 turns).

Option 3 is ruled out by memes specifically.
[Hateful Memes](https://proceedings.nips.cc/paper_files/paper/2020/file/1b84c4cee2b8b3d823b30e2d604b1878-Paper.pdf)
(Kiela et al., NeurIPS 2020) is constructed from *benign confounders* — examples built so
text-alone or image-alone gives the wrong answer, scoring models at **64.73% against
84.7% for humans**. Splitting the words from the picture rebuilds exactly the unimodal
signal the benchmark defeats.

Option 1's known flaw is named in [PICa](https://arxiv.org/pdf/2109.05014)'s own paper
(Yang et al., AAAI 2022): information lost converting image to caption, because the
caption is written before anyone asks. Here that barely bites — usually **there is no
question**, the picture is the whole message — and what remains is fixed by sending their
message along with the image. Meanwhile
[*Caption This, Reason That*](https://arxiv.org/pdf/2505.21538) found VLMs reason **better**
over their own generated captions than over raw pixels, so the text step is not purely a
downgrade. The architecture is [Socratic Models](https://arxiv.org/abs/2204.00598)
(Zeng et al., 2022) — frozen models composed through language, no finetuning.

**Consequences.** Nothing downstream learns that an image existed: it is text by the time
it reaches the brief, her rules, the log and memory extraction. The log row is
`look('meme.png') -> ...`, which the panel's existing `tool_cell()` splits with no
dashboard change. A failed look returns `""`, never an exception, and `_doing()` turns
that into her saying she cannot see — the same anti-confabulation shape as the music
guard, code-enforced.

API-only, even in `local` mode: `qwen3-vl:4b` is 3.3 GB against her ~5 GB 8B on an 8 GB
card, so ollama would evict and reload on every image. The cost of local vision here is a
load stall, not inference.

**Caveat on the numbers.** Benchmark figures are the cited sources' own, not reproduced
here. Measured locally: four real images through the live model
(`tests/eyebench.py --live`), and the ~900-character multilingual OCR that produced the
`MAX_CHARS` cap.

**Deferred.** [Typhoon OCR](https://github.com/scb-10x/typhoon-ocr) for Thai text images —
add when Thai screenshots measurably fail, not before. Local vision when the VRAM exists.
Multiple images per turn.
