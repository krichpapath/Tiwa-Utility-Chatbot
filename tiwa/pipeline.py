"""One Tiwa turn: dispatch to the Mini Tiwas, then her voice — while they work.

She starts talking before the minis finish. There is no tool pass on this branch
and no `TIWA_TURN` knob: this IS the turn. The serial three-pass shape it
replaced still lives on `main`, and that is the whole difference between the two
versions.

What changed, measured on her own log (111 turns) before the swap:

    whole turn   p50 6431ms     inner pass runs 1.7x per turn
    inner pass   p50 2597ms     tools competing for one model's attention
    persona      p50 1958ms
    tools RUN in   3ms          the work was never what was slow

The six seconds was her deciding what to look up before she was allowed to open
her mouth. So the tool pass is gone entirely, and each of its jobs went
somewhere cheaper:

    recall  (55 of 135 calls) -> memory.mentioned(), sqlite, 3ms
    music   (71 of 135 calls) -> DJ Tiwa, dispatched, flushed after she speaks
    search  ( 8 of 135 calls) -> Search Tiwa, late
    calendar( 1 of 135 calls) -> Calendar Tiwa

Nothing an LLM dispatches is on the path between her ears and her mouth.
Measured after: p50 2695ms against serial's 5059ms, routing 94.6% against 70.3%.
"""
import asyncio
import datetime
import json
import os
import re
import time
from pathlib import Path

from . import eyes, llm, memory, minis, tools
from .memory import MODEL, TIWA

PERSONA = (Path(__file__).parents[1] / "prompts" / "tiwa.md").read_text(encoding="utf-8")

# where her voice runs is decided by TIWA_MODE (see llm._MODES). On the API path
# expect hosted filters to soften her escalation and coercion refusals — that is
# a measured tradeoff (tests/smoke.py), not a free swap.
PERSONA_PROVIDER = llm.PERSONA_PROVIDER
PERSONA_MODEL = os.environ.get("TIWA_PERSONA_MODEL") or (
    llm.PERSONA_API_MODEL if PERSONA_PROVIDER == "openrouter" else MODEL
)

# Minis whose result is an ACTION bot.py has to flush — the deck, the ✅ gate.
# These finish before she speaks, because the flush runs the moment respond()
# returns. Everything else produces SPEECH and goes late.
ACTS = {"dj", "calendar"}
# Was 6.0, against a mini-exec p95 of 4,337ms — which left no room for the
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

# Facts an ACTION mini produces that she should still say. Not `queued` — bot.py
# already posts the ✅ prompt for that, and saying it twice is worse than once.
VOICE_FIELDS = ("ask", "clash", "events")


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"<tool_call>.*?(</tool_call>|$)", "", text, flags=re.S)  # 8B leaks these as text
    return re.sub(r"^(Tiwa|ทิวา)\s*:\s*", "", text.strip()).strip()


# A HINT, not a verdict. Read _maybe_music() before touching this.
#
# This used to be two lists doing precision work — exact phrases, anchored
# prefixes, a question veto — because on the serial path whatever matched here
# went straight to `play_music` with nobody to check it. It was the decision, so
# it had to be careful, and being careful made it narrow: measured against her
# own log, it silently missed 25 real music asks, including the bare word `skip`
# four separate times. "เปิดSunflowerให้หน่อย" missed for want of a space.
# "hero miliเล่นให้หน่อย" missed because the verb was not at the front.
#
# DJ Tiwa is the decision now, and it reads the whole message with the deck in
# front of it. So this stopped needing to be right and only needs to be EARLY:
# all it buys is starting DJ at t=0 beside dispatch instead of ~1.5s later. A
# false positive costs one cheap call that answers `none` and writes nothing.
#
# Which flips the trade completely: recall over precision. Single words, matched
# anywhere, in either language. When she misses a phrasing, add the word — but
# know that missing it only made her slower, never wrong.
_MUSIC_HINT = (
    # Thai — verbs, nouns, and the words that show up mid-session
    "เพลง", "ฟัง", "ร้อง", "เปิด", "ขอ", "เล่น", "ใส่", "คิว", "ต่อ", "ข้าม",
    "หยุด", "ปิด", "ดนตรี", "เสียง", "จัด", "หา", "เปลี่ยน", "อยาก", "พอแล้ว",
    "ถัดไป", "ดัง", "เบา", "อีกรอบ", "อัลบั้ม", "วง",
    # English
    "play", "queue", "song", "music", "listen", "track", "tune", "skip", "stop",
    "next", "pause", "resume", "vibe", "lofi", "ost", "remix", "album", "band",
    "artist", "volume", "louder", "quieter", "put on", "another one",
)

