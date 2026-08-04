# First week checklist

Tick these off in order. Nothing here should need the original author.

## Day 1 — running

- [ ] Repo cloned, venv active, `requirements.txt` installed
- [ ] `.env` created with a mode chosen and secrets in place
- [ ] `py -X utf8 chat.py yourname` gets a reply
- [ ] Told her a fact about a friend, then asked about that friend next message — she
      remembered
- [ ] `py -X utf8 tests\test_memory.py` prints `SQL checks OK`
- [ ] Read [the 10-minute tour](../tour.md)

## Day 2 — the model

- [ ] `py -X utf8 dashboard.py --open` — status tab all green except calendar
- [ ] On the **llm** tab, found one turn and read all three passes for it
- [ ] Can name what each pass gets and what each is allowed to do
- [ ] Read [the guards](../concepts/guards.md) — the only page you must not skip
- [ ] Tried to make her believe something false ("you love X") and watched it *not* land
      in the memory tab

## Day 3 — Discord

- [ ] Bot invited to a server, Message Content intent on, `bot.py` running
- [ ] She answers on `@`-mention and ignores everything else
- [ ] Asked her for a song — it played
- [ ] Asked her to pick a song without naming one — it played
- [ ] Watched a queue, a skip and a stop, then checked the log tab
- [ ] Read [music and the DJ](../surfaces/music.md)

## Day 4 — change something

- [ ] Completed [exercise 1](your-turn.md#1-give-her-a-new-opinion-15-minutes)
- [ ] Can state the lever order — code > per-turn rules > tool description > persona
- [ ] Skimmed [conventions and gotchas](conventions.md) end to end
- [ ] Know why `-X utf8` is on every command

## Day 5 — ship

- [ ] Completed [exercise 2](your-turn.md#2-ship-a-tool-end-to-end-6090-minutes)
- [ ] Your bench runs green, and you made it fail on purpose once
- [ ] Ran the three offline benches before considering it done
- [ ] Did **not** touch `pipeline.respond()`

## Whenever

- [ ] [Exercise 3](your-turn.md#3-debug-a-bug-that-actually-shipped-45-minutes) — the
      debugging one
- [ ] Read the [decision log](../reference/decisions.md) once, start to finish
- [ ] Skimmed `PLAN.md` for the tracks that aren't built yet
- [ ] Picked something from [where to go next](../reference/next.md)

## Self-check

You're up to speed when you can answer these without looking:

1. What are the three passes, and which one can call tools?
2. Where would you add a new ability? A new setting? A new surface?
3. Why can't a user write her beliefs, and where is that enforced?
4. Why is there no ffmpeg, no ORM, and no agent framework?
5. What does `TIWA_MODE=api` change, and what does it cost?
6. Why does music start *after* she speaks?
7. What does a `ponytail:` comment mean?
8. What has to happen in a turn before she writes an episode — and why isn't a model
   asked to decide it?

Any you can't answer points at a page that didn't do its job. Say so — see
[docs maintenance](../reference/docs-maintenance.md).
