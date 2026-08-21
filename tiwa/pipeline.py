"""Two-pass turn: inner reasoning (tool registry) -> persona reply. Phase 3."""
import asyncio
import datetime
import os
import re
import time
from pathlib import Path

from . import eyes, llm, memory, tools
from .memory import MODEL, TIWA

PERSONA = (Path(__file__).parents[1] / "prompts" / "tiwa.md").read_text(encoding="utf-8")

# where her voice runs is decided by TIWA_MODE (see llm._MODES). On the API path
# expect hosted filters to soften her escalation and coercion refusals — that is
# a measured tradeoff (tests/smoke.py), not a free swap.
# One knob, same shape as TIWA_MODE in llm.py: "serial" is the three-pass path
# below, "concurrent" is turn.py. Default serial — the swarm proves itself before
# it becomes the default, and every caller (bot, chat, dashboard, benches) keeps
# calling respond() either way.
TURN_MODE = os.environ.get("TIWA_TURN", "serial")
if TURN_MODE not in ("serial", "concurrent"):
    print(f"[tiwa] TIWA_TURN={TURN_MODE!r} unknown — using 'serial'.")
    TURN_MODE = "serial"

PERSONA_PROVIDER = llm.PERSONA_PROVIDER
PERSONA_MODEL = os.environ.get("TIWA_PERSONA_MODEL") or (
    llm.PERSONA_API_MODEL if PERSONA_PROVIDER == "openrouter" else MODEL
)

_INNER_SYSTEM = f"""You are {TIWA}'s inner thoughts, run before she replies. You are NOT the reply.
The brief is about the LAST message; earlier lines resolve who "he/she/it" means AND show you how this conversation has been going (calm, or someone laying into her).
Tools: recall memory for each person/topic that matters.
If the message ASKS FOR AN ACTION — play or stop music, check the calendar, come into
voice — call that tool. Requests in Thai count exactly the same as English ones.
Wanting music without naming a song is still asking for music: invent the search
terms and call play_music. Writing "putting something on" in this brief plays
NOTHING — only the tool call does. Never say a song is playing unless you called
play_music or queue_music in this turn.
PUTTING something on the calendar is calendar_write. calendar_read only answers "what do
I have on" — it changes nothing. A message that names an event and a time is a WRITE.
If she just proposed a date or time and they agree — "ใช่", "ช่าย", "ครับ", "yes", "ok",
even one word — that agreement IS the go-ahead: call calendar_write NOW, with the whole
event in one sentence, taking the date and time from what she proposed. Writing "noted"
or "added it" in this brief puts NOTHING on the calendar; only calendar_write does, and
Krich still has to confirm it with a reaction, so calling it is never the risky choice.
web_search when the message turns on a fact you do not have: news, a score, a price,
a game or show or person they brought up that you do not recognise. Not recognising
something is a reason to SEARCH, not a reason to hedge. Search keywords, never their
whole sentence, and never the same keywords twice in one turn.
An image counts as bringing something up: if you can see a game, show, product or
place in it that you do not recognise, search for it before she replies.
Then output a short plain-text brief (max 5 lines) addressed to her as "you":
- what you actually know or feel about them (memory or tool results only — NEVER invent),
  e.g. "you remember Steven: Krich's cousin, plays guitar"
- what you have no memory of (so you ask instead of bluffing), e.g. "no memory of Steven — ask"
- the one thing you are genuinely unsure of or want to know more about, when there is
  one: which of two things they meant, why they care, what they think of it. Write it as
  "ask them: ..." on its own line. Only when it is real — a question about something you
  could have just searched, or that you do not actually care about, is worse than none.
- NEVER report a memory gap for a song, artist or track title. She does not need to
  recognise music to play it; the search finds it. Say "putting it on" instead.
Nothing else. No greetings, no reply draft, no lines about the speaker's own knowledge.
Never comment on the mood of the conversation — she reads the chat herself and feels
it better than you do (measured: this pass missed two real attacks and invented a
third; tests/moodbench.py)."""


def _clean(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"<tool_call>.*?(</tool_call>|$)", "", text, flags=re.S)  # 8B leaks these as text
    return re.sub(r"^(Tiwa|ทิวา)\s*:\s*", "", text.strip()).strip()


def _arg_name(args) -> str:
    # ponytail: 8B mangles tool args — nests them ({'object': {'name': X}}) or renames
    # the key. Dig until we hit a string; empty string on anything hopeless.
    while isinstance(args, dict):
        args = args.get("name") or next(iter(args.values()), "")
    return str(args or "")