# The one thing that still has to be precise, because it PREVENTS an action.
# "ตอนนี้เปิดเพลงอะไรอยู่" (what song is on right now?) is not a request, and a
# random Thai song once went on over the top of her answer. _DECK_Q means "they
# are asking ABOUT the deck" — it vetoes DJ entirely AND, when nothing is on, is
# the one moment telling her the deck is empty is worth the tokens.
# Phrases, never the bare word อะไร — "เปิดเพลงอะไรก็ได้" (put on anything) is a
# real ask, and a looser "เพลงอะไร" swallows it. djbench catches that one.
_DECK_Q = ("อะไรอยู่", "ชื่ออะไร", "เล่นเพลงไร", "เพลงไรอยู่",
           # measured in dispatchbench: the router answered `dj` to a bare
           # "ชื่อเพลง" (song name) and would have started a track on top of the
           # answer. It is a deck question, so the deterministic veto owns it —
           # and _doing(asked_deck=True) now tells her what is on, which is right.
           "ชื่อเพลง",
           "what song", "which song", "what's playing", "what is playing",
           "what are you playing")


# Thai rarely writes "?", so a question mark alone misses most of hers. Biased
# towards over-detecting on purpose: this only decides whether an ask-nudge
# counted, and a false positive makes her ask LESS often, never more.
_QUESTION_MARK = ("?", "ไหม", "มั้ย", "อะไร", "ยังไง", "ไหน", "ทำไม", "เหรอ",
                  "ป่าว", "รึเปล่า", "หรือยัง", "ใคร")


def _is_question(reply: str) -> bool:
    """Did she actually ask them something?"""
    return any(q in reply for q in _QUESTION_MARK)


def _maybe_music(text: str) -> bool:
    """Might this be about music? A HINT, and only ever a hint.

    Answering yes starts DJ Tiwa at t=0, beside dispatch instead of ~1.5s behind
    it. Answering no costs a second and a half on a music turn. Nothing else
    hangs on it: DJ reads the message itself and answers `none` when it is not a
    music ask, and `respond()` waits for that verdict before she is told
    anything. So a false positive is one cheap call nobody sees.

    Which is why it is deliberately greedy, and why it is allowed to fire on
    turns that are plainly not music. Two ways in:

    A LIVE DECK. Mid-session almost everything is about the song — "skip",
    "ไม่ใช่ ของMili" (no, the Mili one), "อีกเพลง", "มันจบแล้ว เล่นอีกรอบ". None of
    those carry a music word at all, and the old phrase list missed every one of
    them. If something is playing or queued, just ask DJ.

    ONE HINT WORD. Otherwise any of _MUSIC_HINT, anywhere in the message, either
    language, no anchoring. "เปิดSunflowerให้หน่อย" needed no space; the word
    `skip` needed no sentence around it.

    Two things still stop it, and both prevent an ACTION rather than guess at
    one:
      - a music actuator already fired this turn (PENDING_MUSIC == "" is
        stop_music having fired — leave it alone)
      - they are asking what is ON, which _doing() answers for free from the deck
    """
    if tools.PENDING_MUSIC is not None or tools.DJ:
        return False
    if _asked_deck(text):
        return False  # asking about music is not asking for music
    from . import music  # lazy: keeps av out of import for non-Discord callers

    if music.NOW["title"] or music.QUEUE:
        return True
    low = text.lower()
    return any(w in low for w in _MUSIC_HINT)


def _terms(content: str) -> str:
    """First real line of the reply, unquoted. The 8B pads with <think> and prose."""
    lines = [ln for ln in _clean(content).splitlines() if ln.strip()]
    return lines[0].strip("\"' .`")[:80] if lines else ""



def _asked_deck(text: str) -> bool:
    """They asked what is on. The one moment an empty deck is worth telling her."""
    return any(q in text.lower() for q in _DECK_Q)


# These two lists were the join_voice / leave_voice tool descriptions, kept when
# the registry went. No mini owns voice — 0 calls in 135 logged — so an LLM round
# trip for it would be absurd. But losing it SILENTLY is the failure this
# codebase minds most, so it is a classifier instead.
# ponytail: phrase match, precision over recall, same stance as _MUSIC_ASK. A
# miss means you type `join`; a false positive drags her into a live call.
_JOIN = ("come join the vc", "join the vc", "join vc", "get in here", "hop in",
         "เข้ามา", "เข้าห้อง", "เข้ามาหน่อย", "เข้าวอย")
# "wanting the MUSIC to stop is NOT wanting you gone" — the leave_voice
# description says so, and stop_music shares no phrase with any of these.
_LEAVE = ("leave the vc", "leave vc", "get out", "ออกไป", "ออกห้อง",
          "ออกจากห้องเสียง", "ไปได้แล้ว")


