# SWARM — the Mini Tiwa plan

Status: **not started.** This is the picture to argue with before any code moves.

Companion to `PLAN.md`. Same rules: every gate ships alone, every gate has one
runnable check, and a gate that can't be judged by running something isn't done.

---

## The one-sentence version

Main Tiwa decides **what** to do. Mini Tiwas figure out **how**.

She says "put on their favourite song." DJ Tiwa works out who "they" are, what
they like, finds it, and plays it. Main Tiwa never learns any of that.

---

## Why (the honest ranking)

Four reasons were on the table. They are not equally good, and the plan is
built on the strong ones.

| reason | holds up? |
|---|---|
| **Growth** — add skills without breaking the ones that work | **Yes. This is the whole reason.** |
| **Fewer details in her head** — facts instead of "don't say X" | **Yes**, and stronger than it first looked |
| **Latency** — she talks while work happens | **Partly.** Real, but the cause isn't what it looked like |
| **Prompt dilution** — long prompt hurting attention | **No.** Her prompts are ~2.6k tokens; the effect starts an order of magnitude higher |

### Growth is the real one

`docs/concepts/tools.md` already says it:

> More tools = worse tool selection. `now_playing` was deleted for this reason.
> Every tool you add competes with `play_music` for attention.

You deleted a working tool to protect the others. That's the ceiling, and it's
self-diagnosed. Home Assistant is next on the roadmap — lights, locks, garage,
heat — which is 5-6 more tools onto a list of 10.

Under this design, **Main Tiwa's list stays flat forever.** She sees ~4 minis.
DJ Tiwa can have nine tools inside it and Main never knows.

`now_playing` can even come back. It only had to die because it competed.

### "Facts instead of prohibitions"

Look at what's in her **voice** prompt today (`pipeline._doing()`):

> "Never say you do not know the song... Do not sing or quote its lyrics.
> You have not seen the search result yet, so do NOT name an artist, album
> or year for it."

Three prohibitions, all because she doesn't know what got played. If DJ Tiwa
hands back `{"playing": "ATLAS", "artist": "The Score", "queued": 2}`, all three
lines delete themselves. She has the artist. Nothing to forbid.

**Prohibitions are expensive. Facts are cheap.** Every capability you add today
brings its own pile of "don't say X." That's what actually bloats her.

### Latency — measured, not guessed

From her own log, 111 real turns:

| | p50 | p95 |
|---|---|---|
| whole turn | **6,431 ms** | 13,622 ms |
| inner pass (deciding about tools) | 2,597 ms | 4,173 ms |
| persona pass (her voice) | 1,958 ms | 3,243 ms |
| **the tools actually running** | **3 ms** | 4,337 ms |

The tools are free. The six seconds is her thinking about tools *before* she's
allowed to start talking. Splitting capabilities doesn't fix that — **letting
her talk and dispatch at the same time does** (gate S3).

---

## What it looks like

### Today

```mermaid
flowchart LR
    U[user] --> I["inner pass<br/>2.6s<br/>sees all 10 tools"]
    I --> T["tools run<br/>3ms"]
    T --> P["persona pass<br/>2.0s<br/>+ music mechanics<br/>+ don't-say-X rules"]
    P --> R[reply]
    R --> X["extraction<br/>fire and forget"]
```

<figcaption>One line. Every step waits for the one before it. 6.4s.</figcaption>

### After

```mermaid
flowchart TB
    U[user] --> C["code, 3ms<br/>memory + what she's doing<br/>+ music keyword check"]
    C --> M["MAIN TIWA<br/>her voice, full persona<br/>~2.0s"]
    C --> D["DISPATCH<br/>low temp JSON<br/>sees 4 minis, not 10 tools"]
    M --> R[reply on screen]
    D --> DJ[DJ Tiwa]
    D --> CAL[Calendar Tiwa]
    D --> S[Search Tiwa]
    DJ --> F["actions flush<br/>after she speaks"]
    CAL --> F
    S --> L["late result<br/>follow-up message"]
```

<figcaption>Main Tiwa never waits on a mini. That's the rule everything else
depends on.</figcaption>

### One turn, start to finish

