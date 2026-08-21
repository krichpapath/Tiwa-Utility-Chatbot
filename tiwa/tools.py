"""Tool registry for the inner pass. Every tool: fn(db, arg: str) -> str.

Every tool takes ONE string argument named "name" — deliberate: the 8B mangles
nested/renamed structured args (see pipeline._arg_name), so structure is minted
later by schema-constrained calls, never by the tool-calling model.
"""
import contextvars
import dataclasses
import os
import sys
import types

from . import memory
from .memory import TIWA

# Read here rather than importing voice, which pulls in discord, whisper and
# onnxruntime. Same env var, same meaning — voice.DJ_ONLY is the definition.
VOICE_DJ_ONLY = os.environ.get("TIWA_VOICE", "dj") != "full"
# Honest text, not silence: she is told the channel is for music, so she says so
# in her own words instead of claiming she joined and doing nothing.
_DJ_ONLY_NOTE = ("you do not do voice chat right now — the voice channel is only "
                 "for playing music. Say so; do not claim you joined or left.")

TOOLS = {}  # name -> {"schema": ollama tool spec, "fn": callable(db, arg) -> str}


@dataclasses.dataclass
class Turn:
    """What one turn asked for. These six were module globals until 2026-08-21.

    Field names are the old global names on purpose — see the module shim below.
    """

    DJ: list = dataclasses.field(default_factory=list)
    PENDING_MUSIC: "str | None" = None
    PENDING_CALENDAR: list = dataclasses.field(default_factory=list)
    PENDING_JOIN: bool = False
    PENDING_LEAVE: bool = False
    SEEN_URLS: set = dataclasses.field(default_factory=set)


# A ContextVar, not a global and not threading.local: asyncio copies the context
# into every new Task, and asyncio.to_thread carries it across the thread
# boundary — which is exactly the path a tool takes (_tool_chat runs each tool in
# a thread). discord.py dispatches every message as its own Task, so two channels
# talking at once get two Turns for free. A plain global gave them one, and
# `locks[channel.id]` only ever serialized WITHIN a channel.
_TURN = contextvars.ContextVar("tiwa_turn")


def current() -> Turn:
    """The turn in flight. Creates one if nobody called new_turn() — benches and
    `python -m tiwa.tools` poke tools directly and should keep working."""
    try:
        return _TURN.get()
    except LookupError:
        return new_turn()


def new_turn() -> Turn:
    """Start a fresh turn. Called by pipeline.respond() and pipeline.idle()."""
    t = Turn()
    _TURN.set(t)
    return t


# ponytail: the six names above stay readable and writable as `tools.PENDING_MUSIC`
# so bot.py, chat.py, dashboard.py and seven benches did not have to change — which
# is what makes "no behaviour changed" checkable by running them unmodified.
# Module-level code inside THIS file writes f_globals directly and never reaches
# here, so tools below must say current().X explicitly. Upgrade path: when the
# minis land and every caller says current() anyway, delete this class and the
# aliases with it.
_PER_TURN = {f.name for f in dataclasses.fields(Turn)}


class _TurnScoped(types.ModuleType):
    def __getattr__(self, name):
        if name in _PER_TURN:
            return getattr(current(), name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in _PER_TURN:
            setattr(current(), name, value)
        else:
            super().__setattr__(name, value)


sys.modules[__name__].__class__ = _TurnScoped


def tool(description: str, arg_desc: str):
    def reg(fn):
        TOOLS[fn.__name__] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": arg_desc}
                        },
                        "required": ["name"],
                    },
                },
            },
            "fn": fn,
        }
        return fn

    return reg


@tool(
    f"Check {TIWA}'s memory for a person, character, or topic. "
    "Returns known facts or 'no memory'. Use for every name/topic that matters.",
    "who or what to recall",
)
def recall(db, arg: str) -> str:
    return memory.lookup(db, arg)


