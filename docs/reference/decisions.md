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

## ADR-012 · Delete `now_playing`, inject action state instead {#adr-012}

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

## ADR-019 · Reasoning stays off, on every pass {#adr-019}

**Status.** Accepted · July 2026

**Context.** Both her models can reason. `huihui_ai/qwen3-abliterated:8b` is a hybrid
thinking model and ollama exposes `think`; `deepseek/deepseek-v4-flash` advertises
`reasoning`, `reasoning_effort` and `include_reasoning` alongside `structured_outputs`.
Neither was ever switched on, and `think` was a **dead parameter on the API path** — it
existed in `chat()`, was wired to ollama, and was silently discarded by
`_openrouter_chat`, which hardcoded `reasoning: {enabled: False}`.

**Decision.** Wire `think` through to both providers so the knob is real, and leave it
**off** everywhere. Ship `TIWA_EXTRACT_THINK`, defaulted to `0`.

**Why.** Two measurements, not an opinion.

*The tool pass* (12 probes, tools stubbed, local 8B):

| | right tool | median | total |
|---|---|---|---|
| think off | 12/12 | 2,824 ms | 32.4 s |
| think on | 12/12 | **19,217 ms** | 208.4 s |

6.8× slower for no gain, on a pass a human waits through — it runs inside
`channel.typing()`, before her reply pass even starts. Worse, reasoning made it *break a
rule*: all 4/4 recall probes gained a spurious `web_search`, which the tool description
explicitly forbids ("Never for people you should just recall"). Reasoning did not help it
choose; it gave the model room to argue with the tool description.

*The write pass* (`extractbench.py --think`) — the one place latency is genuinely free,
since `memory.extract` is fired off after the reply is already on screen:

| | clean | total |
|---|---|---|
| ollama off | 5/5 | 12.5 s |
| ollama on | **4/5** | 190.4 s |
| openrouter off | 5/5 | 8.6 s |
| openrouter on | 5/5 | 19.6 s |

No gain on either provider. On the local 8B, ~15× slower **and** structured output broke:
it stored `Steven | plays | guitar},{` — raw JSON leaking into an entity name. It also
reversed a non-symmetric relation, writing `Krich | girlfriend of | Mint`, which is the
exact direction error the extraction prompt exists to prevent.

**Consequences.** The knob works now and is honest on both providers, so this is cheap to
revisit against a better model. Off stays the default everywhere.

The one structural note worth keeping: her inner pass **is** her reasoning step — a
separate call whose whole job is to think before she speaks. Enabling model reasoning
means reasoning about reasoning. And the tool miss it would have fixed was already fixed
in code by `_force_music`, which is deterministic, free, and runs in 0 ms.
[Prompts reduce, code decides](../concepts/three-passes.md) — the same rule, applied to a
knob.

**Caveat on the numbers.** Both benches were at ceiling on accuracy (12/12 and 5/5
baseline), so they can show reasoning *hurting* but could not have shown it helping. The
intermittent `play_music` miss (~1 ask in 4–6) did not reproduce at all in the 4 music
probes, and properly measuring that needs ~30 probes a side. Latency alone settled it.

**Bench gap found and closed.** `extractbench` scored `guitar},{` as **ok** — `score()`
matches by substring. The malformed-name check that catches it was added because a human
read the output, not because the bench failed. Worth remembering the next time a green
bench is treated as proof.

## ADR-020 · Inject what she knows; stop waiting for `recall` {#adr-020}

**Status.** Accepted · July 2026

**Context.** An audit of the real database showed five apparent problems: the tool pass
almost never called tools (5 calls in 11 turns, **zero** `recall`, **zero** `web_search`),
the forced music retry was firing as often as the model, the graph held 4 entities and 2
relations, one of those entities was `Marvel Rival` where every turn said `Marvel Rivals`,
and `episodes` was empty so `idle()` could never fire.

**Decision.** Three code changes, and two of the five "problems" written off after
measurement.

**Why — what the measurements actually said.**

*The tool pass is fine.* Hypothesis: eight lines of conversation history degrade tool
selection, the way [Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/)
(Liu et al., TACL 2024) degrades retrieval. **Refuted** — 8/8 right tool with history and
8/8 without, same probes. Then reading the 11 turns settled it: every one was a music
request. No turn needed `recall` or `web_search`. The low count was correct behaviour on
an unrepresentative sample, and the forced-retry rows are the guard working, not the model
failing.

