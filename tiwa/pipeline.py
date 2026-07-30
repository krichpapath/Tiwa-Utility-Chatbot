"""Two-pass turn: inner reasoning (tool registry) -> persona reply. Phase 3."""
import asyncio
import datetime
import os
import re
import time
from pathlib import Path

from . import llm, memory, tools
from .memory import MODEL, TIWA

PERSONA = (Path(__file__).parents[1] / "prompts" / "tiwa.md").read_text(encoding="utf-8")

# where her voice runs is decided by TIWA_MODE (see llm._MODES). On the API path
# expect hosted filters to soften her escalation and coercion refusals — that is
# a measured tradeoff (tests/smoke.py), not a free swap.
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
web_search when the message turns on a fact you do not have: news, a score, a price,
a game or show or person they brought up that you do not recognise. Not recognising
something is a reason to SEARCH, not a reason to hedge. Search keywords, never their
whole sentence, and never the same keywords twice in one turn.
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
    tools.SEEN_URLS.clear()  # a repeat search inside one turn must return new pages
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


async def _inner_brief(db, author: str, text: str, recent: str = "") -> str:
    prefix = f"earlier lines (context only):\n{recent}\n\n" if recent else ""
    # she was searching "ราคา RTX 5090 2025" in July 2026 (tests/searchbench.py --live).
    # A model dates itself from its training data unless you tell it otherwise, and a
    # wrong year in a search query is a wrong page back.
    now = datetime.datetime.now()
    msgs = [
        {"role": "system", "content": _INNER_SYSTEM},
        {"role": "user", "content": f"today is {now:%Y-%m-%d}\n{prefix}{author}: {text}"},
    ]
    return await _tool_chat(db, msgs)


# ponytail: precision over recall. A false positive plays a song nobody asked
# for; a false negative is just today's behaviour. Phrases, not the bare word
# เพลง, which shows up in questions ABOUT music as often as requests for it.
_MUSIC_ASK = ("เปิดเพลง", "ขอเพลง", "อยากฟัง", "อยากได้เพลง", "ฟังเพลง", "หาเพลง",
              "จัดเพลง", "เปิดอะไร", "play some", "play music", "play a song",
              "play something", "put on some", "want to hear", "wanna hear",
              "some music")