@tool(
    "Search the web. Use it whenever the answer depends on something you cannot "
    "know from memory: news, scores, prices, a game or show or person they "
    "brought up that you have never heard of. NOT knowing is a reason to search, "
    "not a reason to guess or to say you are not sure. Never for people you "
    "should just recall. "
    "Pass KEYWORDS, never the sentence they typed — strip 'what is', 'do you "
    "know', 'มึงรู้ไหมว่า', 'อยากรู้ว่า'. Keep names and numbers, and add the year "
    "for anything current: "
    "'มึงรู้ไหมว่าใครชนะบอลเมื่อคืน' -> 'ผลบอลเมื่อคืน'; "
    "'what's that new gojo thing everyone's on about' -> 'Jujutsu Kaisen new season 2026'; "
    "'is the new iphone any good' -> 'iPhone review 2026'; "
    "'เห็นเขาบอกว่าร้านนี้ดี จริงไหม' -> 'รีวิว ร้าน[ชื่อร้าน]'. "
    "Searching the same words twice returns the same page. If a search missed, "
    "CHANGE the words — different angle, a name instead of a description, add a "
    "year — never repeat the same query.",
    "search keywords, not the user's sentence",
)
def web_search(db, arg: str) -> str:
    from urllib.parse import urlsplit  # stdlib: domain is the only source signal she gets

    from ddgs import DDGS  # lazy: keeps dep optional for tests

    # region decides the index. Measured on 'ผลบอลพรีเมียร์ลีก': us-en (the ddgs
    # default) returns pinterest, youtube and a blogspot; th-th returns thairath,
    # trueid and kapook. Same query, one keyword argument apart.
    region = "th-th" if any("฀" <= c <= "๿" for c in arg) else "us-en"
    try:
        hits = DDGS().text(arg, region=region, max_results=8)
    except Exception as e:  # network flake -> brief says so instead of crashing the turn
        return f"search failed: {e}"
    # ponytail: dedupe by url within the turn only. Across turns she is allowed to
    # find the same page again — that is a fresh question, not a repeat.
    seen = current().SEEN_URLS
    fresh = [h for h in hits if h["href"] not in seen]
    seen.update(h["href"] for h in fresh)
    if not fresh:
        return ("every result was one you already saw this turn — these keywords are "
                "spent, search something different or answer with what you have")
    return "\n".join(
        f"{h['title']} [{urlsplit(h['href']).netloc}]: {h['body']}" for h in fresh[:5]
    ) or "no results"


@tool(
    "Read Krich's Google Calendar (next 7 days). Use only when the message "
    "involves his schedule or plans.",
    "ignored",
)
def calendar_read(db, arg: str) -> str:
    from . import gcal

    return gcal.upcoming()


@tool(
    "Join the voice channel the speaker is sitting in, RIGHT NOW. Call it ONLY if "
    "they want you in there this second: 'come join the vc', 'get in here'. "
    "Any future or hypothetical time means DO NOT call it, even if the word join "
    "appears: 'join us later tonight' -> do not call. 'we might vc tomorrow' -> "
    "do not call. 'wanna join us sometime?' -> do not call, answer with words.",
    "ignored",
)
def join_voice(db, arg: str) -> str:
    if VOICE_DJ_ONLY:
        return _DJ_ONLY_NOTE
    current().PENDING_JOIN = True
    # ponytail: a tool cannot reach Discord objects, so flag it and let bot.py
    # act. It can only ever join the speaker's own channel — a wrong call is a
    # no-op, never her barging into someone else's call.
    return "joining if they're in a voice channel"


@tool(
    "Leave the voice channel, RIGHT NOW. Call it ONLY when they want you out of "
    "the call this second: 'ออกไป', 'ออกห้อง', 'ออกจากห้องเสียง', 'ไปได้แล้ว', "
    "'get out', 'leave the vc'. "
    "A future or hypothetical time means DO NOT call it: 'ออกไปตอนดึกนะ' -> do not "
    "call. Wanting the MUSIC to stop is NOT wanting you gone — that is "
    "stop_music, and calling this instead would drop you out of the call.",
    "ignored",
)
def leave_voice(db, arg: str) -> str:
    if VOICE_DJ_ONLY:
        return _DJ_ONLY_NOTE
    current().PENDING_LEAVE = True
    # same shape as join_voice: a tool cannot reach Discord objects, so flag it
    # and let bot.py act after she has finished speaking.
    return "leaving the voice channel"


