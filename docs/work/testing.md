# The bench suite

## What this is

Twenty scripts in `tests/`. Not unit tests — each one runs real code and **prints a
markdown table you read and judge**. No pytest, no fixtures, no CI.

## Why it's here

Most of what can go wrong here is a model behaving differently, not a function returning
the wrong value. `assertEqual` can't see "she sounds flat" or "she picked the wrong
tool 2 times in 6". A table can.

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
| `toolbench.py` | measure | Right tool, right argument, latency per provider | no |
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
| `factbench.py` | Confabulation: 7 invented facts → 0 once the code guard landed |
| `moodbench.py` | The anger arc across turns, and that naming her mood made it worse |
| `episodebench.py` | Episode noise vs real events after raising the bar |
| `leavebench.py` | She can leave a call, and won't confuse it with stopping music |

**Voice track (parked with the feature)** — `voicebench.py`, `wakebench.py` (32 wake
phrasings), `noisebench.py`, `ttsbench.py`, `dumpbench.py`, `routerbench.py`
(asserts the voice_recv patch is installed).

**Music** — `musicbench.py` does a real search and decode; needs network.

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
  monkeypatches `memory.connect` for exactly this.
- **Import `bot`, don't reimplement it.** Bugs lived in the glue between the code and
  discord.py precisely because that glue was untestable. `bot.py` is import-safe now.
- **Test the class Discord actually drives.** A `NotImplementedError` shipped because the
  bench tested `Stream` directly while Discord uses the wrapper. `music.source()` now has
  an `assert` and the bench uses the real path.
- **Seed the memory your scenario assumes.** With an empty graph the inner pass burns the
  turn on "who is this stranger" — that alone cost 5 of 6 music asks in one measured run.

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