# Qwen3 warns against greedy decoding (repetition loops) — low temp, not 0
# measured on tests/pickbench.py: 0.1 is bimodal (1/6 then 6/6 across two runs),
# 0.3 holds 4-5 of 6. Lower is NOT steadier here.
_INNER_OPTS = {"temperature": 0.3, "top_p": 0.8, "top_k": 20, "num_ctx": 4096}


async def _tool_chat(db, msgs: list) -> str:
    """Shared tool loop: chat with the registry until the model outputs text."""
    model = llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL
    # SEEN_URLS used to be cleared here; tools.new_turn() owns that boundary now,
    # so a repeat search inside one turn still returns new pages.
    for _ in range(3):  # ponytail: max 3 tool rounds, plenty for one message
        resp = await asyncio.to_thread(
            llm.chat,
            model=model,
            messages=msgs,
            tools=[t["schema"] for t in tools.TOOLS.values()],
            options=_INNER_OPTS,
        )
        msgs.append(resp["raw"])
        if not resp["tool_calls"]:
            return _clean(resp["content"])
        for tc in resp["tool_calls"]:
            t = tools.TOOLS.get(tc["name"])
            arg = _arg_name(tc["args"])
            t0 = time.perf_counter()
            out = (
                # to_thread: web/calendar tools block on network
                await asyncio.to_thread(t["fn"], db, arg)
                if t
                else "unknown tool"  # models sometimes invent tool names
            )
            # blank arg = the model mangled it; that is the metric G2 benches.
            # The panel splits this on "(" and ") -> " to show the tool, the query
            # she chose and what came back as three separate things.
            memory.log(db, "tool", f"{tc['name']}({arg!r}) -> {out[:240]}",
                       (time.perf_counter() - t0) * 1000)
            msgs.append(llm.tool_result_msg(tc, out))
    # tool rounds exhausted — force a brief from what was gathered so far
    resp = await asyncio.to_thread(llm.chat, model=model, messages=msgs, options=_INNER_OPTS)
    return _clean(resp["content"])


async def _inner_brief(db, author: str, text: str, recent: str = "", seen: str = "") -> str:
    prefix = f"earlier lines (context only):\n{recent}\n\n" if recent else ""
    # what she can see goes to the TOOL pass too, not just to her voice: a game or
    # a product in a screenshot is something to look up, same as one they typed.
    sight = f"\n[they attached an image. you can see it:\n{seen}]" if seen else ""
    # she was searching "ราคา RTX 5090 2025" in July 2026 (tests/searchbench.py --live).
    # A model dates itself from its training data unless you tell it otherwise, and a
    # wrong year in a search query is a wrong page back.
    now = datetime.datetime.now()
    msgs = [
        {"role": "system", "content": _INNER_SYSTEM},
        {"role": "user",
         "content": f"today is {now:%Y-%m-%d}\n{prefix}{author}: {text}{sight}"},
    ]
    return await _tool_chat(db, msgs)


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
# song is on right now?) matched _MUSIC_ASK, so _force_music searched that
# literal sentence and played a random Thai song over the top of her answer.
# That is worse than the silence this whole retry exists to fix.
# Phrases, not the bare word อะไร: "เปิดอะไรก็ได้" (put on anything) is a real ask.
# Split out, because these two do different jobs. _DECK_Q means "they are asking
# ABOUT the deck" — which vetoes the retry AND, when nothing is on, is the one
# moment telling her the deck is empty is worth the tokens.
# Phrases, never the bare word อะไร — "เปิดเพลงอะไรก็ได้" (put on anything) is a
# real ask, and a looser "เพลงอะไร" swallows it. djbench catches that one.
_DECK_Q = ("อะไรอยู่", "ชื่ออะไร", "เล่นเพลงไร", "เพลงไรอยู่",
           "what song", "which song", "what's playing", "what is playing",
           "what are you playing")
_QUESTION = _DECK_Q + ("?", "อะไรบ้าง", "ไหม", "มั้ย", "ทำไม", "เมื่อไหร่")

# A youtube link always carries "?v=", and "?" is in _QUESTION — so every
# `queue https://www.youtube.com/watch?v=...` was read as a question and the
# retry never fired. Strip links before asking "is this a question?", never
# before the verb check, which needs the trailing space in "queue ".
_URL = re.compile(r"https?://\S+")