# Turn.DJ holds the actions she asked for this turn: [(action, arg)]. bot.py
# drains it after the reply, so a song never blocks her talking.
@tool(
    "Play music in the voice channel. Call this whenever someone wants to HEAR "
    "something, in any language. Thai asks for music like this: "
    "'เปิดเพลง...', 'เปิด...ให้หน่อย', 'ฟังเพลง...', 'ขอเพลง...', 'อยากฟัง...', "
    "'อยากได้เพลง...' — all of them mean play it now. "
    "THEY DO NOT HAVE TO NAME A SONG. If they ask YOU to choose, or give only a "
    "mood, a genre, an activity or a game ('เลือกให้หน่อย', 'อะไรก็ได้', 'มันๆ', "
    "'something chill', 'whatever you like'), invent the search terms yourself "
    "and call this anyway. Replying with 'which genre do you want?' instead of "
    "calling it is a FAILURE — pick one and put it on. "
    "Pass ONLY the search terms, never the whole sentence: "
    "'เปิดเพลงRick rollให้หน่อย' -> 'Rick roll'; 'อยากฟังเพลงลูกทุ่ง' -> 'เพลงลูกทุ่ง'; "
    "'อยากได้เพลงเล่น Marvel rival เลือกให้หน่อย มันๆ' -> 'hype gaming EDM'; "
    "'ขอเพลงฟังตอนทำงานหน่อย' -> 'lofi work music'; "
    "'play some lofi to study to' -> 'lofi study'.",
    "song, artist, genre or mood to search for — invent one if they did not say",
)
def play_music(db, arg: str) -> str:
    turn = current()
    if turn.PENDING_MUSIC:
        # Second play this turn. PENDING_MUSIC holds one string, so a plain
        # assignment would drop the first song on the floor — while this function
        # had already told her "it will start playing in a moment" about it. That
        # is the silent-confabulation shape twice over. She can reach here two
        # ways: two play_music calls in one round, or one per tool round (the
        # loop allows 3). Queue it instead: nothing is lost, and it cannot
        # double-start the deck.
        turn.DJ.append(("queue", arg))
    else:
        turn.PENDING_MUSIC = arg
    # She used to answer "never heard of it" while the track was already
    # starting: her never-bluff rule fired on a song title she did not know.
    # Knowing a song is not required to play one.
    return (f"YouTube is being searched for '{arg}' and it will start playing "
            f"in a moment. You do NOT need to recognise this song — say you are "
            f"putting it on. Never claim you cannot find it. "
            # real log: 'เปิดให้แล้ว "เพลงปล้น" ของ Bodyslam' — the search had not
            # even run yet. She fills the gap with a plausible artist because the
            # tool gave her a query and she read it as a result.
            f"You have NOT seen the result: you do not know the artist, the album, "
            f"the year or the video title. Name none of them. Say what you searched "
            f"for and nothing more.")


@tool(
    "Stop the music completely and clear the queue. Thai: 'หยุดเพลง', "
    "'ปิดเพลง', 'พอแล้ว', 'หยุด'.",
    "ignored",
)
def stop_music(db, arg: str) -> str:
    current().PENDING_MUSIC = ""
    return "stopping the music"


