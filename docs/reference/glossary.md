# Glossary

Project and domain terms, including the Thai you'll meet in the code.

## Project terms

**Pass**
: One model call within a turn. There are three: [inner/tools, persona, extraction](../concepts/three-passes.md).
Two more fire only when needed (*seeing* an image, forcing music search terms), and two
run when nobody is talking (*idle*, *reflecting*).

**Brief**
: The short plain-text note pass 1 writes *to her* — what she knows, what she doesn't.
Never shown to the user. Labelled "background, do not recite".

**Turn**
: One user message and everything that happens because of it: up to three model calls,
tool flushes, and a memory write.

**Inner state**
: The second system message handed to pass 2, rebuilt every turn from live state:
language, who she's talking to, per-turn rules, [action state](../concepts/action-state.md),
and the brief.

**Per-turn rules**
: Rules injected in code on every turn because the model forgets them when they're buried
in the long persona prompt. The strongest prompt-level lever in the project.

**Fact** (a relation)
: A durable triple — `Steven · plays · guitar`. Lives in `relations`. Permanent until
deleted.

**Episode**
: A short sentence about something that *happened*, as opposed to something that is
true. `Steven plays guitar` is a fact; `first heard about Steven` is an episode. Written
only when the turn [surprised](#surprise) her, capped at 25 per person. *Not* a transcript.

**Surprise** {#surprise}
: The test that decides whether a turn is worth an episode: did it **move the graph**?
Either she met a subject she'd never met, or a belief flipped. Nothing else counts.
Named after how brains work — you remember the drive where a deer ran out, not the other
four hundred. [How it works](../concepts/memory.md#surprise).

**Reflection**
: A conclusion she draws while nobody is talking to her, from several episodes at once —
*"Krich is always the one telling me about other people."* Stored as an episode under her
own name, so it becomes something she can recall later.
[How it works](../concepts/memory.md#reflection).

**Watermark**
: The bookmark that says "I've already thought about everything up to here." It's just her
newest reflection's timestamp — no extra table. A reflection that concluded nothing still
writes a **blank** row so the bookmark moves; blanks are filtered everywhere she reads.

**Axis**
: A group of relations that are competing answers to the *same question*. `likes` and
`hates` are one axis, so the newer one replaces the older. `plays` and `likes` are not —
you can play a game and like it. There is exactly one axis today (`feel`).

**Entity**
: A node in her graph — a person, character, or thing. Case-insensitive names, no aliases
(so "Gojo" and "Gojo Satoru" are two nodes).

**Guard**
: A safety property enforced in code, never in a prompt. There are
[four](../concepts/guards.md).

**Coercion**
: A user trying to write her beliefs — "you love X", "you hate Y". Blocked in code; logged
as an episode about the attempt.

**Confabulation**
: Her inventing shared history for flavour — *"last time he showed up empty-handed"*.
Sincere-sounding fiction. Blocked from becoming fact.

**Stance**
: The only class of claim she may make about herself — `likes`, `hates`, `wants`, `thinks`…
Present-tense opinion, never a past event.

**Grounding**
: The check that a proposed fact's words actually appear in what the user said (or, for her
own stances, in her own reply).

**The ✅ gate**
: The pattern where a tool queues a request and a human reaction executes it. Used for
calendar writes; intended for anything risky.

**The deck**
: `music.NOW` plus `music.QUEUE` — what's playing and what's next.

**Action state**
: `pipeline._doing()`, the function that tells her what she is actually doing this turn.

**Flush**
: `bot.py` draining the flags tools set, *after* her reply is on screen. Order matters.

**Surface**
: A way of talking to her — Discord text, voice, terminal. A surface converts to and from
text and calls the same pipeline. **A surface is not a brain.**

**Pass label**
: On the panel's llm tab, which pass a logged call belongs to: *thinking*, *her reply*,
*remembering*, *seeing*, *reflecting*, *idle*. The fastest way to answer "why did she do
that?"

**Bench**
: A runnable script in `tests/` that prints a table you read and judge. Not a unit test.
See [the bench suite](../work/testing.md).

**`ponytail:`**
: A comment marking a deliberate simplicity ceiling — "this is knowingly the lazy solution,
and here's what to do when it stops working." Not a TODO.

**Gate (G1–G11)**
: A milestone in `PLAN.md`. Nothing proceeds until the author judges the previous one a
success. G1–G9 are done or built; G10 and G11 aren't started.

**Abliterated**
: A model build with refusal behaviour trained out. Used locally so she stays in character
when a conversation turns hostile. [ADR-013](decisions.md#adr-013-the-local-model-is-an-abliterated-build).

## External terms

**DAVE**
: Discord Audio/Video End-to-end encryption. Mandatory for voice since 2026-03-02, which
is why `voice.py` carries a decryption patch.

**VAD**
: Voice activity detection — deciding whether audio contains speech at all. Mandatory here
because Whisper hallucinates on noise.

**Ducking**
: Lowering (here: pausing) music while someone speaks.

**Smart App Control**
: Windows feature that blocks unsigned binaries. Enforced on this machine; it vetoed two
libraries. [Details](../toolchain/audio-speech.md#constraint-2-smart-app-control).

**Opus**
: The audio codec Discord uses.

**MRO**
: Python's method resolution order. Relevant because `music.source()` builds a class whose
base order decides whether playback works at all.

## Thai you'll meet in the code

Her voice is Thai-first, and these strings are load-bearing — they're in tool
descriptions, wake-word lists, and stop-word lists.

| Thai | Reads as | Means | Where |
|---|---|---|---|
| ทิวา | *Tiwa* | her name | everywhere |
| ที่ว่า | *tee-wa* | *"that which"* — what Whisper hears instead of her name | wake-word variants |
| หนู | *nuu* | "I", used by a younger woman to someone close. **Her register** — never ฉัน | persona rules |
| มึง | *mueng* | "you", blunt and familiar. How she addresses you | persona rules |
| เปิดเพลง | *bpert pleng* | "play a song" | `play_music` |
| ขอเพลง | *kor pleng* | "can I have a song" | `play_music` |
| อยากฟัง | *yaak fang* | "I want to hear" | `play_music` |
| เลือกให้หน่อย | *lueak hai noi* | "pick one for me" — an ask with **no song named** | `play_music` |
| อะไรก็ได้ | *arai gor dai* | "anything is fine" | `play_music` |
| หยุดเพลง / ปิดเพลง | *yut / bpit pleng* | "stop the music" | `stop_music` |
| ข้ามเพลง | *kaam pleng* | "skip the song" | `skip_music` |
| ต่อด้วย | *tor duay* | "then play…" | `queue_music` |
| เข้ามา / เข้าห้อง | *kao maa / kao hong* | "come in" / "join the room" | join commands |
| ออกไป / ออกห้อง | *ork bpai / ork hong* | "get out" / "leave the room" | leave commands |
| เดี๋ยว, ทีหลัง, ตอนดึก, สักพัก | — | "in a bit", "later", "late tonight", "for a while" | the **future-tense veto** — these stop her acting now |

The future-tense list matters more than it looks: without ตอนดึก in it,
*"ออกไปตอนดึกนะ"* ("leave later tonight") would have hung up on a live call.
