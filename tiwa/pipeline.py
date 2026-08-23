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
Measured after: p50 2695ms against serial's 5059ms, routing 93.7% against 70.3%.
"""
import asyncio
import datetime
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


# ponytail: precision over recall. A false positive plays a song nobody asked
# for; a false negative is just today's behaviour. Phrases, not the bare word
# เพลง, which shows up in questions ABOUT music as often as requests for it.
_MUSIC_ASK = ("เปิดเพลง", "ขอเพลง", "อยากฟัง", "อยากได้เพลง", "ฟังเพลง", "หาเพลง",
              "จัดเพลง", "เปิดอะไร", "play some", "play music", "play a song",
              "play something", "put on some", "want to hear", "wanna hear",
              "some music",
              # "เพลงไม่ออกใส่ queue ด้วย" — a queue ask with the verb in the
              # middle, so no prefix in _MUSIC_VERB could ever reach it
              "ใส่คิว", "ใส่ queue", "เข้าคิว", "ลงคิว")

# A bare imperative — "play <title>", "queue <title>" — is the most common ask
# there is, and none of the phrases above match it. Live log, four turns in a
# row: `play ビビデバ - BIBBIDIBA`, `Queue เพลง ビビデバ`, `play tung tung tung
# sahur orchestra` all called no tool, hit no retry, and she claimed she had put
# them on. Anchored to the START of the message, because a bare `play` substring
# also fires on "my dad plays Warframe" and would put on a random song.
# ponytail: prefix match on the raw message. bot.py strips the @mention before
# this sees it, so "@Tiwa play X" works — but "หนู play X" does not. Parse the
# first word properly if leading filler turns out to be common.
_MUSIC_VERB = ("play ", "queue ", "put on ", "เปิดเพลง", "เล่นเพลง", "ต่อคิว",
               "เปลี่ยนเพลง",
               # Thai verb + a title, with no "เพลง" glued on: "ขอ ATLAS-The
               # Score" reached none of the lists above and she claimed she had
               # put it on. The trailing space is what keeps "ขอโทษ" (sorry) and
               # "ขอบคุณ" (thanks) out — Thai does not space its own words, so a
               # space here means a foreign title follows.
               "ขอ ", "เปิด ", "เล่น ", "ต่อ ")

# ...but an imperative is not always a request. "ตอนนี้เปิดเพลงอะไรอยู่" (what
# song is on right now?) matched _MUSIC_ASK, and a random Thai song went on over
# the top of her answer. That is worse than the silence this classifier exists
# to fix.
# Phrases, not the bare word อะไร: "เปิดอะไรก็ได้" (put on anything) is a real ask.
# Split out, because these two do different jobs. _DECK_Q means "they are asking
# ABOUT the deck" — which vetoes the retry AND, when nothing is on, is the one
# moment telling her the deck is empty is worth the tokens.
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
_QUESTION = _DECK_Q + ("?", "อะไรบ้าง", "ไหม", "มั้ย", "ทำไม", "เมื่อไหร่")

# A youtube link always carries "?v=", and "?" is in _QUESTION — so every
# `queue https://www.youtube.com/watch?v=...` was read as a question and the
# retry never fired. Strip links before asking "is this a question?", never
# before the verb check, which needs the trailing space in "queue ".
_URL = re.compile(r"https?://\S+")


def _missed_music(text: str) -> bool:
    """They asked for music, deterministically, for free, in 0ms.

    Named for the job it used to do — catching the ~1 ask in 4-6 the tool pass
    forgot. There is no tool pass now, so it does the whole job: it is what
    starts DJ Tiwa at t=0, and what `route()` uses to overrule a router that
    missed a music ask.

    PENDING_MUSIC == "" means stop_music already fired; leave it.

    ponytail: the loose Thai prefixes in _MUSIC_VERB ("ขอ ", "เปิด ") used to sit
    behind a model veto — _force_music asked "is this really music?" and NONE
    meant drop it. DJ Tiwa's `none` action is that veto now, but it lands AFTER
    _doing() has already told her a song is coming, so a false positive like
    "ขอ ยืมตังหน่อย" can still have her say she put one on. Upgrade path: await
    the DJ decision alongside dispatch (+~0.6s on music turns) and pass the real
    terms to _doing().

    Deliberately does NOT care whether something is already playing. It used to
    skip a busy deck, to stop "เพลงนี้ชื่ออะไร" starting a track over her answer —
    but the phrase list never matched that anyway, and the guard silently disabled
    the retry for "put on a different one", which is the common case. Measured in
    the live log: three confabulated turns in one session, every one of them with
    a song already on.

    It DOES care whether the sentence is a question — that is a different guard,
    and the one this function was missing. See _QUESTION.
    """
    if tools.PENDING_MUSIC is not None or tools.DJ:
        return False
    low = text.lower().strip()
    if any(q in _URL.sub("", low) for q in _QUESTION):
        return False  # asking about music is not asking for music
    return low.startswith(_MUSIC_VERB) or any(k in low for k in _MUSIC_ASK)


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
                          " what is on, just tell them.")
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
    if _asked_deck(text):
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
    asked_music = _missed_music(text)

    # Voice has no mini — 0 calls in 135 logged turns — so the classifier reaches
    # the actuators directly. Returns "" under TIWA_VOICE=dj (the default), where
    # _flush_music brings her in by itself. Wired anyway, so TIWA_VOICE=full is
    # one env var and not a rewrite.
    want = _asked_voice(text)
    if want == "join":
        tools.join_voice(db)
    elif want == "leave":
        tools.leave_voice(db)

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
    # dispatch also returns "ask" — the one thing it would have to ask before
    # acting. Nothing reads it: she is better at asking than the router is, and
    # she has the whole conversation to ask from. It stays in the schema because
    # writing the question is what stops the router guessing instead.
    jobs = []
    try:
        out = await asyncio.wait_for(
            minis.dispatch(db, author, text, recent), DISPATCH_TIMEOUT)
        jobs = route(db, out["dispatch"], text, asked_music,
                     dj_already_running=bool(early))
    except asyncio.TimeoutError:
        # she talks anyway rather than waiting on a stalled router
        memory.log(db, "mini", f"dispatch hit {DISPATCH_TIMEOUT}s — replied without it")

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
        dispatching_music=asked_music or any(n == "dj" for n, _ in jobs),
        looking_up=looking_up,
        voice_asked=want == "dj-only",
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


def _state(db, author: str, text: str, seen: str = "",
           blind: bool = False, extra: str = "",
           dispatching_music: bool = False, looking_up: str = "",
           voice_asked: bool = False) -> str:
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
    await _settle(db)  # settle what happened before deciding whether to speak
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