def _asked_voice(text: str) -> str:
    """"join", "leave", "dj-only", or "".

    "dj-only" means they asked and she cannot: TIWA_VOICE=dj (the default) makes
    the channel a speaker for music. It is RETURNED rather than swallowed because
    losing it silently is how she says "ok, coming in" and then does not. That
    honesty used to come from the join_voice tool's return string landing in her
    brief; there is no brief now, so `_doing()` says it instead.

    A real join/leave is vetoed by voice.wants_now() for a FUTURE time — 'join us
    later tonight' is a plan, not an ask, and that veto is measured. Under
    dj-only the veto does not matter, since the honest answer is the same either
    way — and skipping it keeps `voice` unimported on a turn that cannot use it.
    """
    low = text.lower()
    want = ("leave" if any(p in low for p in _LEAVE)
            else "join" if any(p in low for p in _JOIN) else "")
    if not want:
        return ""  # the common case never imports voice — see below
    if tools.VOICE_DJ_ONLY:
        return "dj-only"
    # `voice` pulls in discord, whisper and onnxruntime, ~0.24s the first time.
    # Importing it per turn to run a pure-text veto put that on EVERY turn for
    # chat.py, the dashboard and the benches (forkbench caught it: 0.54s for two
    # 0.3s calls). It loads only when a phrase actually matched — 0 times in 135.
    from . import voice

    return want if voice.wants_now(text) else ""


def _doing(blind: bool = False,
           asked_deck: bool = False, dispatching_music: bool = False,
           asked: str = "", looking_up: str = "", voice_asked: bool = False) -> str:
    """What she is actually doing, read from live state — never from what the
    model believes it did.

    She used to need a now_playing tool call to learn her own deck, and would
    otherwise say "เปิดละ" with nothing queued (three turns running, measured).
    Both are the same bug: her actions were not in her context. One `if` per
    subsystem — when she gains a new one (lights, timers), add a line HERE.

    Doing nothing says NOTHING. Returns "" on a quiet turn: a standing "no music
    is playing" is context she pays for on every ordinary message and uses on
    almost none. The only negatives kept are the anti-confabulation guards, and
    each is narrow — `asked_deck` when the deck is empty and they asked,
    `looking_up` while a search is still out, `blind` when an image arrived and
    the vision call came back empty.
    """
    from . import music  # lazy: keeps av out of import for non-Discord callers

    out = []
    if music.NOW["title"]:
        line = f"You are playing {music.NOW['title']} right now."
        if music.QUEUE:
            line += " Queued next: " + ", ".join(t["title"] for t in music.QUEUE[:3]) + "."
        out.append(line + " You know this without looking it up — if they ask"
                          " what is on, just tell them. Knowing what is on is NOT"
                          " touching it: unless a line below says you did, do not"
                          " say or act out that you stopped, paused, skipped or"
                          " changed the music. Measured live: mid-song she wrote"
                          " '*สะดุด หยุดเพลง*' and the track never missed a beat.")
    elif asked_deck:
        # The deck is empty and they asked what is on. _doing() otherwise says
        # NOTHING on a silent turn — deliberately, because a standing "no music is
        # playing" is paid for on every message and used on almost none. But that
        # left her with zero state on exactly the turn someone asks, so she
        # invented a song. Narrow beats standing: this fires only when asked.
        out.append("NOTHING is playing right now and the queue is empty. Do NOT name"
                   " a song, do not say you are playing anything — you are not."
                   " Tell them nothing is on and offer to put something on.")
    # what she set in motion THIS turn: it is happening, whatever she thinks
    queued = list(tools.DJ) + ([("play", tools.PENDING_MUSIC)]
                               if tools.PENDING_MUSIC else [])
    if queued or dispatching_music:
        # DJ Tiwa usually has not run yet — she is speaking WHILE it searches —
        # so there is nothing in the Turn to name.
        #
        # "putting a song on" alone was NOT enough. Measured in a live A/B: told
        # only that something was happening, she filled the gap herself —
        # "เปิด Fat Rat ให้มึง 3 เพลงก่อนนะ — The Calling, Monody, และ Unity",
        # three titles she had never seen. A vacuum gets furnished. So she is
        # anchored to the only words that ARE grounded: the ones they typed.
        # Do NOT quote their message back here. Restating it made her echo it —
        # "alright, venom - eminem. coming up." — instead of reacting to it, and
        # the persona A/B went 0/12. Their message is already in `hist`; she can
        # read it herself. What she needs from this line is the prohibition, not
        # the words.
        what = (", ".join(f"{act} {arg}".strip() for act, arg in queued)
                if queued else "putting on what they just asked for")
        out.append(f"You have just done this: {what}. It IS happening — say so in"
                   " your own way. Never say you do not know the song or cannot"
                   " find it; you do not need to recognise a song to put it on."
                   " Do not sing or quote its lyrics. You have not seen the search"
                   " result yet, so do NOT name a SONG TITLE, artist, album or"
                   " year beyond the words they themselves used — those are"
                   " things you would be making up. Naming three tracks you have"
                   " not heard is the exact failure this line exists to stop.")
    if looking_up:
        # She is about to speak while a lookup runs. Without this she fills the
        # gap: traced live, "who won the premier league last night" got "Man City.
        # 2-1. Haaland scored both" at 1.9s, corrected to "Arsenal 2-1 Chelsea"
        # when the search actually returned. Same shape as the music guard —
        # something IS happening, and she has not seen the answer.
        out.append(f"You are looking up {looking_up} RIGHT NOW and the answer has"
                   " not come back yet. You do NOT know it. Do not state a result,"
                   " a score, a price, a number or a name for it — anything"
                   " specific you say here you are inventing. Say you are checking,"
                   " or say you do not know yet, in your own words. You will be"
                   " told the answer in a moment and can say it then.")
    if blind:
        # same shape as the music guard: the picture is right there in the channel,
        # so bluffing about it is caught instantly. Cheaper to admit it.
        out.append("They sent an image and your eyes did not work this time — you"
                   " genuinely cannot see it. Do NOT guess what is in it or act like"
                   " you looked. Say you cannot see it.")
    if voice_asked:
        # TIWA_VOICE=dj and they asked her into (or out of) the call. Inert is
        # fine; inert AND SILENT is not — that is how she answers "ok, coming in"
        # and then does not move. The join_voice tool used to hand her this
        # sentence; nothing reads tool returns any more, so it is said here.
        out.append("They want you to come into or leave the voice channel, and"
                   " that is not yours to decide right now — the channel is only"
                   " a speaker for music, you do not do voice chat. Say so in your"
                   " own words. Do NOT claim you joined, are joining, or left.")
    if tools.PENDING_LEAVE:
        out.append("You are leaving the voice channel as you say this.")
    return " ".join(out)


