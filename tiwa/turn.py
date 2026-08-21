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
# Was 6.0, against a tool-exec p95 of 4,337ms — which left no room for the
# keyword call in front of the search. Measured live: one factual question in
# three said "I'll tell you the second I know" and then never did, because the
# lookup finished on the wrong side of the deadline. A promise she does not keep
# is its own small bluff. 10s matches ACT_TIMEOUT.
LATE_TIMEOUT = 10.0
# Deadline on the work she DOES wait for. Measured live: dispatch p50 ~0.9s,
# a mini ~1.5s. 10s is four times that, and it exists because a plain gather()
# let one real turn run 507 seconds on a stalled search.
ACT_TIMEOUT = 10.0
# Dispatch is on the path to her mouth ON PURPOSE (see respond). Bounded so a
# stalled router costs her a beat, not her voice. Measured p50 ~0.9s.
DISPATCH_TIMEOUT = 4.0

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


# Facts an ACTION mini produces that she should still say. Not `queued` — bot.py
# already posts the ✅ prompt for that, and saying it twice is worse than once.
VOICE_FIELDS = ("ask", "clash", "events")


async def _voice(db, author: str, facts: list, on_late, spoken=None,
                 lang: str = "English") -> str:
    """Say one more line, after the reply. Rule 1: the facts never reach the
    channel — she does.

    `spoken` is set the moment respond() has her words in hand. Without it a mini
    that finishes FASTER than the persona call sends its follow-up first, and she
    answers a question she has not asked yet. latebench only missed this because
    its fake search was slower than its fake persona.
    """
    if spoken is not None:
        await spoken.wait()
    if not facts or on_late is None:
        memory.log(db, "mini", f"late -> {facts or 'nothing'} (nowhere to send it)")
        return ""
    line = await pipeline.say(
        db,
        [{"role": "user", "content": f"{author}: (earlier message)"}],
        # _state() decides her language in CODE, never by the model. This block is
        # written here instead, so without the rule it inherited nothing: measured
        # live, an English question came back "ห้าพันดอลลาร์แล้วครับพี่".
        f"Reply in {lang} only. You just this second found out what you went to "
        f"look up. Say it to {author} in ONE short line, in your own voice, as a "
        "follow-up to what you already said. Do not greet them and do not explain "
        "that you looked it up.\n" + "\n".join(facts),
    )
    if line:
        await on_late(line)
    memory.log(db, "mini", f"late -> {line[:120]}")
    return line


async def _late(db, author: str, jobs: list, on_late, spoken=None,
                lang: str = "English") -> None:
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
    await _voice(db, author, [f"{n}: {r}" for (n, _), r in zip(jobs, results) if r],
                 on_late, spoken, lang)