That leaves the real defect underneath: **`turn_context()` read episodes only**, and pass
3 is told an episode is "almost always null", so it returned `""` on every turn ever
recorded. A stored fact could only be reached by the model *choosing* to call `recall`.
Memory was write-only. It now injects the speaker's facts directly — the same trade as
[ADR-012](#adr-012), and for the same reason: what she should already know is not worth a
round-trip. `recall` keeps its job for third parties.

The fix also dissolves the first "problem": with a populated graph, `recall` fires
unprompted on the very next live turn. **#1 was a symptom of #3.**

*Entity canonicalization.* `canonical()` folds near-duplicates with stdlib `difflib` at a
0.85 ratio. [CESI](https://arxiv.org/abs/1902.00172) (WWW 2018) does this properly by
clustering learned embeddings with side information; at this scale a string ratio is the
whole win. Fewer tools is a real lever too — an adaptive policy showing ~7 tools instead
of 50 lifted selection from 87.1% to 93.1% on
[BFCL](https://proceedings.mlr.press/v267/patil25a.html) — but with selection measured at
8/8 there is nothing here to buy.

*The idle heartbeat could never fire.* `idle()` returns `""` when its fuel is empty, and
its fuel was episodes, which are always null. `recent_episodes()` became `idle_fuel()` and
falls back to the newest facts. Timing is the hard part of a proactive agent — fixed rules
produce untimely, annoying messages ([Liao et al., SIGIR 2023](https://dl.acm.org/doi/10.1145/3539618.3594250))
— so the brakes stay where they were: ≥ 3 h apart, 09:00–23:00, and she is told to output
NOTHING on most ticks.

**Consequences.** She opens every turn knowing who she is talking to, at the cost of up to
12 lines of prompt. Proven live: asked in Thai which games her father plays, she answered
from seeded facts she was never told in the conversation — including a stored `note` — and
asked a follow-up.

**Caveat.** The 11-turn sample is small and was all one activity. "The tool pass is fine"
means fine on 8 probes and on 11 music turns, not fine in general.

## ADR-021 · Anchor the extractor, then give the bench its teeth back {#adr-021}

**Status.** Accepted · July 2026

**Context.** The graph stayed almost empty through 11 real turns. `ไอภพ` was named in four
of them and never stored. Separately, every bench sat at ceiling — 8/8, 12/12, 5/5 — so
nothing could show a fix working.

**Decision.** Fix extraction at the input, move two guards from prompt to code, and add
probes that fail.

**Why.** Tracing one real turn found the whole bug in one line: the model **romanized**
`ไอภพ` to `Iop`, `_grounded()` could not find `Iop` in the Thai text, and a true fact was
dropped. The guard was right; its input was wrong.

That is the failure mode
[AEVS](https://www.mdpi.com/2073-431X/15/3/178) describes — hallucination comes from an
unconstrained generation space, and the fix is to tie every element to a span of the source
*before* extraction rather than filtering after. The cheap version of that is one rule:
copy every name character-for-character in its own script, never romanize. `ไอภพ | plays |
Blade` stored on the next run.

Two more guards moved to code after prompting demonstrably failed:

- **Role direction.** `Krich | girlfriend of | Mint` says Krich is the girlfriend. The rule
  *and the exact wrong example* were already in `_EXTRACT_SYSTEM` and the model reversed it
  anyway. `_role_swap()` fires only when the relation ends in " of", the subject is the
  speaker, and the text literally introduces the object as the speaker's something.
- **Self-relations.** `Nara | owes | Nara`, invented from her own reply. Forbidden in the
  prompt, emitted anyway, rejected in code now.

**The benches had no discriminative power.** When a benchmark is easier than the system,
pass rates saturate near 1 and models of very different capability produce indistinguishable
numbers ([arXiv 2602.16763](https://arxiv.org/html/2602.16763v1)); the answer is harder
items, the MMLU → MMLU-Pro move. `extractbench` gained three probes that all failed when
written — the Thai name, a forbidden reversal, and her enthusiasm treated as fact — plus
checks for forbidden directions and self-relations. It went 5/5 → **6/8** on the first run
and back to 8/8 only after the fixes landed.

**Consequences.** Two of the checks exist because a human read the output, not because a
bench failed: `guitar},{` and `Nara owes Nara` both scored **ok**. A green bench is evidence,
not proof.

**Measured and rejected.**

- *A bigger vision model for Thai.* `qwen3-vl-32b-instruct` is cheaper per token than the
  8B ($0.104/M vs $0.117/M), so it looked free. Across two runs on the same sign it was
  better once and worse once, while costing **4.2× the latency** (7,385 ms vs 1,744 ms) on
  a call that blocks her reply. Run-to-run variance on one image is not evidence. Keeping
  the 8B; [Typhoon OCR](https://github.com/scb-10x/typhoon-ocr) stays the real upgrade.
- *Folding `Gojo` into `Gojo Satoru`.* Tempting at 0.62, but the same containment rule
  merges `Blade` with `Blade Runner`, and a wrong merge is unrecoverable. `lookup()` already
  matches substrings both ways, so the pair already **reads** as one. Two nodes, one answer.
- *difflib for relation names.* Disqualified by measurement: `likes`/`dislikes` scores
  **0.769** while `plays`/`playing` scores **0.667**, so every cutoff that folds the pair we
  want also merges a relation with its opposite. A 4-character stem of the first word
  separates them cleanly.

## ADR-022 · Episodes fire on surprise, not on the model's judgment {#adr-022}

**Status.** Accepted, August 2026.

**Context.** The live database held **2 episodes across 97 logged turns** — and both
were written on the same calendar turn, one a restatement of the other. Pass 3 is told
*"episode: almost always null"*, backed by four BAD examples and one GOOD, and it obeyed
almost absolutely. So `turn_context()`'s episode lines were effectively dead, and she
knew facts about people while having essentially no memory of anything happening —
semantic memory without episodic memory, in
[Tulving's](https://doi.org/10.1037/h0080017) split.

The two that *did* land are the tell. The prompt forbids an episode for "someone asking a
question" and for "anything already captured as a memory above"; those two rows are a
question, and each other. The gate was not merely too tight — it was **miscalibrated**,
silent for 95 turns and then wrong twice on one.

**Decision.** Stop asking a model *"would this matter in a month?"* — a judgment call
it always declines — and decide in code from whether the turn **moved the graph**: a
subject she had never met, or a belief that flipped. Both signals are already computed
inside `store_extraction()`, so it costs no extra call.

This is what event segmentation theory says the brain does: cut memories at
**prediction errors**, the moments the pattern broke
([Neurosci & Biobehav Rev](https://www.sciencedirect.com/science/article/abs/pii/S0149763424000010)).
[EM-LLM](https://arxiv.org/abs/2407.09450) (ICLR 2025) segments a token stream by
Bayesian surprise and beats full-context models on LongBench, which is the same idea one
level down. A model-written episode still wins when one appears; it just no longer has
to.

**Consequences.** Measured **4 of 12** turns on a replay, and 3 of 4 on a live
conversation. The rate falls on its own as the graph fills — new people get rarer, only
flips remain. Three narrowing rules each came from reading real output rather than from
a failing check: objects don't count (or every game ever named is a life event), the
speaker doesn't count (his own facts are already in her context), and freshness is judged
against a snapshot taken **before** the batch — live, `Steven plays guitar` read as old
news because `Krich cousin of Steven` had invented him one line earlier in the same turn.

**Rejected.** Lowering the prompt bar instead. The prompt bar *is* the mechanism that
failed, and the project rule is the same one that settled the guards, `now_playing` and
role direction: **prompts reduce, code decides.**

## ADR-023 · A new belief replaces the old one {#adr-023}

**Status.** Accepted, August 2026.

**Context.** The primary key is `(src, rel, dst)`, so `Tycoon likes X` and `Tycoon hates
X` were two valid rows. Both survived, both were injected every turn, and she read a flat
contradiction and picked one at random. `canonical_rel()` deliberately keeps opposites
apart as *names* — that is correct and measured — but nothing ever retired the loser.

**Decision.** `_supersede()`: relations that are two answers to the **same question**
compete for one (subject, object) pair, and the newer one wins. One hand-listed axis
(`feel`: like/love/enjoy/prefer vs hate/dislike), with polarity, so a genuine flip is
reported and a refinement (`likes` → `loves`) is not.

**Consequences.** Verified live — "Steven loves durian" then "Steven hates durian now"
leaves one row and one episode. `plays` and `likes` between the same pair are untouched:
different questions. This is also the half of the forgetting literature that applies at
her scale; **decay engines are not**, at nine facts. See
[open questions](../open-questions.md).

## ADR-024 · Reflection rides the heartbeat that was already running {#adr-024}

**Status.** Accepted, August 2026.

**Context.** `idle_turn()` wakes every 30 minutes from 09:00–23:00 — about **28 model
calls a day** — and is rate-limited to *speaking* once every 3 hours. The overwhelming
majority of those calls ran, decided "nothing to say", and were discarded. That is
thinking time already bought and thrown in the bin.

**Decision.** `pipeline._settle()` gives the tick a second job. Once `REFLECT_EVERY = 3`
episodes have piled up unprocessed, it reads them and writes back one conclusion.

- **Reflections are episodes filed under her own name**, so they land back in the stream
  they were drawn from — `idle_fuel()` retrieves them like anything else she lived, and
  the newest one dates the watermark for free. No new table.
  [Generative Agents](https://arxiv.org/abs/2304.03442) stores reflections back into the
  same observation stream for the same reason.
- **It gets the expensive model.** Nobody is waiting on it, which makes it the one pass
  where latency does not matter — the argument
  [Letta](https://www.letta.com/blog/sleep-time-compute/) makes for sleep-time agents.
- **It fires only on a backlog**, so a quiet day costs zero calls.

**Consequences.** Live output on a four-turn conversation: *"Krich is always the one
telling me about other people, but I still don't know what he himself thinks about
anything."* No single turn contained that.

A reflection that concludes nothing still writes a **blank row** as the watermark, or the
same three episodes are re-reflected every half hour forever; `idle_fuel()` and the panel
filter `text != ''` so it never reaches her as something she lived. A reflection that
**fails** deliberately does not watermark — the events stay unreflected and the next tick
retries, rather than a provider outage silently eating her week.

**Depends on [ADR-022](#adr-022).** With zero episodes this pass reflects on an empty
room; that is why episodes shipped first.

## ADR-025 · The calendar needed a prompt, not code {#adr-025}

**Status.** Accepted, August 2026.

**Context.** Reported as "she notes it in the database but calls no tool". The live log
shows exactly that:

```text
Tycoon: พรุ่งนี้เช้ามีนัดกินข้าว 13.00 ช่วยลงปติทินให้หน่อย
ทิวา:   ...มึงหมายถึงพรุ่งนี้ (อาทิตย์ 2 ส.ค.) 13.00 ป่าว     ← a fair question
Tycoon: ช่ายๆๆ                                              ← yes
ทิวา:   โอเค ลงให้ละ อาทิตย์ 2 ส.ค. 13.00                    ← claims the write
```

No `calendar_write` row. Same shape as the music confabulation
([ADR-012](#adr-012)): she narrates an action she never performed.

**Investigated, and the obvious suspect was wrong.** Both calendar tool descriptions say
*"Krich's calendar"* / *"his schedule"* while the asker was Tycoon, which looked like the
cause. `tests/calbench.py --live` says no — Tycoon and Krich behave identically on every
case, twice. Don't fix what the data cleared.

**Measured cause.** `_INNER_SYSTEM` spent four lines on music and one word on the
calendar: *"check the calendar"*. Nothing in the prompt described writing at all, so a
message leading with a calendar word mapped to `calendar_read`. Reproducible: 0/2 on the
bare confirmation, 0/2 on `ลงปฏิทินให้หน่อย นัดหมอฟัน…`, ~50% overall.

**Decision.** Add the missing rule to `_INNER_SYSTEM` rather than build a
`_force_calendar()` mirroring `_force_music()`. **20/21** across three runs afterwards,
from ~50%.

This is the ladder working in the direction it usually doesn't. The project rule is
*prompts reduce, code decides*, and four ADRs record a prompt failing and code fixing it —
but that rule is about **guards**, where the cost of a leak is unbounded. Here the prompt
had simply never been written. Reaching for a forced retry first would have added a model
call per calendar turn to compensate for a sentence nobody wrote.

**Consequences.** ~95%, not 100%, and the residual is always the bare confirmation. If it
recurs in real use the answer is the one music already took — a forced retry — and the ✅
gate makes that safe to over-fire, since a wrong queue costs one ❌. `calbench.py` asserts
`>= 6/7` so the regression is caught rather than rediscovered from a log.

**Also found.** `gcal.apply_change()`'s parse is solid and needed nothing: Thai titles,
relative dates, and even a Buddhist-calendar year (`2569` → `2026`) all resolve correctly.
The stage everyone assumes is fragile was fine; the stage nobody documented was broken.