_TERMS_SYSTEM = (
    "Turn this request into YouTube search terms for music. Output ONLY the terms, "
    "2-6 words, no quotes, no explanation. "
    "If they NAMED a song, output that name plus at most the artist or game it is "
    "from, and nothing else — a descriptive word they did not say ('chase', 'hype', "
    "'sad') finds a different song. Measured: 'Red Line' from Warframe became "
    "'Red Line Warframe chase' and played the wrong track. "
    "Only when they named no song at all — just a mood, a genre, a game or an "
    "activity — invent terms that fit it. "
    "If the message is NOT asking for music at all, output exactly NONE. "
    "'ขอ ยืมตังหน่อย' (lend me money) is NONE. 'เปิด ประตู' (open the door) is NONE."
)


def _missed_music(text: str) -> bool:
    """They asked for music and the tool pass called nothing — about 1 ask in 4-6.

    PENDING_MUSIC == "" means stop_music fired; that IS a music tool, leave it.

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


async def _force_music(db, text: str) -> None:
    """Ask for search terms as plain text rather than retrying the tool call: a
    model that just declined to call a tool declines again often enough, but it
    always answers a question. One extra call, only on turns that missed.
    """
    resp = await asyncio.to_thread(
        llm.chat,
        model=llm.TOOL_MODEL if llm.PROVIDER == "openrouter" else MODEL,
        messages=[{"role": "system", "content": _TERMS_SYSTEM},
                  {"role": "user", "content": text}],
        options={"temperature": 0.3, "num_ctx": 1024},
    )
    terms = _terms(resp["content"])
    # The veto. _MUSIC_VERB now matches bare Thai verbs ("ขอ ", "เปิด ") to catch
    # "ขอ ATLAS-The Score", and those also start "ขอ ยืมตังหน่อย". Playing a random
    # song over an unrelated message is worse than the silence this retry exists
    # to fix, so the model that reads the sentence gets the last word.
    if not terms or terms.strip(" .").upper() == "NONE":
        memory.log(db, "tool", f"play_music -> not a music ask after all: {text[:60]!r}")
        return
    tools.play_music(db, terms)
    memory.log(db, "tool", f"play_music({terms!r}) -> forced, the tool pass skipped it")


def _asked_deck(text: str) -> bool:
    """They asked what is on. The one moment an empty deck is worth telling her."""
    return any(q in text.lower() for q in _DECK_Q)


# Straight out of the join_voice / leave_voice tool descriptions. On the
# concurrent path there is no tool pass, and no mini owns voice — 0 calls in 135
# logged, so an LLM round trip for it would be absurd. But losing it SILENTLY is
# the failure this codebase minds most, so it becomes a classifier instead.
# ponytail: phrase match, precision over recall, same stance as _MUSIC_ASK. A
# miss means you type `join`; a false positive drags her into a live call.
_JOIN = ("come join the vc", "join the vc", "join vc", "get in here", "hop in",
         "เข้ามา", "เข้าห้อง", "เข้ามาหน่อย", "เข้าวอย")
# "wanting the MUSIC to stop is NOT wanting you gone" — the leave_voice
# description says so, and stop_music shares no phrase with any of these.
_LEAVE = ("leave the vc", "leave vc", "get out", "ออกไป", "ออกห้อง",
          "ออกจากห้องเสียง", "ไปได้แล้ว")


def _asked_voice(text: str) -> str:
    """"join", "leave" or "". Vetoed by voice.wants_now() for a FUTURE time —
    'join us later tonight' is a plan, not an ask, and that veto is measured."""
    low = text.lower()
    want = ("leave" if any(p in low for p in _LEAVE)
            else "join" if any(p in low for p in _JOIN) else "")
    if not want:
        return ""  # the common case never imports voice — see below
    # `voice` pulls in discord, whisper and onnxruntime, ~0.24s the first time.
    # Importing it per turn to run a pure-text veto put that on EVERY turn for
    # chat.py, the dashboard and the benches (forkbench caught it: 0.54s for two
    # 0.3s calls). It loads only when a phrase actually matched — 0 times in 135.
    from . import voice

    return want if voice.wants_now(text) else ""


def _doing(missed_music: bool = False, blind: bool = False,
           asked_deck: bool = False, dispatching_music: bool = False) -> str:
    """What she is actually doing, read from live state — never from what the
    model believes it did.

    She used to need a now_playing tool call to learn her own deck, and would
    otherwise say "เปิดละ" with nothing queued (three turns running, measured).
    Both are the same bug: her actions were not in her context. One `if` per
    subsystem — when she gains a new one (lights, timers), add a line HERE.

    Doing nothing says NOTHING. Returns "" on a quiet turn: a standing "no music
    is playing" is context she pays for on every ordinary message and uses on
    almost none. The only negatives kept are the anti-confabulation guards, and
    both are narrow — `missed_music` fires when she was asked for a song and has
    none, `blind` when an image arrived and the vision call came back empty.
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
        # On the concurrent path DJ Tiwa has not run yet — she is speaking WHILE
        # it searches — so there is nothing in the Turn to name. The guarantee is
        # the same either way and it is the second half of this text that carries
        # it: she has not seen a result, so she invents no artist and no title.
        what = (", ".join(f"{act} {arg}".strip() for act, arg in queued)
                if queued else "putting a song on")
        out.append(f"You have just done this: {what}. It IS happening — say so in"
                   " your own way. Never say you do not know the song or cannot"
                   " find it; you do not need to recognise a song to put it on."
                   " Do not sing or quote its lyrics. You have not seen the search"
                   " result yet, so do NOT name an artist, album or year for it —"
                   " those are things you would be making up.")
    elif missed_music:
        # asked for music, and _force_music came up empty too — the only turn
        # where telling her what is NOT happening is worth the tokens
        out.append("They asked for music but the search came up empty and nothing"
                   " is queued — do NOT say you are putting a song on. Say it did"
                   " not work.")
    if blind:
        # same shape as the music guard: the picture is right there in the channel,
        # so bluffing about it is caught instantly. Cheaper to admit it.
        out.append("They sent an image and your eyes did not work this time — you"
                   " genuinely cannot see it. Do NOT guess what is in it or act like"
                   " you looked. Say you cannot see it.")
    if tools.PENDING_LEAVE:
        out.append("You are leaving the voice channel as you say this.")
    return " ".join(out)