```mermaid
sequenceDiagram
    autonumber
    participant U as Krich
    participant B as bot.py
    participant M as Main Tiwa
    participant D as dispatch
    participant DJ as DJ Tiwa
    U->>B: "put on the song I like"
    B->>B: recall + action state (3ms, code)
    par she talks
        B->>M: persona + memory + state
        M-->>B: "got it, one sec"
        B-->>U: reply on screen (~2.0s)
    and work happens
        B->>D: which mini?
        D-->>B: {"mini":"dj","task":"their favourite song"}
        B->>DJ: task
        DJ->>DJ: recall(Krich) -> likes The Score
        DJ->>DJ: search -> ATLAS
        DJ-->>B: {playing, artist, queued}
    end
    B-->>U: now playing (flush, after she spoke)
```

---

## The rules that don't bend

These are guarantees, not preferences. Each one has already been broken once in
this project's history, which is why it's written down.

1. **Only Main Tiwa speaks.** Minis return structured data. Ever.
2. **Minis return facts, never instructions.** The moment a mini returns
   `"tell them it's playing"`, the prompt bloat has moved into a new file and
   the voice can leak. Facts plus a completeness flag; **code** turns the flag
   into a guard line if one is needed.
3. **Minis read, they never write beliefs.** `recall` yes. Anything that sets a
   memory, no. Offline reflection stays the only thing that changes what she
   thinks. (`docs/concepts/guards.md`)
4. **One writer.** Minis return data; `bot.py` acts on it. Already true today —
   "tools set flags, they don't act."
5. **The check-mark gate is untouched.** Calendar Tiwa can decide *what* to
   write. Krich still confirms it with a reaction.
6. **Nothing a mini does blocks her reply.** DeepSeek freed your GPU, not your
   clock. Each hop is still ~2 seconds.
7. **Minis get taste, never voice.** Taste = who "they" are and what they like
   (memory data). Voice = the Thai register, the sass, the escalation ladder.
   DJ Tiwa needs taste to find a favourite song. It never needs voice. If a mini
   seems to need voice, it's doing Main's job.

---

## The minis

### DJ Tiwa — first, and the template

53% of all tool calls today. Half of it already exists as `_TERMS_SYSTEM`.

**Its own tools:** `recall` (read), `web_search` (read), `play`, `queue`, `skip`,
`stop`, `now_playing`

**What it handles that Main Tiwa currently has to:**

- their favourite song → recall who they are, what they like, then search
- "something like this one" → read the deck, find similar
- play vs queue vs skip — the deck state decides, not her
- naming search terms (`_TERMS_SYSTEM` moves here whole)
- the missed-ask retry (`_missed_music` / `_force_music` move here)
- knowing what's on without a tool call

**Main Tiwa's music tools: 4 → 1.** Her `_doing()` music block shrinks to facts.

### Calendar Tiwa — the judgment, not the mechanics

Half of calendar is built and must **not** become an agent. The other half
doesn't exist and can only be an agent.