def route(db, jobs: list, text: str, asked_music: bool,
          dj_already_running: bool = False) -> list:
    """The router's answer, with the deterministic rules applied over the top.

    This is the hybrid: the classifier does not merely back the model up, it
    also overrules it. Both directions were measured on 111 real turns
    (`dispatchbench`), and both fire on real traffic.

    Exported so the bench grades the SYSTEM rather than the model alone —
    grading the raw dispatch call would measure something production never runs.
    """
    out = list(jobs)

    # VETO. Dispatch answered `dj` to "มึงเล่นเพลงไรอยู่เนี่ย" (what song is even
    # playing), which would start a track over her answer — the exact failure
    # djbench exists for. _DECK_Q is the narrow list of phrases that are
    # unambiguously questions ABOUT the deck and never requests, so it cannot
    # swallow a polite ask or a youtube link the way the full _QUESTION list would.
    if pipeline._asked_deck(text):
        if any(n == "dj" for n, _ in out):
            memory.log(db, "mini", f"dj vetoed — {text[:50]!r} asks what is on")
        out = [j for j in out if j[0] != "dj"]
        return out

    # NET. The model failed to notice a music ask — measured at 1 in 4-6 before,
    # and dispatch still misses some. In respond() DJ is already running by now,
    # started at t=0; the bench has no such head start and asks for the job back.
    if asked_music:
        out = [j for j in out if j[0] != "dj"]
        if not dj_already_running:
            out.append(("dj", text))
    return out


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

    # code, ~3ms
    third = memory.mentioned(db, text, skip=author)
    asked_music = pipeline._missed_music(text)

    # Voice has no mini and there is no tool pass here, so without this nothing
    # could set the flags and "come join the vc" would silently do nothing.
    # Returns "" under TIWA_VOICE=dj (the default) — the voice channel is a
    # speaker for music, and _flush_music brings her in by itself. The classifier
    # stays wired so TIWA_VOICE=full is one env var, not a rewrite.
    want = pipeline._asked_voice(text)
    if want == "join":
        tools.join_voice(db, "")
    elif want == "leave":
        tools.leave_voice(db, "")

    # DJ starts NOW, not after dispatch. The classifier already said this is a
    # music ask, deterministically and for free — making the song wait for a
    # model to agree is the exact round trip this whole design removes.
    early = [asyncio.create_task(asyncio.to_thread(minis.run, db, "dj", text))
             ] if asked_music else []

    # Dispatch runs BEFORE she speaks, and this is a deliberate reversal.
    #
    # It used to run beside her, which is why she bluffed: asked "who won the
    # premier league last night" she answered "Man City. 2-1. Haaland scored
    # both" at 1.9s, and the real result — "Arsenal 2-1 Chelsea" — arrived after.
    # She cannot decline to answer something she does not know is being looked up.
    #
    # It costs almost nothing, which is why it is affordable. Measured: persona
    # ~1.9s, dispatch ~0.9s, a mini ~1.5s. The turn was ALREADY bounded by
    # dispatch+mini (2.4s), not by her words, so moving dispatch in front buys
    # correctness out of slack that was already being spent.
    jobs, ask = [], ""
    try:
        out = await asyncio.wait_for(
            minis.dispatch(db, author, text, recent), DISPATCH_TIMEOUT)
        jobs, ask = route(db, out["dispatch"], text, asked_music,
                          dj_already_running=bool(early)), out["ask"]
    except asyncio.TimeoutError:
        # she talks anyway rather than waiting on a stalled router
        memory.log(db, "mini", f"dispatch hit {DISPATCH_TIMEOUT}s — replied without it")

    # what she is allowed to know she is doing, before she has the answer
    looking_up = ", ".join(t for n, t in jobs if n not in ACTS)
    # _state() decides her language in CODE, never by the model. The follow-up
    # writes its own state block, so without this it answered an English
    # question in Thai — measured live, twice out of two.
    lang = "Thai" if any("฀" <= c <= "๿" for c in text) else "English"
    state = pipeline._state(
        db, author, text, seen,
        inner="",  # there is no tool pass on this path — `extra` is its replacement
        blind=bool(images) and not seen,
        extra=third,
        dispatching_music=asked_music or any(n == "dj" for n, _ in jobs),
        looking_up=looking_up,
    )

    spoken = asyncio.Event()  # nothing follows up before she has said the first thing

    # SPEECH is spun off and lands after she talks. ACTIONS are awaited, because
    # bot.py's flush runs the moment respond() returns.
    says = [j for j in jobs if j[0] not in ACTS]
    if says:
        _pending[author] = asyncio.create_task(
            _late(db, author, says, on_late, spoken, lang))
    acts = [asyncio.to_thread(minis.run, db, n, t) for n, t in jobs if n in ACTS]

    reply = await pipeline.say(db, hist, state)
    # Her words are ready. Everything still owed gets a DEADLINE, because a plain
    # gather() has none: latbench measured one live turn at 507 SECONDS with no
    # model call over 60s — a stalled search in a worker thread, held through
    # asyncio.run's executor shutdown. She had had the reply in hand since 1.9s.
    # Losing a song is recoverable; making her mute for eight minutes is not.
    results = []
    if early or acts:
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*early, *acts), ACT_TIMEOUT)
        except asyncio.TimeoutError:
            memory.log(db, "mini", f"acts hit {ACT_TIMEOUT}s — replied without them")
    spoken.set()
    memory.log(db, "turn", f"{author}: {text[:100]} -> {reply[:120]}",
               (time.perf_counter() - turn0) * 1000)

    # An ACTION mini finished before she spoke, so its facts could not reach the
    # state block — that was built at t=3ms. Calendar's "which Tuesday did you
    # mean" has to reach them anyway, so it goes out the same way a late search
    # does: as a second line, in her voice.
    said = [f"{v}" for r in results if isinstance(r, dict)
            for k, v in r.items() if k in VOICE_FIELDS and v]
    if said:
        _pending[author] = asyncio.create_task(
            _voice(db, author, said, on_late, lang=lang))
    return reply
