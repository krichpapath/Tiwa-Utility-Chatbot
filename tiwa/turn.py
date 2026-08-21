"""The concurrent turn: she starts talking while the minis work. TIWA_TURN=concurrent.

`pipeline.respond()` is the serial control arm and stays exactly as it was. This
is the other side of the A/B, and it shares every piece that decides what she
says — `pipeline._state()` builds the same block, `pipeline.say()` makes the same
call with the same sampling. Only the timing differs, which is the only way the
comparison means anything.

What changed, measured on her own log (111 turns):

    whole turn   p50 6431ms     inner pass runs 1.7x per turn
    inner pass   p50 2597ms     tools competing for one model's attention
    persona      p50 1958ms
    tools RUN in   3ms          the work was never what was slow

The six seconds was her deciding what to look up before she was allowed to open
her mouth. So the tool pass is gone from this path entirely:

    recall  (55 of 135 calls) -> memory.mentioned(), sqlite, 3ms
    music   (71 of 135 calls) -> DJ Tiwa, dispatched, flushed after she speaks
    search  ( 8 of 135 calls) -> Search Tiwa, late (S4)
    calendar( 1 of 135 calls) -> Calendar Tiwa

Nothing an LLM dispatches is on the path between her ears and her mouth.
"""
import asyncio
import time

from . import eyes, memory, minis, pipeline, tools
from .memory import TIWA


# Minis whose result is an ACTION bot.py has to flush — the deck, the ✅ gate.
# These finish before she speaks, because the flush runs the moment respond()
# returns. Everything else produces SPEECH and goes late.
ACTS = {"dj", "calendar"}
LATE_TIMEOUT = 6.0  # tool exec p95 is 4,337ms; past this she was not going to say it

# author -> the speech work still running for them. Genuinely cross-turn, like
# music.NOW — not a per-turn flag, so it does not belong on the Turn.
# ponytail: unbounded dict keyed by author. Cap it if she ever has many users.
_pending = {}


def _cancel_pending(author: str):
    """They spoke again. A search for the message before this one is stale.

    Only SPEECH is cancelled. A song they asked for still plays — that is the
    rule from SWARM.md S4, and it is why ACTS never lands in here.
    """
    task = _pending.pop(author, None)
    if task and not task.done():
        task.cancel()


async def _late(db, author: str, jobs: list, on_late) -> None:
    """A mini finished after she spoke. She says it herself, one line, or not at all."""
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(asyncio.to_thread(minis.run, db, n, t) for n, t in jobs)),
            timeout=LATE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # never a silent drop — a silent drop is how she starts believing she
        # knows something nobody ever told her
        memory.log(db, "mini", f"late({[n for n, _ in jobs]}) -> timed out")
        return
    except asyncio.CancelledError:
        memory.log(db, "mini", f"late({[n for n, _ in jobs]}) -> cancelled, they moved on")
        raise
    facts = [f"{n}: {r}" for (n, _), r in zip(jobs, results) if r]
    if not facts or on_late is None:
        memory.log(db, "mini", f"late({[n for n, _ in jobs]}) -> {facts or 'nothing'}")
        return
    # Rule 1: only Main Tiwa speaks. The facts do not reach the channel, she does.
    line = await pipeline.say(
        db,
        [{"role": "user", "content": f"{author}: (earlier message)"}],
        "You just this second found out what you went to look up. Say it to "
        f"{author} in ONE short line, in your own voice, as a follow-up to what "
        "you already said. Do not greet them and do not explain that you looked "
        "it up.\n" + "\n".join(facts),
    )
    if line:
        await on_late(line)
    memory.log(db, "mini", f"late({[n for n, _ in jobs]}) -> {line[:120]}")


async def _work(db, author: str, text: str, recent: str, asked_music: bool,
                on_late=None) -> dict:
    """Decide, then act. Only ACTS are awaited; speech is spun off and goes late."""
    out = await minis.dispatch(db, author, text, recent)
    jobs = [j for j in out["dispatch"] if not (asked_music and j[0] == "dj")]

    says = [j for j in jobs if j[0] not in ACTS]
    if says:
        _pending[author] = asyncio.create_task(_late(db, author, says, on_late))

    acts = [j for j in jobs if j[0] in ACTS]
    results = await asyncio.gather(
        *(asyncio.to_thread(minis.run, db, name, task) for name, task in acts)
    ) if acts else []
    return {"jobs": acts, "results": list(results), "ask": out["ask"]}


async def respond(db, hist: list, author: str, text: str, images=(),
                  on_late=None) -> str:
    """One Tiwa turn, forked.

    `on_late` is an async callable taking one string — bot.py passes the channel's
    send. Without it a late result is logged and dropped, which is what chat.py,
    the dashboard and the benches want: nothing there can receive a second
    message. Keyword-only in practice, so no existing caller changed.
    """
    turn0 = time.perf_counter()
    tools.new_turn()
    _cancel_pending(author)  # a search for their previous message is stale now
    recent = "\n".join(
        m["content"] if m["role"] == "user" else f"{TIWA}: {m['content']}"
        for m in hist[-9:-1]
    )
    # Sight stays ON the critical path on purpose: she is reacting to a picture
    # that is already on screen, and "I can't see it" a second later is worse
    # than waiting. It is also the rarest turn there is.
    seen = await asyncio.to_thread(eyes.look, db, images, text) if images else ""

    # everything below is code, ~3ms, and it is all she needs to start talking
    third = memory.mentioned(db, text, skip=author)
    asked_music = pipeline._missed_music(text)
    state = pipeline._state(
        db, author, text, seen,
        inner="",  # there is no tool pass on this path — `extra` is its replacement
        blind=bool(images) and not seen,
        extra=third,
        dispatching_music=asked_music,
    )

    # DJ starts NOW, not after dispatch. The classifier already said this is a
    # music ask, deterministically and for free — making the song wait ~2.6s for
    # a model to agree is the exact round trip this whole design removes.
    jobs = [asyncio.to_thread(minis.run, db, "dj", text)] if asked_music else []

    reply, *_ = await asyncio.gather(
        pipeline.say(db, hist, state),
        *jobs,
        _work(db, author, text, recent, asked_music, on_late),
    )
    memory.log(db, "turn", f"{author}: {text[:100]} -> {reply[:120]}",
               (time.perf_counter() - turn0) * 1000)
    return reply
