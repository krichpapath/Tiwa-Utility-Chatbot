# The bench suite

## What this is

Thirty-four scripts in `tests/`. Not unit tests — each one runs real code and **prints a
markdown table you read and judge**. No pytest, no fixtures, no CI.

## Why it's here

Most of what can go wrong here is a model behaving differently, not a function returning
the wrong value. `assertEqual` can't see "she sounds flat" or "she picked the wrong
mini 2 times in 6". A table can.

So benches come in two kinds, and telling them apart matters:

| Kind | Deterministic? | How to read it |
|---|---|---|
| **Checks** | yes — pass or fail | Any failure is a real bug |
| **Measures** | no — calls a model | A score. Compare against the score before your change |

## Start with these eight {#start-here}

Recommended canon. Run these and you know the system is sound:

| Bench | Kind | Protects | Runs offline? |
|---|---|---|---|
| `test_memory.py` | check | **The guards**, coercion + confabulation by name, plus the episode rate, belief supersession and the reflection pass | yes |
| `djbench.py` | check | The whole DJ engine — queue, skip, auto-advance, stop, action state | yes |
| `panelbench.py` | check | Control panel: 38 assertions incl. "never writes secrets" | yes |
| `dispatchbench.py` | measure | Right mini, right task, on 111 hand-corrected real turns | no |
| `pickbench.py` | measure | Music asks with **no song named** — the newest regression | no |
| `searchbench.py` | both | Search mechanics offline; `--live` shows the keywords she picks | partly |
| `eyebench.py` | both | [Her eyes](../surfaces/eyes.md): image → her voice, blind turns, zero cost when idle | partly |
| `smoke.py` | measure | Does she still sound like herself | no |

```bash
py -X utf8 tests\test_memory.py
```

```bash
py -X utf8 tests\djbench.py
```

```bash
py -X utf8 tests\panelbench.py
```

```bash
py -X utf8 tests\searchbench.py
```

```bash
py -X utf8 tests\eyebench.py
```

The first three need no API key and no Ollama. Run them before every commit.
`searchbench.py` and `eyebench.py` join them without `--live` — `searchbench`'s offline
half fakes `ddgs` and checks dedupe, region and formatting; `eyebench`'s fakes the vision
model and checks the plumbing, which is where every one of those bugs actually lives.

With `--live` both become measures. `searchbench --live` prints the query she chose for
five asks. `eyebench --live` sends four real images — a meme, a screenshot, a Thai sign
and a photo — through the real vision model for about $0.0006, and prints what she saw.
Both are the part only you can judge.

## The rest

**Memory and behaviour (measures)** — evidence for [decisions](../reference/decisions.md)
more than routine gates:

| Bench | What it measured |
|---|---|
| `extractbench.py` | Extraction quality per provider — direction errors, phantom entities, JSON leaking into names. `--think` A/Bs model reasoning on the write pass ([ADR-019](../reference/decisions.md#adr-019)) |
| `factbench.py` | Confabulation: 7 invented facts → 0 once the code guard landed. Defaults to ollama — pass `openrouter` to run it on the API path |
| `worthbench.py` | Whether a fact is worth a row, not just true. The convert rule (a music ask becomes a durable taste), and the drop log — five kinds of junk in, five reasons out, nothing written. Partly offline |
| `moodbench.py` | The anger arc across turns, and that naming her mood made it worse |
| `episodebench.py` | Episode noise vs real events after raising the bar |
| `outagebench.py` | A dead provider costs her a turn, never her voice — all three turn call sites, plus the heartbeat surviving three outages. Offline |

**Voice track (parked with the feature)** — `voicebench.py`, `wakebench.py` (32 wake
phrasings), `noisebench.py`, `ttsbench.py`, `dumpbench.py`, `routerbench.py`
(asserts the voice_recv patch is installed).

**Music** — `py -X utf8 -m tiwa.music` does a real search and decode; needs network.

**[The swarm](../concepts/the-swarm.md)** — thirteen benches, and on this branch they
cover the only turn shape there is. The nine offline ones need no key, no network and
no model: `llm.chat` is replaced by a sleeper or a canned answer. The four live ones
are the evidence the design was accepted on:

| Bench | Kind | Protects |
|---|---|---|
| `turnbench.py` | check | Two turns at once must not read each other's flags. Drives the real path — tools run inside `asyncio.to_thread`, so the fix only works if the context survives the thread hop |
| `minibench.py` | check | The dispatch layer: strict-schema legality at every depth, the literal word "json" in the prompt (DeepSeek returns empty without it), and six routing shapes including an invented mini and non-JSON junk |
| `forkbench.py` | check | The tool pass is gone but nothing she needed from it is. Wall-clock proves the shape — two calls, not three — and `TURN_MODE` cannot come back |
| `latebench.py` | check | Actions land before she speaks, speech lands after, neither is silent. Uses the *measured* uneven delays; equal ones hid a real bug once |
| `djminibench.py` | check | DJ Tiwa's action choice, the deck reaching the decision as a fact, and none of `play_music`'s instruction paragraph leaking into its return |
| `searchminibench.py` | check | Keywords not sentences, today's year in the prompt, a flake staying quiet — plus a source scan asserting **no mini can reach a memory write** |
| `calminibench.py` | check | Ambiguous date asks and queues nothing; a clash is flagged but still queued; the reply always lands before the follow-up |
| `growthbench.py` | check | **The thesis.** Register a fourth mini, assert Main Tiwa's prompt did not grow. Also that no tool registry can grow back, and that every actuator has a named caller — two were silently orphaned once |
| `latbench.py` | measure | Real turns from her own log, timed. p50 2.7s here against `main`'s 5.1s. One arm per run — `--tag` names it, and it writes `data/ab_<tag>.json` |
| `dispatchbench.py` | measure | Routing accuracy on 111 hand-corrected real turns: 93.7% vs the tool pass's 70.3% |
| `personabench.py` | measure | Blind pairwise A/B over **two** `latbench` files — run it on each branch, then hand both here. Each pair judged twice with the sides swapped. Not 1–5 scoring — [LLM judges hit ~69% on role identification](https://arxiv.org/pdf/2508.10014) where humans hit 90.8% |
| `chatbench.py` | measure | The turns that need **no mini at all** — 59% of real traffic, and where memory and personality are the whole product. Asserts no mini fires on any of them, then prints her replies for you to read |
| `livechat.py` | measure | One real scripted conversation end to end. The only thing that shows a *sequence* — deck carrying between turns, follow-ups landing, her staying in character while a mini works |

`recordbench.py` closes the set: one logged row of every pass that exists goes in, and
exactly one — hers — comes out. That is the guarantee that multi-agent traces cannot
pollute the fine-tune set, checked rather than trusted.

**Calendar** — `calbench.py`. Offline it asserts the ✅ gate: `calendar_write` queues a
sentence and reaches nothing else. `--live` runs seven asks through dispatch and
Calendar Tiwa and counts how many actually queued a write — the failure it exists for is her *saying* she
put something in the calendar without calling the tool. See
[getting it called](../surfaces/calendar.md#getting-it-called).

## How to write one

```python
sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, pipeline, tools

db = memory.connect(":memory:")        # never the real database
print("| asked | result | ok |")       # a table you can read
...
assert good >= 5, "regressed"          # exit non-zero on failure
```

Rules that come from real pain:

- **Never touch `data/tiwa.db`.** Use `:memory:` or a temp file. `panelbench.py`
  monkeypatches `memory.connect` for exactly this. **Importing `bot` is not enough** —
  `bot.db` is already the real database and `_start()` logs `playing <title>` to it, so
  `djbench.py` quietly wrote its six fixtures into the live activity log on every run.
  Found while reading that log for a real music bug, with `Bad Apple (video)` ×5 sitting
  in the middle of the evidence. If a bench drives `bot`, reassign `bot.db` first.
- **Import `bot`, don't reimplement it.** Bugs lived in the glue between the code and
  discord.py precisely because that glue was untestable. `bot.py` is import-safe now.
- **Test the class Discord actually drives.** A `NotImplementedError` shipped because the
  bench tested `Stream` directly while Discord uses the wrapper. `music.source()` now has
  an `assert` and the bench uses the real path.
- **Seed the memory your scenario assumes.** With an empty graph she has nothing to go on
  and the turn goes on "who is this stranger" — that alone cost 5 of 6 music asks in one
  measured run.

## Gotchas

- **Measures are noisy.** `pickbench` scored 4/6 then 5/6 with no code change. Run twice
  before concluding anything, and never tune on a single run — a temperature change once
  looked like an improvement and was noise.
- **API benches cost money.** They're small, but they hit `TIWA_DAILY_TOKENS`.
- **`smoke.py` needs judgement**, not a threshold. Read her replies and decide.
- **There is no CI.** These run when you run them.

## Go deeper

- [Your turn](your-turn.md) — exercises that end in a failing bench.
- [Common workflows](workflows.md#add-a-bench)
