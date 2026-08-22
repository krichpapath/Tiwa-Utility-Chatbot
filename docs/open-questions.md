# Open questions

The audit's unknowns, and what the author answered. Kept as a record so nobody re-derives
a rationale that was actually decided.

**Nothing in this guide states a "why" that isn't either answered below or measured in
`PLAN.md`.**

## Answered

**Is her voice allowed on a hosted API?**
: **Yes.** The system is still developing; this guide documents only what exists today.
`mixed` and `api` both run her voice on the API. `PLAN.md`'s "persona stays local, always"
constraint is superseded — [ADR-002](reference/decisions.md#adr-002).

**Is Discord voice end-to-end encrypted while she listens?**
: **Yes, currently.** `enable_dave_decrypt()` runs at import and decrypts DAVE in-process,
so encryption stays on for everyone in the call. Opting out is no longer possible
(close code 4017). `README.md` and `PLAN.md` described the old opt-out approach; both are
now corrected in place — [F4](reference/findings.md#f4).

**Does `api` mode really need no Ollama?**
: **That's the intent.** `api` is the low-spec mode: heavy work on the API, GPU free. The
tradeoffs accepted are cost and less freedom (hosted filters). Music, Discord and tool
calling are all expected to work identically. Calendar writes used to violate this by
calling Ollama directly; fixed 2026-07-30 — [F3](reference/findings.md#f3).

**Why an abliterated model locally?**
: **Freedom of speech.** She's meant to swear, insult back, and refuse in her own voice
rather than emit a policy notice — [ADR-013](reference/decisions.md#adr-013-the-local-model-is-an-abliterated-build).

**Is speech-to-text parked or paused?**
: **Paused, and taught here.** It's "a long way from finished, not even close" — so
[voice in](surfaces/voice-in.md) documents it as real, switchable, and currently not good
enough at Thai.

**Are the magic numbers measured?**
: **No** — `EPISODES_KEEP`, `LLM_LOG_KEEP`, history `maxlen`, the `hist[-9:-1]` window and
`MIN_UTTERANCE_S` are all chosen, not tuned. Labelled as such in
[conventions](work/conventions.md#numbers-in-the-code-are-mostly-chosen-not-measured), and
every variable is explained for a beginner in [env vars](reference/env.md).

**What's the deployment target?**
: **24/7 eventually** — she should be able to act whenever she wants. Starting and stopping
her by hand is expected for now, so [setup](setup.md) covers both.

**Which benches are canonical?**
: Author deferred to a recommendation. Chosen six, with the reasoning, in
[the bench suite](work/testing.md#start-here). The three offline ones
(`test_memory`, `djbench`, `panelbench`) are the pre-commit set.

**What is `graph_view.py`?**
: **Author didn't recognise it.** 42 lines, dumps the memory graph to `data/graph.html`
using a CDN-hosted library, so it needs internet. Mostly superseded by the panel's memory
tab, but it draws a force-directed picture the panel doesn't. Kept and documented rather
than deleted — [F6](reference/findings.md#f6).

## Still open

~~One real bug~~ **— fixed 2026-08-23.** It was found while building
[reflection](reference/decisions.md#adr-024) and deliberately not fixed there
because it was older and unrelated:

- **A provider outage kills the heartbeat, permanently.** `pipeline.idle()`'s speak pass
  is unguarded, so when Ollama is down `_tool_chat` raises straight out of the tick.
  discord.py's `tasks.loop` logs the exception and **stops the loop** unless a
  `@loop.error` handler is registered, and none is — so she goes silent until the process
  restarts, with nothing on screen to say why. `_settle()` catches its own errors
  (`tests/test_memory.py` asserts it); the older pass around it does not.

    **Closed.** `idle_turn()` guards its own body and logs an `error` row; the next
    tick retries in 30 minutes. Nobody is waiting on an unprompted message, so it
    stays quiet in the channel rather than announcing itself.

    The same silence existed on the two paths somebody *is* waiting on, and that
    turned up the hard way: an expired key made every Discord turn raise 401 with a
    traceback in the console and **nothing at all in the channel**. `bot._apologise()`
    now posts a plain line naming the cause — not in her voice, because the model is
    what failed and faking her line would be bluffing in a different hat.
    `tests/outagebench.py` drives all three call sites.

Then two soft ones, for whenever they matter:

- **Verbatim parroting.** The API model has been caught copying an example from
  `tiwa.md` word for word. Watch for it in real chat; the fix is rewriting the example,
  not adding a rule.
- **Relation direction on the API.** One extraction produced a mangled subject/object with
  a phantom entity. Clean on local, and clean on both since the prompt gained explicit
  direction rules. ~~If it recurs, the answer is a closed relation vocabulary.~~
  **Not that answer.** A closed vocabulary was drafted for the pollution problem and
  withdrawn — tested against the real graph it leaks both ways, since `wants` is right for
  *"Krich wants a PS5"* and wrong for *"wants to listen to this song"*. If direction
  recurs, the next thing to try is more direction examples, not a smaller vocabulary.
  See [ADR-027](reference/decisions.md#adr-027).

## Adding to this page

Found something the code can't explain? Add it here rather than guessing, and write
*"Unknown — original author to confirm"* in the page that needed it. See
[docs maintenance](reference/docs-maintenance.md).
