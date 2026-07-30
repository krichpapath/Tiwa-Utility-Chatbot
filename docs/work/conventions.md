# Conventions and gotchas

The unwritten rules. Most of these cost somebody a debugging session.

## Always `-X utf8`

```bash
py -X utf8 bot.py
```

She replies in Thai. Without this flag Windows uses a legacy console encoding and you get
`UnicodeEncodeError` mid-reply. Every command in this guide has it. `run_forever.cmd` has
it. Add it to muscle memory.

## `ponytail:` comments are deliberate ceilings

```python
# ponytail: python substring scan over all entity names; FTS5 when table gets big
```

A `ponytail:` comment means **"this is knowingly the simplest thing that works, and here
is what to do when it stops working."** It is not a TODO and not an apology. Don't
"improve" one without evidence it's hurting.

Current ones worth knowing: substring recall instead of FTS5 · one music deck for all
guilds · calendar supports only add and cancel · max 3 tool rounds per turn.

## Prompts reduce, code decides

The project's central lesson, learned twice the hard way. A prompt rule cuts a failure
rate; only code takes it to zero. Both coercion and confabulation had prompt-only fixes
that leaked, and the measured drop from "prompt" to "code" was 4/7 → 0 and 4 → 0.

So: prompts for *tendencies*, code for *guarantees*. If something must never happen, it
goes in a function.

## One string argument per tool

Never add a second parameter or a nested object. See
[the tool registry](../concepts/tools.md#every-tool-takes-one-string-named-name).

## A surface is not a brain

`voice.py` and `music.py` convert to and from text and call the same
`pipeline.respond()`. Nothing about being audio reaches the pipeline.

## Benches print tables, not dots

There's no pytest. A bench is a script that runs real code and prints a markdown table
you read and judge. Some are stochastic — they measure a model, so 9/12 is a result, not
a failure. See [the bench suite](testing.md).

## Windows and Smart App Control

Unsigned DLLs are blocked and there's no exception mechanism. Before adding a dependency
with native code, check it loads. Two libraries have already been vetoed —
[details](../toolchain/audio-speech.md#constraint-2-smart-app-control).

## Thai is not decoration

Thai strings in tool descriptions, wake-word lists, and stop-word lists are load-bearing.
`เปิดเพลง` in `play_music`'s description is the difference between working and not. Don't
"clean up" Thai you can't read — the [glossary](../reference/glossary.md) explains the
common ones.

## Numbers in the code are mostly chosen, not measured

Unless a comment says otherwise, a constant is somebody's reasonable guess. **Measured**
ones carry the number in a comment (`4 is the measured sweet spot`, `0.02 sits in the
gap`). **Chosen** ones include `EPISODES_KEEP = 25`, `LLM_LOG_KEEP = 400`, history
`maxlen=40`, the `hist[-9:-1]` window, and `MIN_UTTERANCE_S = 0.4`. Change them freely
with a reason.

## Log everything through one place

Model calls go through `llm.chat()`, which logs prompt, reply, tokens and latency. Actions
go through `memory.log()`. Both feed [the panel](../surfaces/panel.md). A new code path
that skips them is invisible when you're debugging, and it quietly breaks
[modes](../concepts/modes.md) too — `gcal.apply_change()` built its own Ollama client until
2026-07-30, which made `api` mode secretly need a local model
([F3](../reference/findings.md)).

## The repo state itself

- **Most of the project is uncommitted or untracked** — `dashboard.py`, `tiwa/voice.py`,
  `tiwa/music.py`, every bench. `git log` shows 2 commits. Don't trust the commit history
  as a record of the design; `PLAN.md` is the real one.
- **`.env`, `client_secret*.json` and `data/` are gitignored.** Keep it that way. Her
  memory contains real conversations about real people.
- **`PLAN.md` is the historical record**, not current state. It holds the plan as written
  plus every measurement taken along the way, so it is deliberately *not* rewritten when
  reality moves — the stale claims are marked `SUPERSEDED` in place instead. Trust the
  code, then this guide, then `PLAN.md`. Keep annotating it that way; don't tidy it.

## Gotchas index

| Symptom | Cause | Page |
|---|---|---|
| `UnicodeEncodeError` | missing `-X utf8` | above |
| She ignores you in a server | not `@`-mentioned | [Discord](../surfaces/discord.md) |
| Settings change did nothing | env read at import — restart | [panel](../surfaces/panel.md) |
| `calendar unavailable` | never ran `gcal_auth.py` | [calendar](../surfaces/calendar.md) |
| Music plays nothing, no error | tool never fired — read the llm tab | [tools](../concepts/tools.md) |
| `NotImplementedError` on every song | MRO order in `music.source()` | [music](../surfaces/music.md) |
| Listener dies after one utterance | voice_recv router, needs the patch | [transport](../toolchain/discord-transport.md) |
| "Thanks for watching!" from silence | VAD disabled | [voice in](../surfaces/voice-in.md) |
| A user's claim became her belief | a guard was removed | [guards](../concepts/guards.md) |
