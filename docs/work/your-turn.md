# Your turn

Three exercises, increasing in scope. No solutions here — hints and a definition of done.
Do them in order; each assumes the last.

---

## 1 · Give her a new opinion (15 minutes)

**Goal.** Change something about how she talks and see it in a real reply.

Pick one:

- Make her refuse to answer in English before noon.
- Make her use a nickname for you instead of your Discord name.
- Give her a strong opinion about a food.

**Files likely involved.** `prompts/tiwa.md`, possibly `tiwa/pipeline.py` (per-turn rules).

**Hint.** Decide *which lever* first. A personality trait belongs in `prompts/tiwa.md`.
Something that must happen every single turn belongs in the per-turn rules where she can't
forget it — [order of effectiveness](conventions.md#prompts-reduce-code-decides).

**Done when.** `py -X utf8 chat.py yourname` shows the new behaviour twice in a row, and
you can explain why you chose the lever you chose.

---

## 2 · Ship a tool end to end (60–90 minutes)

**Goal.** Add `remind_me` — she takes "remind me to stretch in 20 minutes" and messages
you when the time comes.

This touches three layers: the registry, the flag-and-flush pattern, and the Discord loop.

**Files likely involved.**

```
tiwa/tools.py   the tool + a module-level pending list
bot.py          a tasks.loop that fires due reminders
tests/          a bench proving one fires and an unrelated message doesn't
```

**Hints.**

- The tool must **not** sleep or send anything. It parses nothing and reaches nothing —
  it appends to a list, like [`calendar_write`](../surfaces/calendar.md) does.
- Copy the flush pattern from `_flush_music()`. Note *where* in `on_message` the flushes
  sit and why leaving is last.
- Look at `idle_turn` for how a `tasks.loop` is registered and started.
- Relative time ("in 20 minutes") is a parsing problem. `gcal.apply_change()` shows the
  project's answer: a schema-constrained model call with the current time in the prompt.
  Do that *after* the tool fires, not inside it.
- Write the tool description like it's code. Include Thai — `เตือนหนู`, `อีก 20 นาที`.
- Should she be able to *decline* a reminder? Look at how `play_music` lets her refuse and
  decide deliberately.

**Done when.**

1. `py -X utf8 -m tiwa.tools` passes.
2. In Discord, "@tiwa remind me to stretch in 2 minutes" gets a reply *and* a message two
   minutes later.
3. Your bench passes and fails for the right reasons.
4. You did not modify `pipeline.respond()`. If you think you had to, re-read
   [a surface is not a brain](conventions.md#a-surface-is-not-a-brain).

**Success criterion for this guide:** you should be able to finish this without asking the
original author anything. If you got stuck on something the docs didn't cover, that's a
docs bug — note it in [docs maintenance](../reference/docs-maintenance.md).

---

## 3 · Debug a bug that actually shipped (45 minutes)

Real bug, real symptom. She answered once in voice, then went permanently deaf. The
console showed:

```
listener hiccup, still listening: ...
```

…and then nothing, ever again. The message says "still listening". She was not.

**Your job.** Find the class of mistake, then find the two *different* bugs in this story
that produced the same user-visible symptom.

**Hints.**

- Read `tiwa/voice.py` around `_watch`, `_sweep`, and `say()`.
- Bug one: the error handler itself raised. Ask what happens to a `try/except` block whose
  `except` body references a name that was never imported.
- Bug two: something waited forever *while holding a lock*. Ask which lock, who else
  needed it, and what happens if a callback you don't control never fires.
- Both fixes are in the code now. Find them, then explain why each is the *general* fix
  rather than a patch.

**Questions to answer (write them down):**

1. Why did the log say "still listening"?
2. Why is `asyncio.wait_for(done.wait(), timeout=...)` better than `await done.wait()` for
   a callback from a library thread?
3. Where else in this codebase could the same two mistakes hide? Name one specific place.

**Done when.** You can explain both bugs to somebody else, and you've found one place in
the codebase with the same shape of risk. There's nothing to commit — this one is for
your eyes.

---

## Then

[First week checklist](first-week.md).
