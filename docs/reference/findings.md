# Audit findings

Problems found while writing this guide. They were **found, not fixed** — documenting the
project was the job, and changing code during an audit hides what the audit found. Fixes
land afterwards and get marked here when they do.

Ordered by what would bite a new developer first.

---

## F1 · The mid-song URL refresh could never run — **FIXED 2026-07-30** {#f1}

**Was:** `tiwa/music.py`'s `source()` accepted a `query` argument and then called
`cls(url)`, dropping it. `Stream.__init__` passes `query` to `_decode()`, where it exists so
a dropped stream can **re-resolve an expired CDN URL** and resume — so that whole recovery
branch was unreachable through `source_for()`. The other two defences (libavformat
reconnect options, and seeking to `self.seconds` on retry) did work; this was the one that
handles an *expired* URL rather than a transient error.

**Now:** `return cls(url, query)`.

**Verified**, not just read — stubbing `music.find` and pointing a stream at an unreachable
source shows attempt 1 failing on the original URL and attempt 2 onward using the
re-resolved one, with the original query passed through each time.

---

## F2 · Two dependencies were used but not declared — **FIXED 2026-07-30** {#f2}

**Was:** `numpy` (imported by `tiwa/music.py` and `tiwa/voice.py`) and `davey` (imported by
`voice.enable_dave_decrypt`) were both missing from `requirements.txt`. They arrived
transitively — numpy via onnx-asr, davey via discord.py — so a fresh clone worked by luck.
A resolver change or a slimmer install would have killed music and voice on import.

**Now:** both declared, with a comment saying why. Unpinned, matching the rest of the file.

Kept here as a record; see the [dependency table](../toolchain/dependencies.md).

---

## F3 · `api` mode was not actually local-free — **FIXED 2026-07-30** {#f3}

**Was:** every model call in the project went through `llm.chat()` — except
`gcal.apply_change()`, which built an `ollama.Client()` directly. So a confirmed calendar
write needed Ollama running **even in `api` mode**, the one mode whose entire purpose is
needing nothing local, and the call never appeared in `llm_log` — invisible on exactly the
page you'd use to debug a bad calendar parse.

**Now:** routed through `llm.chat(..., fmt=_EVENT_FORMAT)`, picking `llm.EXTRACT_MODEL` on
the API path just like `memory.extract` does. `api` mode needs nothing local, and calendar
parses show up on the llm tab with everything else.

One thing that had to change with it: `_EVENT_FORMAT` now marks **every** property required
plus `additionalProperties: false`. OpenRouter sends the schema as a *strict* json_schema
and rejects one whose properties aren't all required — `end` is still nullable, so the model
can answer `null`. The prompt keeps the word "JSON" in it, which DeepSeek needs and
`llm.chat()` asserts.

**Verified** by `py -X utf8 -m tiwa.gcal`, which now asserts the routing and the strict
schema statically — no network, no calendar auth needed.

---

## F4 · `PLAN.md` and `README.md` contradicted the code — **FIXED 2026-07-30** {#f4}

**Fixed by annotation, not rewriting.** `PLAN.md` is the historical record — the plan as
written plus every measurement taken along the way — so overwriting it would destroy the
thing that makes it valuable. Instead it now carries a dated header pointing here for
current state, and each stale claim is marked in place:

