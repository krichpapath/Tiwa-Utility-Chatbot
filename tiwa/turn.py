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


async def _work(db, author: str, text: str, recent: str, asked_music: bool) -> dict:
    """The other half of the fork. Never blocks her reply."""
    out = await minis.dispatch(db, author, text, recent)
    jobs = list(out["dispatch"])

    # The net UNDER the router, not inside it. _missed_music fires when the model
    # failed to notice a music ask — about 1 in 4-6 — and a net that only runs
    # when the router already noticed is not a net. It is deterministic, it is
    # measured across 24 dated cases in djbench, and it costs no call.
    if asked_music and not any(name == "dj" for name, _ in jobs):
        jobs.append(("dj", text))
        memory.log(db, "mini", f"dj({text[:60]!r}) -> forced, dispatch skipped it")

    if not jobs:
        return {"jobs": [], "results": [], "ask": out["ask"]}
    # minis run concurrently with each other too — two jobs cost one job's wait
    results = await asyncio.gather(
        *(asyncio.to_thread(minis.run, db, name, task) for name, task in jobs)
    )
    return {"jobs": jobs, "results": list(results), "ask": out["ask"]}


async def respond(db, hist: list, author: str, text: str, images=()) -> str:
    """One Tiwa turn, forked. Same signature as pipeline.respond()."""
    turn0 = time.perf_counter()
    tools.new_turn()
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

    reply, work = await asyncio.gather(
        pipeline.say(db, hist, state),
        _work(db, author, text, recent, asked_music),
    )
    # S4 turns `work` into a follow-up message or next turn's state. Until then it
    # is logged, and the Turn flags it set are drained by bot.py after the reply —
    # which is how music already behaved, so music is correct on this path today.
    memory.log(db, "turn", f"{author}: {text[:100]} -> {reply[:120]}",
               (time.perf_counter() - turn0) * 1000)
    return reply