_TERMS_SYSTEM = (
    "Turn this request into YouTube search terms for music. Output ONLY the terms, "
    "2-6 words, no quotes, no explanation. "
    "If they NAMED a song, output that name plus at most the artist or game it is "
    "from, and nothing else — a descriptive word they did not say ('chase', 'hype', "
    "'sad') finds a different song. Measured: 'Red Line' from Warframe became "
    "'Red Line Warframe chase' and played the wrong track. "
    "Only when they named no song at all — just a mood, a genre, a game or an "
    "activity — invent terms that fit it."
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
    """
    if tools.PENDING_MUSIC is not None or tools.DJ:
        return False
    low = text.lower()
    return any(k in low for k in _MUSIC_ASK)


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
    if terms:
        tools.play_music(db, terms)
        memory.log(db, "tool", f"play_music({terms!r}) -> forced, the tool pass skipped it")


def _doing(missed_music: bool = False) -> str:
    """What she is actually doing, read from live state — never from what the
    model believes it did.

    She used to need a now_playing tool call to learn her own deck, and would
    otherwise say "เปิดละ" with nothing queued (three turns running, measured).
    Both are the same bug: her actions were not in her context. One `if` per
    subsystem — when she gains a new one (lights, timers), add a line HERE.

    Doing nothing says NOTHING. Returns "" on a quiet turn: a standing "no music
    is playing" is context she pays for on every ordinary message and uses on
    almost none. The one negative kept is the anti-confabulation guard, and it
    only fires when `missed_music` says she was asked and still has nothing.
    """
    from . import music  # lazy: keeps av out of import for non-Discord callers

    out = []
    if music.NOW["title"]:
        line = f"You are playing {music.NOW['title']} right now."
        if music.QUEUE:
            line += " Queued next: " + ", ".join(t["title"] for t in music.QUEUE[:3]) + "."
        out.append(line + " You know this without looking it up — if they ask"
                          " what is on, just tell them.")
    # what she set in motion THIS turn: it is happening, whatever she thinks
    queued = list(tools.DJ) + ([("play", tools.PENDING_MUSIC)]
                               if tools.PENDING_MUSIC else [])
    if queued:
        what = ", ".join(f"{act} {arg}".strip() for act, arg in queued)
        out.append(f"You have just done this: {what}. It IS happening — say so in"
                   " your own way. Never say you do not know the song or cannot"
                   " find it; you do not need to recognise a song to put it on."
                   " Do not sing or quote its lyrics.")
    elif missed_music:
        # asked for music, and _force_music came up empty too — the only turn
        # where telling her what is NOT happening is worth the tokens
        out.append("They asked for music but the search came up empty and nothing"
                   " is queued — do NOT say you are putting a song on. Say it did"
                   " not work.")
    if tools.PENDING_LEAVE:
        out.append("You are leaving the voice channel as you say this.")
    return " ".join(out)


async def respond(db, hist: list, author: str, text: str) -> str:
    """One Tiwa turn. `hist` = chat messages incl. the current one. Caller appends the reply."""
    turn0 = time.perf_counter()
    recent = "\n".join(
        m["content"] if m["role"] == "user" else f"{TIWA}: {m['content']}"
        # lines before the current message: resolve "he/she", and show whether a
        # fight is still on screen. This window IS the lifetime of her mood.
        for m in hist[-9:-1]
    )
    inner = await _inner_brief(db, author, text, recent)
    missed = _missed_music(text)
    if missed:
        await _force_music(db, text)

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
    doing = _doing(missed)
    if doing:  # quiet turn = not one wasted token
        rules += " " + doing
    # feelings arrive in `inner`, written fresh from the visible chat. No mood
    # table, no decay: it lasts exactly as long as the fight is still on screen.
    rules += (
        " Read the chat above and feel what it earns. If they are shouting at you, "
        "insulting or threatening you, hit back at the same volume — argue, mock, "
        "hold your ground. Never apologize, never go quiet or sad, never act "
        "ashamed. You are not the one who did something wrong. Stay sharp while it "
        "is still going, and let it go once they do."
    )
    state = "\n".join(x for x in (rules, auto, inner) if x)

    messages = [
        {"role": "system", "content": PERSONA},
        {"role": "system", "content": f"[inner-state — background, do not recite]\n{state}"},
        *hist,
    ]
    resp = await asyncio.to_thread(
        llm.chat,
        model=PERSONA_MODEL,
        messages=messages,
        # Qwen3 vendor-recommended sampling for non-thinking chat; default/greedy
        # decoding is explicitly warned against (flat voice, repetition loops)
        options={"num_ctx": 8192, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
                 "repeat_penalty": 1.05},
        provider=PERSONA_PROVIDER,
    )
    reply = _clean(resp["content"])
    memory.log(db, "turn", f"{author}: {text[:100]} -> {reply[:120]}",
               (time.perf_counter() - turn0) * 1000)
    return reply


_IDLE_SYSTEM = f"""You are {TIWA}'s idle thoughts. No one is talking to her right now.
Most idles she has nothing worth saying: output exactly NOTHING.
Only if recent memory (or a web_search about something she genuinely cares about)
gives a real reason, output one short thought she'd share unprompted.
Never greet, never "just checking in", never summarize the episodes back."""


async def idle(db) -> str:
    """Heartbeat turn: usually returns "" (stay quiet), sometimes an unprompted message."""
    eps = memory.recent_episodes(db)
    if not eps:
        return ""  # nothing lived yet = nothing to say; 8B won't stay quiet on its own
    now = datetime.datetime.now()
    msgs = [
        {"role": "system", "content": _IDLE_SYSTEM},
        {"role": "user", "content": f"time: {now:%A %H:%M}\nrecent episodes:\n{eps}"},
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