@tool(
    "Queue a song to play AFTER the current one — use when music is already "
    "playing and they want more, not instead. Thai: 'ต่อด้วย...', 'ใส่คิว...', "
    "'เปิดต่อ...', 'เพิ่มเพลง...ลงคิว'. Pass only the search terms. "
    "ONE CALL QUEUES ONE SONG. If they ask for several ('หลายๆเพลง', 'a few "
    "songs', 'some more'), call this MULTIPLE TIMES in the same reply — 3 calls "
    "for 3 songs. Repeating the same search is correct and gives a different "
    "song each time; do not invent song titles to vary it.",
    "song, artist or genre to queue",
)
def queue_music(db, arg: str) -> str:
    current().DJ.append(("queue", arg))
    return (f"'{arg}' is queued to play next. You do NOT need to recognise it — "
            f"the search handles that.")


@tool(
    "Skip the current song and play the next queued one. Use when they are "
    "bored of it or say it does not fit. Thai: 'ข้ามเพลง', 'เปลี่ยนเพลง', "
    "'ถัดไป', 'ไม่เอาเพลงนี้'.",
    "ignored",
)
def skip_music(db, arg: str) -> str:
    current().DJ.append(("skip", ""))
    return "skipping"


# No now_playing tool on purpose: what is on the deck is handed to her every
# turn by pipeline._doing(), so asking for it was a wasted round-trip — and one
# less tool in the list is one less way to miss play_music.



# Turn.PENDING_CALENDAR holds plain-language change requests awaiting Krich's ✅
@tool(
    "Request a change to Krich's calendar (add or cancel an event). Pass ONE "
    "plain sentence, e.g. 'add dentist tomorrow 15:00'. Queued until Krich confirms.",
    "the change in one sentence",
)
def calendar_write(db, arg: str) -> str:
    current().PENDING_CALENDAR.append(arg)
    return "queued — Krich must confirm with ✅ before it happens"


if __name__ == "__main__":  # runnable check: registry shape + dispatch
    db = memory.connect(":memory:")
    memory.remember(db, "Krich", "cousin", "Steven")
    for t in TOOLS.values():
        assert "name" in t["schema"]["function"]["parameters"]["properties"]  # _arg_name contract
    assert "Steven" in TOOLS["recall"]["fn"](db, "Steven")
    turn = new_turn()
    assert "confirm" in TOOLS["calendar_write"]["fn"](db, "add x tomorrow")
    assert turn.PENDING_CALENDAR == ["add x tomorrow"]
    # TIWA_VOICE=dj (the default): the channel is a speaker for music, so these
    # two answer honestly and set nothing. Silently setting nothing would be the
    # confabulation shape — she would say she joined and not have.
    assert "only for playing music" in TOOLS["join_voice"]["fn"](db, "")
    assert "only for playing music" in TOOLS["leave_voice"]["fn"](db, "")
    assert turn.PENDING_JOIN is False and turn.PENDING_LEAVE is False
    globals()["VOICE_DJ_ONLY"] = False  # TIWA_VOICE=full is one env var away
    try:
        TOOLS["join_voice"]["fn"](db, "")
        assert turn.PENDING_JOIN is True
        TOOLS["leave_voice"]["fn"](db, "")
        assert turn.PENDING_LEAVE is True
    finally:
        globals()["VOICE_DJ_ONLY"] = True
    TOOLS["play_music"]["fn"](db, "lofi")
    assert turn.PENDING_MUSIC == "lofi"
    # the module still answers to the old names, which is what let seven benches
    # and dashboard.py stay untouched through this refactor
    assert sys.modules[__name__].PENDING_MUSIC == "lofi"
    # calling the same tool twice in one turn is normal — 6 of the 7 multi-call
    # rounds in the live log were the SAME tool twice. A second play must not
    # overwrite the first, because she was already told the first was happening.
    TOOLS["play_music"]["fn"](db, "jazz")
    assert turn.PENDING_MUSIC == "lofi", "second play_music silently dropped the first"
    assert turn.DJ[-1] == ("queue", "jazz"), turn.DJ
    TOOLS["stop_music"]["fn"](db, "")
    assert turn.PENDING_MUSIC == ""
    # a new turn starts empty — the old globals carried the last song forever
    assert new_turn().PENDING_MUSIC is None
    print("tools ok:", ", ".join(TOOLS))