| Claim | What was done |
|---|---|
| The E2EE opt-out paragraph | Struck through, with a `SUPERSEDED` block above it explaining that DAVE is mandatory since 2026-03-02 and E2EE now stays **on**. Flagged as a privacy claim not to quote |
| "persona stays local, always" | Marked `SUPERSEDED` inline, pointing at [ADR-002](decisions.md#adr-002) |
| `tool_log` | Rewritten to `log(ts, kind, text, ms)` — the shape that shipped — with a comment saying it was never called `tool_log` |
| ffmpeg / `FFmpegPCMAudio` / faster-whisper in track D | A note above the stage list saying how to read them, since the same file records the replacements further down |

`README.md`'s privacy note was corrected the same way: the wrong claim is quoted as
history, with the correction above it.

**The original finding, for the record:**

| Claim | Where | Reality |
|---|---|---|
| `voice.decline_e2ee()` sets `max_dave_protocol_version = 0`; calls she joins are no longer encrypted | `PLAN.md`, `README.md` privacy note | That function does not exist. `enable_dave_decrypt()` decrypts in-process and **E2EE stays on**. Opting out is now rejected with close code 4017 |
| `TIWA_ALLOW_E2EE=1` keeps E2EE | `PLAN.md` | No such variable in the code |
| Tool calls are logged to a `tool_log` table | `PLAN.md` tracks A1, E3 | The table is called `log`; there is no `tool_log` |
| "Persona stays local, always — her voice never does" | `PLAN.md` "Constraints" | Superseded. `mixed` and `api` both use an API persona. [ADR-002](decisions.md#adr-002) |
| Track D specifies ffmpeg, `FFmpegPCMAudio`, and faster-whisper | `PLAN.md` D0–D5 | All three were replaced. The same file records the replacement later — the task list was never updated |

The privacy one mattered most: it told a reader that joining a call weakens encryption for
everyone in it, and that stopped being true.

---

## F5 · Off-by-one in the decoder retry log — **FIXED 2026-07-30** {#f5}

**Was:** `for attempt in range(4)` with `if attempt >= 3: break` gives 4 attempts while the
log said `try {attempt + 1}/3` — so a real run printed `resuming, try 4/3` and then gave up
anyway. Behaviour was fine; the message lied.

**Now:** one `tries = 4` constant drives both the loop and the message, the number shown is
the attempt about to happen, and the final failure says `giving up` instead of promising a
retry that isn't coming.

---

## F6 · `graph_view.py` was undocumented — **CLOSED 2026-07-30** {#f6}

37 lines that dump the memory graph to `data/graph.html` using `vis-network` **from a CDN**,
so the page renders blank offline. The author did not recognise the file when asked, which
is what made it a finding — not the code, the fact that nobody knew what it was for.

**Closed by documenting, not deleting.** It does something the panel doesn't: a
force-directed picture of who is connected to whom. So the README now marks it
"needs internet", and the module docstring says the same plus when to prefer the panel.

Still a fair candidate for deletion if it stays unused — but a documented 37-line script
costs nothing, and deleting a tool the author had forgotten is not a call worth making for
them.

---

## F7 · `dashboard.py` is the largest file in the project {#f7}

**Severity: observation. Nothing to fix — deliberately left alone.** 672 lines, bigger than
`voice.py` (570) and more than double `pipeline.py` (234).

It's a single-file HTML-in-Python server with no template engine. That's the right trade
for one localhost user: a template engine would be a dependency, and splitting it into
modules would spread one debugging tool across five files.

It's recorded because of what it does to a newcomer's instincts. **The biggest file in the
repo is a debugging tool, not a brain** — it is the *least* important file to understand and
the easiest to mistake for the most important. If you're reading this project by file size,
you'll start in exactly the wrong place. Start at `pipeline.py`.

The number is worth re-checking, though: if it doubles again, that's a signal the panel has
grown features that belong in the bot.

---

## Not findings

Things that look wrong and aren't:

- **`tools.py` module-level mutable globals** (`PENDING_MUSIC`, `DJ`, …). Deliberate —
  a tool must not be able to act, so it sets a flag. Marked `ponytail:`.
- **`lookup()` scanning entity names in Python** instead of SQL. Deliberate ceiling with a
  documented upgrade path (FTS5).
- **Duplicated rules between `prompts/tiwa.md` and the per-turn rules.** Deliberate — the
  model forgets rules buried in a long prompt.
- **No pytest.** Deliberate; benches print tables because the thing under test is a model.
- **Real Google OAuth client secret in the repo root.** Correctly gitignored. This guide
  uses a placeholder filename and never names the real one.
