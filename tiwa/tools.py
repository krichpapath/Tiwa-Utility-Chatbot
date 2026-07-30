"""Tool registry for the inner pass. Every tool: fn(db, arg: str) -> str.

Every tool takes ONE string argument named "name" — deliberate: the 8B mangles
nested/renamed structured args (see pipeline._arg_name), so structure is minted
later by schema-constrained calls, never by the tool-calling model.
"""
from . import memory
from .memory import TIWA

TOOLS = {}  # name -> {"schema": ollama tool spec, "fn": callable(db, arg) -> str}


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
    "Web search. Use ONLY for current events or facts outside memory that the "
    "message directly asks about. Never for people you should just recall.",
    "search query",
)
def web_search(db, arg: str) -> str:
    from ddgs import DDGS  # lazy: keeps dep optional for tests

    try:
        hits = DDGS().text(arg, max_results=3)
    except Exception as e:  # network flake -> brief says so instead of crashing the turn
        return f"search failed: {e}"
    return "\n".join(f"{h['title']}: {h['body']}" for h in hits) or "no results"


@tool(
    "Read Krich's Google Calendar (next 7 days). Use only when the message "
    "involves his schedule or plans.",
    "ignored",
)
def calendar_read(db, arg: str) -> str:
    from . import gcal

    return gcal.upcoming()


PENDING_JOIN = False  # set by the tool, acted on by bot.py after the reply
PENDING_LEAVE = False


@tool(
    "Join the voice channel the speaker is sitting in, RIGHT NOW. Call it ONLY if "
    "they want you in there this second: 'come join the vc', 'get in here'. "
    "Any future or hypothetical time means DO NOT call it, even if the word join "
    "appears: 'join us later tonight' -> do not call. 'we might vc tomorrow' -> "
    "do not call. 'wanna join us sometime?' -> do not call, answer with words.",
    "ignored",
)
def join_voice(db, arg: str) -> str:
    global PENDING_JOIN
    PENDING_JOIN = True
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
    global PENDING_LEAVE
    PENDING_LEAVE = True
    # same shape as join_voice: a tool cannot reach Discord objects, so flag it
    # and let bot.py act after she has finished speaking.
    return "leaving the voice channel"


# DJ actions she asked for this turn: [(action, arg)]. bot.py drains it after
# the reply, so a song never blocks her talking.
DJ = []
PENDING_MUSIC = None  # kept for the old play/stop path; bot.py drains both


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
    global PENDING_MUSIC
    PENDING_MUSIC = arg
    # She used to answer "never heard of it" while the track was already
    # starting: her never-bluff rule fired on a song title she did not know.
    # Knowing a song is not required to play one.
    return (f"YouTube is being searched for '{arg}' and it will start playing "
            f"in a moment. You do NOT need to recognise this song — say you are "
            f"putting it on. Never claim you cannot find it.")


@tool(
    "Stop the music completely and clear the queue. Thai: 'หยุดเพลง', "
    "'ปิดเพลง', 'พอแล้ว', 'หยุด'.",
    "ignored",
)
def stop_music(db, arg: str) -> str:
    global PENDING_MUSIC
    PENDING_MUSIC = ""
    return "stopping the music"


@tool(
    "Queue a song to play AFTER the current one — use when music is already "
    "playing and they want more, not instead. Thai: 'ต่อด้วย...', 'ใส่คิว...', "
    "'เปิดต่อ...'. Pass only the search terms.",
    "song, artist or genre to queue",
)
def queue_music(db, arg: str) -> str:
    DJ.append(("queue", arg))
    return (f"'{arg}' is queued to play next. You do NOT need to recognise it — "
            f"the search handles that.")


@tool(
    "Skip the current song and play the next queued one. Use when they are "
    "bored of it or say it does not fit. Thai: 'ข้ามเพลง', 'เปลี่ยนเพลง', "
    "'ถัดไป', 'ไม่เอาเพลงนี้'.",
    "ignored",
)
def skip_music(db, arg: str) -> str:
    DJ.append(("skip", ""))
    return "skipping"


# No now_playing tool on purpose: what is on the deck is handed to her every
# turn by pipeline._doing(), so asking for it was a wasted round-trip — and one
# less tool in the list is one less way to miss play_music.



PENDING_CALENDAR = []  # plain-language change requests awaiting Krich's ✅ in Discord


@tool(
    "Request a change to Krich's calendar (add or cancel an event). Pass ONE "
    "plain sentence, e.g. 'add dentist tomorrow 15:00'. Queued until Krich confirms.",
    "the change in one sentence",
)
def calendar_write(db, arg: str) -> str:
    PENDING_CALENDAR.append(arg)
    return "queued — Krich must confirm with ✅ before it happens"


if __name__ == "__main__":  # runnable check: registry shape + dispatch
    db = memory.connect(":memory:")
    memory.remember(db, "Krich", "cousin", "Steven")
    for t in TOOLS.values():
        assert "name" in t["schema"]["function"]["parameters"]["properties"]  # _arg_name contract
    assert "Steven" in TOOLS["recall"]["fn"](db, "Steven")
    assert "confirm" in TOOLS["calendar_write"]["fn"](db, "add x tomorrow")
    assert PENDING_CALENDAR == ["add x tomorrow"]
    TOOLS["join_voice"]["fn"](db, "")
    assert PENDING_JOIN is True
    TOOLS["leave_voice"]["fn"](db, "")
    assert PENDING_LEAVE is True
    TOOLS["play_music"]["fn"](db, "lofi")
    assert PENDING_MUSIC == "lofi"
    TOOLS["stop_music"]["fn"](db, "")
    assert PENDING_MUSIC == ""
    print("tools ok:", ", ".join(TOOLS))