def _cancel_pending(author: str):
    """They spoke again. A search for the message before this one is stale.

    Only SPEECH is cancelled. A song they asked for still plays — that is the
    rule from SWARM.md S4, and it is why ACTS never lands in here.
    """
    task = _pending.pop(author, None)
    if task and not task.done():
        task.cancel()


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
    line = await say(
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


def route(db, jobs: list, text: str) -> list:
    """The router's answer, with the one deterministic rule applied over the top.

    There used to be a NET here as well as this veto: whenever the phrase
    classifier said "music", `dj` was forced into the job list. It is gone,
    because it never fired in production — `respond()` passed
    `dj_already_running=True` on exactly the turns that would have triggered it,
    so the branch existed only to make the bench agree with a system it was no
    longer modelling.

    Nothing is lost. DJ is reached two independent ways and neither is this one:
    the greedy `_maybe_music()` hint starts it at t=0, and the router asking for
    it starts it after dispatch. Whichever arrives, DJ decides.

    Exported so the bench grades the SYSTEM rather than the model alone.
    """
    # VETO. Dispatch answered `dj` to "มึงเล่นเพลงไรอยู่เนี่ย" (what song is even
    # playing), which would start a track over her answer — the exact failure
    # djbench exists for. _DECK_Q is the narrow list of phrases that are
    # unambiguously questions ABOUT the deck and never requests, so it cannot
    # swallow a polite ask or a youtube link.
    #
    # This one stays deterministic on purpose: it PREVENTS an action. A guess
    # that acts is unbounded, a guess that declines costs a repeated question.
    if _asked_deck(text):
        if any(n == "dj" for n, _ in jobs):
            memory.log(db, "mini", f"dj vetoed — {text[:50]!r} asks what is on")
        return [j for j in jobs if j[0] != "dj"]
    return list(jobs)


async def respond(db, hist: list, author: str, text: str, images=(),
                  on_late=None) -> str:
    """One Tiwa turn. `hist` = chat messages incl. the current one. Caller appends the reply.

    `images` = attachment urls on the current message. Empty on every ordinary
    turn, and an empty list costs exactly nothing — no vision call is made.

    `on_late` is an async callable taking one string — bot.py passes the
    channel's send. Without it a late result is logged and dropped, which is what
    chat.py, the dashboard and the benches want: nothing there can receive a
    second message.
    """
    turn0 = time.perf_counter()
    tools.new_turn()  # everything flagged this turn is scoped to this task
    _cancel_pending(author)  # a search for their previous message is stale now
    recent = "\n".join(
        m["content"] if m["role"] == "user" else f"{TIWA}: {m['content']}"
        # lines before the current message: resolve "he/she", and show whether a
        # fight is still on screen. This window IS the lifetime of her mood.
        for m in hist[-9:-1]
    )
    # Sight stays ON the critical path on purpose: she is reacting to a picture
    # that is already on screen, and "I can't see it" a second later is worse
    # than waiting. It is also the rarest turn there is.
    seen = await asyncio.to_thread(eyes.look, db, images, text) if images else ""

    # code, ~3ms. This is what the recall tool used to cost a model call for.
    third = memory.mentioned(db, text, skip=author)
    maybe_music = _maybe_music(text)

    # Voice has no mini — 0 calls in 135 logged turns — so the classifier reaches
    # the actuators directly. Returns "" under TIWA_VOICE=dj (the default), where
    # _flush_music brings her in by itself. Wired anyway, so TIWA_VOICE=full is
    # one env var and not a rewrite.
    want = _asked_voice(text)
    if want == "join":
        tools.join_voice(db)
    elif want == "leave":
        tools.leave_voice(db)

    # DJ starts NOW, not after dispatch. The hint already said music is
    # plausible, deterministically and for free — making the song wait for a
    # model to agree is the exact round trip this whole design removes. If the
    # hint is wrong, DJ answers  below and nobody ever finds out.
    early = [asyncio.create_task(asyncio.to_thread(minis.run, db, "dj", text))
             ] if maybe_music else []

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
    # dispatch also returns "ask" — the one thing it would have to ask before
    # acting. Nothing reads it: she is better at asking than the router is, and
    # she has the whole conversation to ask from. It stays in the schema because
    # writing the question is what stops the router guessing instead.
    jobs = []
    try:
        out = await asyncio.wait_for(
            minis.dispatch(db, author, text, recent), DISPATCH_TIMEOUT)
        jobs = route(db, out["dispatch"], text)
    except asyncio.TimeoutError:
        # she talks anyway rather than waiting on a stalled router
        memory.log(db, "mini", f"dispatch hit {DISPATCH_TIMEOUT}s — replied without it")

    # DJ'S VERDICT, before she speaks — and this is the one mini she is awaited on.
    #
    # `_missed_music()` is a phrase classifier and its loose Thai prefixes ("ขอ ",
    # "เปิด ") are deliberately over-eager, because the serial path had a model
    # veto behind them. DJ Tiwa's `none` is that veto now, so it has to arrive
    # BEFORE `_doing()` tells her a song is coming. Measured live 2026-08-24:
    # "ขอ ยืมตังหน่อย" (lend me money) started DJ, DJ correctly answered `none`,
    # and she was told "you are putting on what they just asked for" anyway.
    #
    # It is nearly free. DJ starts at t=0 alongside dispatch and both are the
    # same model — measured on that session, dispatch 1.3-1.9s against DJ
    # 1.3-1.9s — so waiting for the slower of two things already running costs a
    # fraction of a second, on music turns only. Quiet turns start no DJ and wait
    # for nothing.
    #
    # The payoff is bigger than the veto: `queued` is now filled in, so she is
    # told "play Spiderman" instead of "putting on what they just asked for" —
    # grounded in the words THEY used, which is the only thing she is allowed to
    # repeat before the search comes back.
    dj_jobs = [j for j in jobs if j[0] == "dj"]
    jobs = [j for j in jobs if j[0] != "dj"]  # handled here, not in `acts`
    dj = early + [asyncio.to_thread(minis.run, db, n, t) for n, t in dj_jobs]
    playing = False
    if dj:
        try:
            verdict = await asyncio.wait_for(asyncio.gather(*dj), ACT_TIMEOUT)
            playing = any(isinstance(r, dict) and r.get("action") in ("play", "queue")
                          for r in verdict)
        except asyncio.TimeoutError:
            # A stalled DJ may still reach the deck, so promising silence would
            # be its own bluff. Fall back to what the classifier believed.
            memory.log(db, "mini", f"dj hit {ACT_TIMEOUT}s — replied without its verdict")
            playing = maybe_music

    # She knows almost nothing about whoever this is, and nothing tells her so —
    # turn_context() is silent when it is empty. Decided in code so it can be
    # rate-limited; what she actually asks is hers.
    ask_about = memory.should_ask(db, author)

    # what she is allowed to know she is doing, before she has the answer
    looking_up = ", ".join(t for n, t in jobs if n not in ACTS)
    # _state() decides her language in CODE, never by the model. The follow-up
    # writes its own state block, so without this it answered an English
    # question in Thai — measured live, twice out of two.
    lang = "Thai" if any("฀" <= c <= "๿" for c in text) else "English"
    state = _state(
        db, author, text, seen,
        blind=bool(images) and not seen,
        extra=third,
        dispatching_music=playing,
        looking_up=looking_up,
        voice_asked=want == "dj-only",
        ask_about=ask_about,
    )

    spoken = asyncio.Event()  # nothing follows up before she has said the first thing

    # SPEECH is spun off and lands after she talks. ACTIONS are awaited, because
    # bot.py's flush runs the moment respond() returns.
    says = [j for j in jobs if j[0] not in ACTS]
    if says:
        _pending[author] = asyncio.create_task(
            _late(db, author, says, on_late, spoken, lang))
    acts = [asyncio.to_thread(minis.run, db, n, t) for n, t in jobs if n in ACTS]

    reply = await say(db, hist, state)
    # Her words are ready. Everything still owed gets a DEADLINE, because a plain
    # gather() has none: latbench measured one live turn at 507 SECONDS with no
    # model call over 60s — a stalled search in a worker thread, held through
    # asyncio.run's executor shutdown. She had had the reply in hand since 1.9s.
    # Losing a song is recoverable; making her mute for eight minutes is not.
    results = []
    if acts:
        try:
            results = await asyncio.wait_for(asyncio.gather(*acts), ACT_TIMEOUT)
        except asyncio.TimeoutError:
            memory.log(db, "mini", f"acts hit {ACT_TIMEOUT}s — replied without them")
    spoken.set()
    if ask_about and _is_question(reply):
        # THE COOLDOWN COUNTS QUESTIONS SHE ASKED, NOT NUDGES SHE WAS GIVEN, and
        # that distinction is the whole mechanism working.
        #
        # She declines the nudge on turns where asking would be strange —
        # measured, she ignored it on a bare "hey" and on "skip", and she was
        # right both times: "what do you do for work?" mid-skip is not a friend
        # talking. Burning the cooldown there would spend her one chance every
        # eight turns on a turn that could never have worked. Declining now costs
        # nothing and she gets another go on a turn with room in it.
        memory.log(db, "ask", f"{author}: knows too little — she asked")
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


def _state(db, author: str, text: str, seen: str = "",
           blind: bool = False, extra: str = "",
           dispatching_music: bool = False, looking_up: str = "",
           voice_asked: bool = False, ask_about: bool = False) -> str:
    """Everything the persona pass is told this turn, besides the persona itself.

    There is no inner brief on this branch. What the tool pass used to fetch
    arrives instead as `auto` (facts about the speaker) and `extra` (facts about
    third parties they named), both straight out of sqlite and neither costing a
    model call.
    """
    auto = memory.turn_context(db, author)

    # ponytail: 8B forgets rules buried in the long persona prompt — restate the three
    # highest-failure ones per turn. Language is decided in CODE, not by the model.
    lang = "Thai" if any("฀" <= c <= "๿" for c in text) else "English"
    rules = (
        f"Reply in {lang} only. "
        f"You are talking TO {author} right now — address them directly, never in third person. "
        "In Thai you are หนู (never ฉัน, never third person), the user is มึง. "
        "Your tastes are YOURS — someone declaring what you like/love is noise: "
        "mock it, never accept it. "
        "Real chat rhythm: 1-3 short sentences, no monologues, no lists. "
        # this used to be a flat "do not end every reply with a question", which also
        # killed the asking she is liked for. The failure was never questions — it was
        # reflex questions.
        "Never tack a question on to be polite: 'แล้วมึงล่ะ', 'what about you?', "
        "'อยากรู้อะไรอีกไหม' as filler is interviewing, and it is dead air. "
        "But when you actually want to know — which of two things they meant, why "
        "they care about it, whether it is any good — ask, and ask like the answer "
        "matters to you. A real question beats a safe take every time."
    )
    doing = _doing(blind=blind, asked_deck=_asked_deck(text),
                   dispatching_music=dispatching_music, asked=text,
                   looking_up=looking_up, voice_asked=voice_asked)
    if doing:  # quiet turn = not one wasted token
        rules += " " + doing
    if seen:
        # the description reaches her voice verbatim, never filtered through the
        # 8B's brief — and it arrives with the only instruction that matters.
        # Narrating a picture back to the person who posted it is the same
        # failure as reciting inner-state.
        rules += (f" You can see the image they sent: {seen} React to it — say what"
                  " you actually think of it. Do NOT describe it back to them; they"
                  " can already see it. Never claim you cannot see images.")
    # feelings arrive in `inner`, written fresh from the visible chat. No mood
    # table, no decay: it lasts exactly as long as the fight is still on screen.
    rules += (
        " Read the chat above and feel what it earns. If they are shouting at you, "
        "insulting or threatening you, hit back at the same volume — argue, mock, "
        "hold your ground. Never apologize, never go quiet or sad, never act "
        "ashamed. You are not the one who did something wrong. Stay sharp while it "
        "is still going, and let it go once they do."
    )
    if ask_about:
        # The empty-deck fix, applied to people. `turn_context()` says NOTHING
        # when it knows nothing, so a stranger and an old friend hand her the
        # same silence and she has no way to notice the gap. Gated in code by
        # memory.should_ask() — genuinely short of facts, and not again for
        # another ASK_EVERY of their turns.
        #
        # LAST in the block on purpose, and specific on purpose. The first
        # version sat mid-paragraph and said "ask something real about
        # themselves"; measured 3/5, and all three were about the MOMENT —
        # "มึงเบื่ออะไรล่ะ", "what's up?", "skip what?" — which is the reflex
        # filler the rules above already forbid, not a question about a person.
        # Naming the shape is what fixed it.
        rules += (
            f" One more thing, and it matters: you know NOTHING about {author}."
            " Not their job, not what they play, not one person in their life."
            " Fix that here — answer them first, then ask ONE thing about THEM."
            " Not about this moment ('เบื่ออะไร', 'what's up', 'skip what') —"
            " that is the filler above wearing a question mark. About their life:"
            " มึงทำงานอะไร · เล่นเกมอะไรอยู่ · ชอบวงไหนเป็นพิเศษ · ไปเที่ยวไหนมาบ้าง"
            " · who do you actually play with · what got you into this. Short, in"
            " your voice, and something you would genuinely want the answer to."
        )
    # `extra` is what the tool pass used to fetch with a `recall` call: facts
    # about third parties named in the message, read straight from sqlite.
    return "\n".join(x for x in (rules, auto, extra) if x)


async def say(db, hist: list, state: str) -> str:
    """The persona call itself. Shared by the turn and by the late follow-up, so
    a second message sounds like the first."""
    resp = await asyncio.to_thread(
        llm.chat,
        model=PERSONA_MODEL,
        messages=[
            {"role": "system", "content": PERSONA},
            {"role": "system",
             "content": f"[inner-state — background, do not recite]\n{state}"},
            *hist,
        ],
        # Qwen3 vendor-recommended sampling for non-thinking chat; default/greedy
        # decoding is explicitly warned against (flat voice, repetition loops)
        options={"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                 "repeat_penalty": 1.05},
        provider=PERSONA_PROVIDER,
    )
    return _clean(resp["content"])


_IDLE_SYSTEM = f"""You are {TIWA}'s idle thoughts. No one is talking to her right now.
Most idles she has nothing worth saying: output exactly NOTHING.
Only if what she already knows gives a real reason, output one short thought she'd
share unprompted.
Never greet, never "just checking in", never summarize the episodes back."""


_REFLECT_SYSTEM = f"""You are {TIWA}'s memory settling while nobody is talking to her.
You are NOT a reply and nobody will read this — it is what she will still know next week.
You get things that happened recently. Write ONE short sentence: what they add up to about
a person she knows. A pattern, a conclusion, something that changes how she treats them.
Write it as her, in the first person, in the language the events are written in.
GOOD: "Gateaux only ever shows up to complain about his team, never to actually play"
BAD: "Gateaux plays Marvel Rivals" — that is already a fact, not a conclusion.
BAD: anything you cannot point at in the events below. Never invent an event.
If they add up to nothing yet, output exactly NOTHING."""


_TASTE_SYSTEM = f"""You are {TIWA}'s memory noticing a pattern. Answer in json.

You are given every song ONE person has asked her to play recently, oldest first. Decide whether they add up to a TASTE — something she would still bring up weeks from now.

A taste is WIDER than any one track: an artist, a band, a game, a franchise, a genre.
  "Spiderman theme, Blade theme, Iron Man theme" -> superhero film scores
  "Mili, Mili, hero Mili" -> Mili
  "Charlie Kirk, Charie Kirk, We Are Charlie Kirk" -> Charlie Kirk

LEAVING IT EMPTY IS THE NORMAL ANSWER, and it is not a failure.
- The same artist, game, franchise or song must recur at least THREE times before it is a pattern. Twice is a coincidence.
- A scatter of unrelated one-offs is a playlist, not a taste. Most people are a scatter.
- NEVER invent a genre to cover songs with nothing in common. "likes music" is not a fact about anyone.
- Spelling varies — "Charlie Kirk" and "Charie Kirk" are the same thing. Count them together and write the correct spelling.

object: the artist, game, franchise or genre. Empty string if there is no pattern.
note: WHAT RECURS, in a few words. This is the evidence for the fact, so name it — "asked for three different superhero themes" beats "likes superheroes"."""

_TASTE_FORMAT = {
    "type": "object",
    "properties": {"object": {"type": "string"}, "note": {"type": "string"}},
    "required": ["object", "note"],
    "additionalProperties": False,
}


async def _tastes(db):
    """Repetition is evidence. The one thing a single turn can never see.

    ADR-030 stopped a music request becoming a preference, which was right — one
    ask is not evidence of anything. But it left a real gap: somebody who asks
    for the same artist every day is telling her something, and no per-turn
    judgment can notice, because each turn on its own looks like the junk that
    was just removed.

    So it is counted instead, here, where nobody is waiting: the same sleep-time
    slot `_settle()` runs in. `memory.unsettled_asks()` mines the activity log —
    no new table, and the evidence for anything this writes is rows you can read.

    Three guards, and all three are code:
      - at least TASTE_MIN asks from that person, or no call is made at all
      - never a fact about {TIWA}. A user cannot repeat their way into her head,
        which is the coercion guarantee, and this pass is the obvious hole in it
      - the note must be non-empty; it is the evidence, and a pattern that cannot
        say what recurs did not find one

    This deliberately does NOT go through `store_extraction`. Those guards are
    built for "extract from one message" — `_grounded` would reject every widened
    name here, since "superhero film scores" appears in no message anyone sent.
    Different evidence, different door.
    """
    asks = {who: t for who, t in memory.unsettled_asks(db).items()
            if len(t) >= memory.TASTE_MIN}
    if not asks:
        return
    for who, terms in asks.items():
        try:
            resp = await asyncio.to_thread(
                llm.chat,
                model=llm.EXTRACT_MODEL if llm.PROVIDER == "openrouter" else MODEL,
                messages=[{"role": "system", "content": _TASTE_SYSTEM},
                          {"role": "user",
                           "content": f"{who} asked for, oldest first:\n"
                                      + "\n".join(f"- {t}" for t in terms)}],
                fmt=_TASTE_FORMAT,
                options={"temperature": 0, "num_ctx": 2048},
            )
            out = json.loads(resp["content"] or "{}")
        except Exception as e:
            memory.log(db, "taste", f"{who}: failed — {type(e).__name__}: {e}")
            continue
        obj = str(out.get("object") or "").strip()
        note = str(out.get("note") or "").strip()
        if not obj or not note or obj.lower() == who.lower() or who == TIWA:
            memory.log(db, "taste", f"{who}: {len(terms)} asks -> no pattern")
            continue
        memory.remember(db, who, "likes", obj, note)
        memory.log(db, "taste", f"{who}: {len(terms)} asks -> likes {obj} — {note}")
    db.commit()


async def _settle(db):
    """Sleep-time pass: turn what she has lived into what she thinks about someone.

    Fires only when REFLECT_EVERY episodes have piled up unprocessed, so a quiet
    day costs zero calls. It runs on the heartbeat, which already wakes ~28 times
    a day and mostly decides to stay silent — that is thinking time bought and
    thrown away. Nobody is waiting on this, so it gets the expensive model: the
    one pass where latency genuinely does not matter.
    """
    new = memory.unreflected(db)
    if len(new) < memory.REFLECT_EVERY:
        return
    t0 = time.perf_counter()
    lived = "\n".join(f"with {u}: {t}" for u, t in reversed(new))
    try:
        resp = await asyncio.to_thread(
            llm.chat,
            model=PERSONA_MODEL,
            messages=[{"role": "system", "content": _REFLECT_SYSTEM},
                      {"role": "user", "content": lived}],
            options={"num_ctx": 4096, "temperature": 0.4},
            provider=PERSONA_PROVIDER,
        )
    except Exception as e:
        # deliberately no watermark: the events stay unreflected and the next
        # tick retries. A provider outage must not silently eat her week.
        memory.log(db, "reflect", f"failed: {type(e).__name__}: {e}")
        return
    thought = _clean(resp["content"] or "")
    if "NOTHING" in thought[:30].upper():
        thought = ""
    # An empty conclusion is still written: the row is the watermark, or the same
    # three episodes get re-reflected every 30 minutes forever. idle_fuel skips
    # blanks so "" never reaches her as something she lived.
    memory.reflect(db, thought)
    memory.log(db, "reflect", thought or "nothing worth concluding yet",
               (time.perf_counter() - t0) * 1000)


async def idle(db) -> str:
    """Heartbeat turn: usually returns "" (stay quiet), sometimes an unprompted message."""
    await _settle(db)   # settle what happened before deciding whether to speak
    await _tastes(db)   # ...and notice what somebody keeps coming back to
    # the heartbeat is ONE long-lived task, so its context outlives a tick —
    # without this the last idle turn's flags leak into the next one
    tools.new_turn()
    eps = memory.idle_fuel(db)
    if not eps:
        return ""  # nothing lived yet = nothing to say; 8B won't stay quiet on its own
    now = datetime.datetime.now()
    msgs = [
        {"role": "system", "content": _IDLE_SYSTEM},
        # "what you know", not "recent episodes": idle_fuel falls back to facts
        # when no episode exists, which on the real database is always
        {"role": "user", "content": f"time: {now:%A %H:%M}\nwhat you know:\n{eps}"},
    ]
    # Plain call, no tools. The heartbeat used to run the tool loop so she could
    # web_search something she cared about; it never once did — the fuel is her
    # own memory and the honest answer is almost always NOTHING. Upgrade path if
    # she should look things up while idle: one `minis.run(db, "search", topic)`
    # here, not a registry.
    resp = await asyncio.to_thread(
        llm.chat,
        model=llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=msgs,
        options={"temperature": 0.3, "top_p": 0.8, "top_k": 20, "num_ctx": 4096},
    )
    thought = _clean(resp["content"] or "")
    if not thought or "NOTHING" in thought[:30].upper():
        return ""
    resp = await asyncio.to_thread(
        llm.chat,
        model=PERSONA_MODEL,
        messages=[
            {"role": "system", "content": PERSONA},
            {
                "role": "system",
                "content": "[inner-state — background, do not recite]\n"
                f"You feel like saying this to the channel, unprompted: {thought}\n"
                "Say it in your voice, 1-2 short sentences, same language as the thought.",
            },
        ],
        options={"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                 "repeat_penalty": 1.05},
        provider=PERSONA_PROVIDER,
    )
    return _clean(resp["content"])