| work | who does it |
|---|---|
| parse a date, build the event, list events | code + `gcal._EVENT_FORMAT` (exists) |
| the write itself | check-mark gate (exists, never an agent's call) |
| "this Tuesday or next Tuesday?" | **Calendar Tiwa** |
| "does this clash with something?" | **Calendar Tiwa** |
| **"is this worth bringing up right now?"** | **Calendar Tiwa** |

That last row is why it earns the name. *Should I mention this?* is a judgment,
not a lookup — and constraint #5 says **she is not a butler**, so it has to be a
judgment she's allowed to make. Nothing in the current design can do it.

It also unblocks the grounded heartbeat on the roadmap: "lights still on at 1am,
you asleep?" is the same question.

### Search Tiwa — read-only, off the critical path

8 calls in 135, but the worst tail latency (p95 4.3s). The only mini shape that
*both* sides of the industry argument endorse: read-only workers that add
intelligence, not actions.

**Its own tools:** `web_search`, `recall` (read)

**Handles:** multi-hop ("who directed it" → "what else did they do"), dedupe
(`SEEN_URLS` already exists), and "I don't recognise this" → search instead of
hedge.

### Memory Tiwa — deferred, on purpose

`recall` is a 3ms sqlite lookup. An LLM in front of it buys two seconds and no
new ability. It also can't write, by rule — so it has nothing to reason about.

**Build it when:** a mini asks something `recall` can't answer in one lookup.
Entity normalization ("Gojo" vs "Gojo Satoru") is the trigger already on the
roadmap.

---

## Gates

Each ships alone. Each has one runnable check. Stop at any point and what's
merged still works.

```mermaid
timeline
    title Swarm gates
    Prerequisite : S0 Turn object
    Foundation : S1 mini contract : S2 DJ Tiwa
    The payoff : S3 fork the reply : S4 late results
    Growth : S5 Search Tiwa : S6 Calendar Tiwa : S7 prove the property
```

### S0 · Turn object — kill the globals ✅ done 2026-08-21

**Did:** the six per-turn globals moved onto a `Turn` dataclass held in a
`contextvars.ContextVar` (`tiwa/tools.py`). asyncio copies the context into every
Task, and `asyncio.to_thread` carries it across the thread boundary — which is
the path a tool actually takes.

`pipeline.respond()` and `pipeline.idle()` call `tools.new_turn()`. The heartbeat
needs its own call because `tasks.loop` is one long-lived Task whose context
outlives a tick.

**The old names still work.** A module-level `__getattr__`/`__setattr__` shim maps
`tools.PENDING_MUSIC` onto the current turn, so `bot.py`, `chat.py`,
`dashboard.py` and seven benches did not change. That is deliberate: it is what
makes *"no behaviour changed"* checkable by running those benches **unmodified**.
Marked `ponytail:` in the source with its upgrade path — delete the shim once the
minis land and every caller says `current()` anyway.

**What it was hiding.** With one shared Turn, two concurrent turns produce:

| | old | now |
|---|---|---|
| metal turn's `PENDING_MUSIC` | `'lofi'` | `'metal'` |
| lofi turn's DJ list | 3 jobs, 2 from the other channel | its own |
| `PENDING_CALENDAR` | both channels' writes merged | separate |

The last row is the real one: a calendar change requested in one channel
appearing in another channel's ✅ gate.

**Check:** `tests/turnbench.py` — concurrency, the thread hop, the heartbeat
reset, and that assigning `tools.PENDING_MUSIC = None` doesn't shadow the shim.
No Discord, no network, no model. Verified to fail when `current()` is forced to
return one shared `Turn`.

`djbench`, `panelbench`, `test_memory` pass unmodified.

### S1 · The mini contract ✅ done 2026-08-21

**Did:** `tiwa/minis.py` — registry plus dispatch, one module, exactly the shape
`tools.py` already has. (Not a package: `tools.py` holds the decorator *and* all
ten tools in 270 lines, and there is no reason minis differ. It becomes a package
the day one mini needs its own file.)

**Nothing calls it.** `pipeline.py` is untouched — S1 ships the contract, S2 moves
music onto it. The gate is additive and revertable on its own.

A mini is `fn(db, task: str) -> dict`, registered with a description and **the
list of fact keys it may return**:

```python
@mini("plays and queues music. give it the song, mood or 'their favourite'.",
      ("playing", "artist"))
def dj(db, task): ...
```

**Declared fields are how rule 2 is enforced**, and they are the only automatic
part of it. You cannot reliably detect `"tell them it's playing"` at runtime — but
you can make adding a field a visible edit someone reviews. A mini that starts
smuggling voice has to say so in its own signature first. `clean()` drops
undeclared keys, nested objects, and non-dict returns.

`run()` degrades to `{}` on a crash, an unknown mini, or an unusable return —
never raises. Same stance as tools: a bad mini is a no-op she asks about, not a
dead turn.

Dispatch schema, as built:

```json
{
  "dispatch": [{"mini": "dj", "task": "their favourite song"}],
  "ask": "which friday did they mean"
}
```

- `"dispatch": []` is the **common** case — 59% of turns need no mini. The prompt
  says so in those words: *"An empty list is the normal answer, not a failure."*
- Ambiguous? Empty dispatch + a populated `ask`. Not new — the inner pass already
  emits `ask them: ...` lines.
- `mini` is a **strict enum of the live registry**. The tool pass has to cope with
  invented names; this pass cannot produce one.
- The task is a **goal, not a plan** — "their favourite song", not the user's
  sentence and not steps. The mini works out the rest.
- No `blocking` field. One rule instead: **LLM dispatches never block; code
  (recall, action state) runs inline.** A knob here is a knob that gets set wrong.

**Two traps this repo has already been bitten by, both now checked offline:**
OpenRouter rejects a strict `json_schema` whose properties are not *all* required
(`gcal._EVENT_FORMAT` carries the same note), and DeepSeek returns **empty
content** unless the prompt literally contains the word "json". `llm.chat`
asserts the second, but only on a live call — and a live call needs a key.

**Check:** `py -X utf8 -m tiwa.minis` (registry contract) and
`tests/minibench.py` (schema strictness at every depth, the "json" word, temp 0,
and six routing cases including an invented mini and non-JSON junk). No key, no
network, no model.

### S2 · DJ Tiwa ✅ built 2026-08-21 · not yet wired

**This gate was written wrong and reading `djbench` is what showed it.** The plan
said move `_TERMS_SYSTEM`, `_missed_music`, `_force_music` and the deck state into
the mini. Two of those must not move, and one of them for a reason that matters
more than the test.

#### `_missed_music` stays in the turn layer — it is the net UNDER the router

It fires when the model **failed to notice** a music ask — about 1 ask in 4–6.
Put it inside DJ Tiwa and it only ever runs on turns where the router already
dispatched DJ, which is exactly the turns that never needed it. A safety net
inside the thing it is catching is not a safety net.

So it stays in `pipeline`, deterministic, on every turn, in front of dispatch.
That is the cheap layer of a hybrid router, and it is already built and already
measured — 24 cases in `djbench`, every one traced to a dated live-log failure.

`_doing()` and `_asked_deck()` stay too: they compose Main Tiwa's state from
*every* mini, not just music.

#### What actually moved

`_TERMS_SYSTEM` plus the play/queue/skip/stop choice, into `minis.dj`. One
schema-constrained call at temp 0 returns `{action, terms}`.

The deck is **read**, never asked for — `music.NOW` and `music.QUEUE` go into the
prompt as facts. That is the tool call this mini deletes: Main Tiwa used to need
four competing tools and had to remember which one the deck state called for.

The action is then applied through the existing tool functions, so they still
write to the `Turn` and `bot._flush_music` still drains it **unchanged**.

Facts out: `action`, `terms`, `playing`, `queued`. Not `play_music`'s return
value — that is a paragraph of *"do NOT name the artist, album or year"*, which
is a prohibition, belongs to `_doing()`, and is precisely what rule 2 keeps out
of a mini. `djminibench` asserts none of it leaks.

#### Not wired yet

`pipeline.respond()` is untouched and the four music tools are still registered.
Wiring means removing them from Main's list in the same change that starts
calling dispatch — otherwise music gets decided twice. That belongs with S3,
which has to make dispatch concurrent anyway; serial dispatch would add ~2s to
every turn for no gain.

**Check:** `tests/djminibench.py` — the contract, the deck reaching the decision,
each action landing on the `Turn`, the empty-terms veto, and junk leaving the
deck untouched. Offline, canned model.

`djbench` passes **unmodified** — `git diff tests/djbench.py` is empty.

### S3 · Fork the reply ✅ built 2026-08-21 · live latency unmeasured

**Did:** `tiwa/turn.py`. She starts talking at t≈3ms while dispatch runs beside
her. `TIWA_TURN=serial|concurrent` picks the path *inside* `pipeline.respond()`,
so `bot.py`, `chat.py`, `dashboard.py` and every bench are untouched — same trick
as `TIWA_MODE` in `llm.py`. Default stays `serial`.

**The tool pass is gone from this path**, because the log says it was never the
work that was slow:

| | p50 |
|---|---|
| whole turn | 6,431 ms |
| inner pass | 2,597 ms **× 1.7 rounds** |
| persona | 1,958 ms |
| **tools actually running** | **3 ms** |

1.7 × 2,597 + 1,958 ≈ 6,373. The six seconds was her deciding what to look up
before she was allowed to open her mouth. Where each tool went:

| tool | calls | now |
|---|---|---|
| `recall` | 55 | `memory.mentioned()` — sqlite, 3 ms |
| music | 71 | DJ Tiwa, dispatched, flushed after she speaks |
| `web_search` | 8 | Search Tiwa, arrives late (S4) |
| `calendar_read` | 1 | Calendar Tiwa |

**`memory.mentioned()`** is what makes the fork safe. `turn_context()` already
covered the person talking; `recall` existed for third parties, and it was the
single most common reason the tool pass ran a second round. It is a substring
scan over 25 entities. Precision-biased like `_MUSIC_ASK`, with `MENTION_MIN`
keeping short Thai names out of unrelated words.

**Sight stays on the critical path** on purpose. She is reacting to a picture
already on screen, "I can't see it" a second later is worse than waiting, and it
is the rarest turn there is.

**The confabulation guard had to survive being early.** On a music turn she
speaks *while* DJ searches, so there is nothing in the `Turn` to name.
`_doing(dispatching_music=True)` gives her the same text minus the title: *it IS
happening, and you have not seen the result, so name no artist, album or year.*
True either way, and if DJ finds nothing the follow-up (S4) corrects it.

**Both paths share the words.** `pipeline._state()` and `pipeline.say()` were
extracted so serial and concurrent build the identical inner-state block with
identical sampling. `forkbench` asserts it — if the blocks differ, the A/B is
measuring two things at once.

**Check:** `tests/forkbench.py` — wall-clock proof of overlap (2 calls in 1×
delay, not 2×), no tool pass, the env switch routing both ways, `mentioned()`,
the safety net still catching a music ask the router missed, and the guard text
holding mid-dispatch. Offline.

**Not yet measured live: p50 latency.** `OPENROUTER_API_KEY` returns 401, so the
3,500 ms target is unverified. The bench proves the calls overlap; only a live
run proves the number.

### S4 · Late results ✅ done 2026-08-21

**The split is actions vs speech**, and it falls straight out of `bot.py`: the
flush runs the moment `respond()` returns, so anything that produces an **action**
has to finish first. Anything that produces **speech** does not.

| | minis | when |
|---|---|---|
| **actions** | `dj`, `calendar` | before she speaks — the flush needs them |
| **speech** | `search`, anything new | after, as a follow-up message |

**Writing this found a real gap in S3.** `forkbench` gave every fake call the same
delay, which hid the fact that `gather` waits for the *slowest* branch — so a
music turn was still bound by dispatch + DJ ≈ 4.1s even though her words were
ready at 2.0s. Two fixes:

- **DJ starts at t=0, not after dispatch.** `_missed_music` already knows it is a
  music ask, deterministically and for free. Making the song wait ~2.6s for a
  model to agree is the exact round trip this design exists to delete.
- **Speech minis are spun off**, not awaited. `latebench` uses the *measured*
  uneven delays — dispatch 2.6s, persona 2.0s, mini 1.5s, p95 search 4.3s — which
  is what makes the difference visible at all.

**Only she speaks.** A late result does not reach the channel as facts; it goes
through `pipeline.say()` and comes out in her voice, one line. `latebench`
asserts the raw fact string never appears in what was sent.

**Never a silent drop.** A timeout at `LATE_TIMEOUT` (6s — tool p95 is 4,337ms)
says nothing rather than something invented, and logs why. Silent drops are how
she starts believing things nobody told her.

**Cancellation, one rule:** a new message from the same person cancels their
pending **speech**; actions are untouched. A stale search dies, the song they just
asked for still plays. `latebench` drives exactly that pair.

**`on_late`** is one optional async callable. `bot.py` passes the channel's send;
`chat.py`, the dashboard and the benches pass nothing and late results are logged
and dropped, because none of them can receive a second message.

**Check:** `tests/latebench.py` — six scenarios, offline. Note the harness runs
one event loop per scenario and holds it open: `asyncio.run()` cancels pending
tasks when its coroutine returns, which would kill every late task the instant
she stopped speaking. `bot.py` has one long-lived loop, so that is the real shape.

### S5 · Search Tiwa ✅ done 2026-08-21

Keywords, then one search. Read-only, and never on the path to her mouth — it is
the p95 tool (4,337 ms) and S4 is what makes that free.

The keyword rules move here **whole** from the `web_search` tool description:
strip `what is` / `มึงรู้ไหมว่า`, keep names and numbers, add the current year,
search in the language the answer lives in. Nothing about them is new. What
changed is that they are now the **only** thing in the prompt instead of one of
ten tool descriptions competing for attention.

**One hop, not multi.** Multi-hop was the argument for Search being a real agent
rather than a function — but it is 8 calls in 135 and nothing has missed yet.
Marked `ponytail:` with the trigger: add the second hop when a real question
needs one.

**A failed search returns nothing, not an error.** `web_search` never raises, so
a network flake arrives as the string `"search failed: …"`. Handing that back as
a finding is how she ends up reading an exception at someone. Empty `found` means
`_late()` says nothing at all.

**Reused rather than rewritten:** `pipeline._terms()` already strips the
`<think>` the 8B leaks, takes the first real line, unquotes and caps it — it does
this for the music retry. The first draft reimplemented it and got `<think>`
wrong; the bench caught it.

**Check:** `tests/searchminibench.py` — the rules are present, the model's answer
is parsed rather than pasted, today's date reaches the prompt (she was searching
*"ราคา RTX 5090 2025"* in July 2026), a blank answer falls back to the task rather
than searching `""`, and a flake stays quiet. Plus a source scan asserting **no
mini can reach a memory write** — the coercion guarantee, checked rather than
trusted. `searchbench` still owns the live index.