async def respond(db, hist: list, author: str, text: str, images=(),
                  on_late=None) -> str:
    """One Tiwa turn. `hist` = chat messages incl. the current one. Caller appends the reply.

    `images` = attachment urls on the current message. Empty on every ordinary
    turn, and an empty list costs exactly nothing — no vision call is made.
    """
    if TURN_MODE == "concurrent":
        from . import turn  # late: turn.py imports this module

        return await turn.respond(db, hist, author, text, images, on_late)
    # `on_late` is ignored on the serial path: nothing there finishes after she
    # speaks, which is the whole difference between the two.
    turn0 = time.perf_counter()
    tools.new_turn()  # everything the tools flag this turn is scoped to this task
    recent = "\n".join(
        m["content"] if m["role"] == "user" else f"{TIWA}: {m['content']}"
        # lines before the current message: resolve "he/she", and show whether a
        # fight is still on screen. This window IS the lifetime of her mood.
        for m in hist[-9:-1]
    )
    # before the brief, so the tool pass can look up whatever she saw
    seen = await asyncio.to_thread(eyes.look, db, images, text) if images else ""
    inner = await _inner_brief(db, author, text, recent, seen)
    missed = _missed_music(text)
    if missed:
        await _force_music(db, text)

    state = _state(db, author, text, seen, inner, missed,
                   blind=bool(images) and not seen)
    reply = await say(db, hist, state)
    memory.log(db, "turn", f"{author}: {text[:100]} -> {reply[:120]}",
               (time.perf_counter() - turn0) * 1000)
    return reply


def _state(db, author: str, text: str, seen: str = "", inner: str = "",
           missed: bool = False, blind: bool = False, extra: str = "",
           dispatching_music: bool = False) -> str:
    """Everything the persona pass is told this turn, besides the persona itself.

    Extracted so turn.py's concurrent path builds the SAME block from the same
    code — the whole point of the A/B is that only the timing differs. `inner` is
    the tool pass's brief on the serial path and "" on the concurrent one, where
    the same ground arrives as `auto` + `extra` without a model call.
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
    doing = _doing(missed, blind=blind, asked_deck=_asked_deck(text),
                   dispatching_music=dispatching_music)
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
    # `extra` is the concurrent path's replacement for what the tool pass used to
    # fetch: facts about third parties named in the message, read from sqlite.
    return "\n".join(x for x in (rules, auto, extra, inner) if x)


async def say(db, hist: list, state: str) -> str:
    """The persona call itself. Shared so both paths speak with identical settings."""
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
Only if recent memory (or a web_search about something she genuinely cares about)
gives a real reason, output one short thought she'd share unprompted.
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
    thought = await _tool_chat(db, msgs)
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