### S6 · Calendar Tiwa ✅ done 2026-08-21

The judgment half only. `gcal._EVENT_FORMAT` still parses the date — it already
handles Thai titles, relative dates and Buddhist years, all measured — and the
✅ gate is still the write. Neither moved, and neither is ever an agent's call.

What is new is what did not exist: **which Tuesday did they mean, does it clash,
is it worth bringing up.** It reads the week itself (`gcal.upcoming()`, no tool
call) so the decision has the calendar in front of it.

**Asking and acting are mutually exclusive.** A populated `ask` forces the action
to `none` and queues nothing. A guess that lands in someone's calendar is worse
than a question — and the ✅ gate makes the *opposite* error cheap, so the
asymmetry is deliberate.

**A clash is pointed out, not blocked.** It still queues; Krich decides.

**"Not a butler" is in the prompt verbatim**, because the failure mode of a
calendar agent is reading the diary at people. `calminibench` asserts the line is
still there.

**This gate exposed an ordering bug in S4.** An ACTION mini finishes *before* she
speaks, so its facts cannot reach the state block — that was built at t=3ms. They
go out as a follow-up instead, which meant a mini that finishes faster than the
persona call could send its follow-up **first**, and she would answer a question
she had not asked yet. `latebench` never saw it because its fake search was
slower than its fake persona. Fixed with a `spoken` event: nothing follows up
until her words are in hand. `calminibench` drives the fast-mini case.

**Check:** `tests/calminibench.py` — strict schema, the week read not asked, a
write that only queues, ambiguity asking and changing nothing, a clash flagged
but not blocking, junk degrading, and the reply always landing first. Plus a
source scan: no mini can reach `gcal.apply_change` or the calendar API.
`calbench` passes unmodified.

### S7 · Prove the property ✅ done 2026-08-21

Registered a fourth mini and measured what grew:

| | before | after | delta |
|---|---|---|---|
| minis registered | 3 | 4 | +1 |
| **Main Tiwa's prompt** | 4,609 | 4,609 | **+0** |
| dispatch prompt | 1,823 | 1,897 | +74 (one line) |

And the containment, measured rather than asserted:

| | chars Main reads |
|---|---|
| the 4 music tool descriptions she used to read | **1,643** |
| the one `dj` line she reads instead | **312** |

**5× less, and flat** — DJ can gain tools forever and that 312 does not move.
That is the number `docs/concepts/tools.md` was worrying about when it deleted
`now_playing`, and it is the reason Home Assistant's five or six new tools are
now affordable.

**Check:** `tests/growthbench.py` — the delta table above, plus assertions that
`turn.py` never touches `tools.TOOLS`, never runs the tool pass, and that the
action/speech split is declared rather than guessed.

### S7 · Prove the property — the whole thesis in one number

**Do:** add a trivial 4th mini (a timer, whatever). Count Main Tiwa's prompt
tokens before and after.

**They must be the same.**

If Main's prompt grew when you added a mini, the contract leaked and this design
didn't do the thing it exists for. You find that out in an afternoon rather than
a month.

---

## Repo

Feature branch. Not a separate repo — `memory.py` and its guards are shared, and
two diverging copies means one of them stops being the guarantee.

```
git switch -c swarm
```

```
tiwa/
  tools.py         # S0: Turn lives here, next to the flags it replaced
  minis.py         # S1: @mini + clean() + dispatch(). S2 adds DJ Tiwa here
  pipeline.py      # untouched — the control arm for A/B
  record.py        # later: JSONL export, tagged
tests/
  turnbench.py     # S0: concurrency
  minibench.py     # S1: dispatch layer, offline
  dispatchbench.py # later: routing accuracy, live
  latbench.py      # later: p50/p95 per arch
```

One module per layer until a file gets long enough to hurt — `tools.py` carries a
registry and ten tools in 270 lines and nobody has wanted it split.

One knob, mirroring `TIWA_MODE`: `TIWA_TURN=serial|concurrent`, default `serial`.

### Fine-tune data stays clean

The recorder doesn't exist yet (`PLAN.md:345` lists it as unbuilt), so design it
once, now. Every row gets `arch`, `turn_id`, `pass`.

The persona training set is `WHERE pass='persona'` — **identical under both
architectures**, because Main Tiwa's call shape doesn't change. Mini traces are
different `pass` values and never match the filter.

### Abandon if

- p50 turn latency isn't under 3,500ms after S3, **and** S7 shows Main's prompt
  growing anyway — then you got neither thing you came for
- dispatch accuracy is worse than today's tool selection (`dispatchbench`)
- **`test_memory.py` needs a single line changed to pass**

That last one is not a test to update. It's the exit condition firing. The
coercion guarantee lives below the turn layer; if concurrency reaches it,
something is in the wrong place.

---

### Completeness pass ✅ 2026-08-21

Two gaps found by auditing the registry against the plan rather than by any bench.

**Voice was orphaned.** The concurrent path has no tool pass and no mini owns
voice, so nothing could set `PENDING_JOIN` / `PENDING_LEAVE` — *"come join the
vc"* silently did nothing. Every bench passed; the serial path still worked. Now
`pipeline._asked_voice()`, a phrase list plus the measured `voice.wants_now()`
veto. 0 join/leave calls in 135 logged, so a model round trip for it would be
absurd — but losing it *silently* is the failure this codebase minds most.

`growthbench` now requires **every tool to name an owner**, so this cannot
recur. Writing that check found two more (`recall`, `calendar_read`) which turned
out to be superseded by code rather than dead — the check had been asking "is
this function called" instead of "is this capability reachable".

**`record.py` was promised and never built.** It is the filter, not new plumbing:
`llm_log` already had every call. A persona row is one whose **first system
message IS `prompts/tiwa.md`** — string equality against the file, not a keyword
guess. Every other pass opens differently, so mini traces cannot match. On the
real log: 400 rows in, **80 out**, every other pass falling out by its own system
prompt.

No `arch` tag, and none is needed: `pipeline.say()` is shared and `forkbench`
asserts both paths build a byte-identical state block, so a persona row is the
same row either way.

`--drop-state` exists because there is one real judgment call here and it is not
mine — `next.md` wants fine-tuning so she sounds like herself *"without prompt
crutches"*, and training with the `[inner-state]` block in teaches her to need it.
Export both and compare.

**One more caught in passing:** `_asked_voice` imported `voice` (discord,
whisper, onnxruntime — 0.24s) on every turn to run a pure-text veto. `forkbench`
caught it as 0.54s for two 0.3s calls. It now imports only when a phrase actually
matched.

### Voice is DJ-only ✅ 2026-08-21

`TIWA_VOICE=dj` (the default) makes the voice channel a **speaker for music and
nothing else**. `full` restores the rest.

| | under `dj` |
|---|---|
| ears (Whisper, wake word) | off — `LISTEN` is held false regardless of `TIWA_LISTEN` |
| TTS (`voice.say`) | no-op |
| conversational join / leave | inert, and **says so** |
| `_flush_music` auto-join | **live** — a song still pulls her into the channel |
| typed `join` / `leave` | live — that is how you get her out |

Most of this was already off: `TIWA_LISTEN` defaulted to `0`, and `voice.say()`
is only reachable from `_heard`, which needs ears. The only thing genuinely still
running was her deciding to join or leave mid-conversation.

**Inert, not silent.** The two tools return *"you do not do voice chat right now
— the voice channel is only for playing music. Say so; do not claim you joined or
left."* A tool that quietly sets nothing is the confabulation shape this codebase
keeps finding: she says she did it and did not.

The `_asked_voice` classifier stays wired and stays correct — `growthbench`
flips the flag and re-runs all ten cases under `full`, so turning voice back on
is one env var rather than a rewrite. It also asserts `voice.join()` is **not**
gated, because music is what the channel is still for.

## Where it stands

All eight gates are built and every offline bench is green. `TIWA_TURN` still
defaults to `serial` — the swarm proves itself before it becomes the default.

```
py -X utf8 -m tiwa.tools     py -X utf8 tests/turnbench.py    (S0)
py -X utf8 -m tiwa.minis     py -X utf8 tests/minibench.py    (S1)
py -X utf8 tests/djminibench.py                               (S2)
py -X utf8 tests/forkbench.py                                 (S3)
py -X utf8 tests/latebench.py                                 (S4)
py -X utf8 tests/searchminibench.py                           (S5)
py -X utf8 tests/calminibench.py                              (S6)
py -X utf8 tests/growthbench.py                               (S7)
py -X utf8 tests/recordbench.py    py -X utf8 -m tiwa.record  (recorder)
```

Untouched and still passing: `djbench`, `panelbench`, `test_memory`, `calbench`.

**Still owed, and all of it blocked on one thing.** `OPENROUTER_API_KEY` returns
401, so nothing below has been run against a real model:

- `latbench` — live p50/p95, and whether S3 actually hits 3,500 ms
- `dispatchbench` — routing accuracy on the labelled set
- the persona A/B judge
- one real conversation through `TIWA_TURN=concurrent`

The offline benches prove the calls overlap, the guards hold and the containment
is real. **They do not prove she still sounds like herself.** Only a live A/B does.

## How to know it worked

| question | how you check |
|---|---|
| Does adding a skill stay cheap? | **S7, answered: +0 chars.** `growthbench` |
| Does she pick the right mini? | `dispatchbench` — labels mined free from the 135 tool calls already in `log`, weighted by real traffic (53% music, 41% recall, 6% search). Report the **false-positive rate on the 59% of turns that should dispatch nothing** |
| Is she faster? | `latbench` — p50 < 3,500ms |
| Does she still sound like herself? | Blind A/B: same input, serial vs concurrent, judge picks which is more Tiwa. **Not** 1-5 scoring — LLM judges only hit ~69% on role identification vs 90.8% for humans. Weight the set toward the escalation ladder, since sharp personas drift hardest |
| Is she still coercion-proof? | `test_memory.py`, unchanged, green |

---

## Open

1. **`note_provisional`** — the one genuinely new write path. Needs a TTL and a
   guard chain, and tests written *before* the tool exists. Or drop it.
2. **Voice or text?** If the 2s target is for voice, the budget is ~800ms and the
   answer is streaming + filler phrases — a different project.
3. **Heartbeat first?** A provider outage permanently kills the idle loop today
   (`docs/open-questions.md`, still open). Adding threads around an unguarded
   loop makes it intermittent instead of reproducible. It's a `try` block.
