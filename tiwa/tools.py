"""Per-turn state, and the actuators the Mini Tiwas drive.

This file used to be a tool REGISTRY: a `TOOLS` dict of schemas handed to a
tool-calling model, which read ten descriptions and picked one. That model pass
is gone from this branch — `minis.py` is the registry now, and the functions
below are what a mini reaches for once it has already decided.

So they take a decided argument and act. No descriptions, because nothing reads
them; no return paragraphs, because nothing is listening. `web_search` is the one
exception and returns its results, since Search Tiwa needs them.

What is emphatically NOT gone is `Turn`: one turn's pending actions, scoped to
the asyncio task that asked for them. bot.py, chat.py and dashboard.py drain it
after she speaks, exactly as before.

    py -X utf8 -m tiwa.tools
"""

import contextvars
import dataclasses
import os
import sys
import types

from . import memory

# Read here rather than importing voice, which pulls in discord, whisper and
# onnxruntime. Same env var, same meaning — voice.DJ_ONLY is the definition.
VOICE_DJ_ONLY = os.environ.get("TIWA_VOICE", "dj") != "full"


@dataclasses.dataclass
class Turn:
    """What one turn asked for. These six were module globals until 2026-08-21.

    Field names are the old global names on purpose — see the module shim below.
    """

    DJ: list = dataclasses.field(default_factory=list)
    PENDING_MUSIC: "str | dict | None" = None
    PENDING_CALENDAR: list = dataclasses.field(default_factory=list)
    PENDING_JOIN: bool = False
    PENDING_LEAVE: bool = False
    SEEN_URLS: set = dataclasses.field(default_factory=set)


# A ContextVar, not a global and not threading.local: asyncio copies the context
# into every new Task, and asyncio.to_thread carries it across the thread
# boundary — which is exactly the path a mini takes (turn work runs in threads).
# discord.py dispatches every message as its own Task, so two channels talking at
# once get two Turns for free. A plain global gave them one, and
# `locks[channel.id]` only ever serialized WITHIN a channel.
_TURN = contextvars.ContextVar("tiwa_turn")


def current() -> Turn:
    """The turn in flight. Creates one if nobody called new_turn() — benches and
    `python -m tiwa.tools` poke these directly and should keep working."""
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
# so bot.py, chat.py and dashboard.py did not have to change — which is what makes
# "no behaviour changed" checkable by running them unmodified. Module-level code
# inside THIS file writes f_globals directly and never reaches here, so the
# actuators below must say current().X explicitly.
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


# ---------------------------------------------------------------- the web


def web_search(db, arg: str) -> str:
    """Search Tiwa's hands. Returns text — never raises, so a network flake
    arrives as a sentence the caller can recognise instead of eating the turn.

    The keyword rules that used to live in this function's tool description now
    live in `minis._SEARCH_SYSTEM`, which is the only prompt that reads them.
    """
    from urllib.parse import urlsplit  # stdlib: domain is the only source signal she gets

    from ddgs import DDGS  # lazy: keeps dep optional for tests

    # region decides the index. Measured on 'ผลบอลพรีเมียร์ลีก': us-en (the ddgs
    # default) returns pinterest, youtube and a blogspot; th-th returns thairath,
    # trueid and kapook. Same query, one keyword argument apart.
    region = "th-th" if any("฀" <= c <= "๿" for c in arg) else "us-en"
    try:
        hits = DDGS().text(arg, region=region, max_results=8)
    except Exception as e:
        return f"search failed: {e}"
    # ponytail: dedupe by url within the turn only. Across turns she is allowed to
    # find the same page again — that is a fresh question, not a repeat.
    seen = current().SEEN_URLS
    fresh = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        url = h.get("href", "")
        if not isinstance(url, str):
            continue
        try:
            valid = urlsplit(url).scheme in ("http", "https") and urlsplit(url).hostname
        except ValueError:
            continue
        if not valid or url in seen:
            continue
        seen.add(url)
        fresh.append(h)
        if len(fresh) == 5:
            break
    if not fresh:
        return (
            "every result was one you already saw this turn — these keywords are "
            "spent, search something different or answer with what you have"
        )
    return (
        "\n".join(
            f"{str(h.get('title', ''))[:180]} [{urlsplit(h['href']).netloc}] "
            f"{h['href']}: {' '.join(str(h.get('body', '')).split())[:600]}"
            for h in fresh
        )
        or "no results"
    )


# ---------------------------------------------------------------- voice

# No mini owns voice: 0 calls in 135 logged turns, so a model round trip for it
# would be absurd. `pipeline._asked_voice()` is the classifier that reaches these,
# and it returns "" under TIWA_VOICE=dj — so under the default they are never
# called at all. A tool cannot reach Discord objects either way, so both only
# flag the Turn and let bot.py act after she has finished speaking.


def join_voice(db, arg: str = "") -> None:
    if VOICE_DJ_ONLY:
        return  # the channel is a speaker for music; Player.flush brings her in
    current().PENDING_JOIN = True


def leave_voice(db, arg: str = "") -> None:
    if VOICE_DJ_ONLY:
        return
    current().PENDING_LEAVE = True


# ---------------------------------------------------------------- the deck

# Turn.DJ holds the actions she asked for this turn: [(action, arg)]. bot.py
# drains it after the reply, so a song never blocks her talking. DJ Tiwa
# (minis.dj) is the only thing that calls these — it has already chosen the
# action and the search terms by the time it gets here.


def play_music(db, arg: str | dict) -> None:
    turn = current()
    if turn.PENDING_MUSIC:
        # Second play this turn. PENDING_MUSIC holds one string, so a plain
        # assignment would drop the first song on the floor — while she had
        # already been told the first was happening. Queue it instead: nothing is
        # lost, and it cannot double-start the deck.
        turn.DJ.append(("queue", arg))
    else:
        turn.PENDING_MUSIC = arg


def stop_music(db, arg: str = "") -> None:
    current().PENDING_MUSIC = ""


def queue_music(db, arg: str | dict) -> None:
    current().DJ.append(("queue", arg))


def skip_music(db, arg: str = "") -> None:
    current().DJ.append(("skip", ""))


# No now_playing: what is on the deck is handed to her every turn by
# pipeline._doing(), so asking for it was always a wasted round-trip.


# ---------------------------------------------------------------- calendar

# Turn.PENDING_CALENDAR holds plain-language change requests awaiting requester approval.
# Calendar Tiwa decides WHAT to propose; this only queues it, and conversational approval is
# still the write.


def calendar_write(db, arg: str) -> None:
    current().PENDING_CALENDAR.append(arg)


if __name__ == "__main__":  # runnable check: the Turn is per-turn and the actuators act
    db = memory.connect(":memory:")
    turn = new_turn()

    calendar_write(db, "add x tomorrow")
    assert turn.PENDING_CALENDAR == ["add x tomorrow"]

    # TIWA_VOICE=dj (the default): the channel is a speaker for music, so these
    # two set nothing at all. Silently setting the flag would be the shape this
    # whole codebase minds — she would say she joined and not have.
    join_voice(db)
    leave_voice(db)
    assert turn.PENDING_JOIN is False and turn.PENDING_LEAVE is False
    globals()["VOICE_DJ_ONLY"] = False  # TIWA_VOICE=full is one env var away
    try:
        join_voice(db)
        leave_voice(db)
        assert turn.PENDING_JOIN is True and turn.PENDING_LEAVE is True
    finally:
        globals()["VOICE_DJ_ONLY"] = True

    play_music(db, "lofi")
    assert turn.PENDING_MUSIC == "lofi"
    # the module still answers to the old names, which is what lets bot.py,
    # chat.py and dashboard.py drain the Turn without knowing it exists
    assert sys.modules[__name__].PENDING_MUSIC == "lofi"
    # a second play must not overwrite the first: she was already told about it
    play_music(db, "jazz")
    assert turn.PENDING_MUSIC == "lofi", "second play_music silently dropped the first"
    assert turn.DJ[-1] == ("queue", "jazz"), turn.DJ
    skip_music(db)
    assert turn.DJ[-1] == ("skip", "")
    stop_music(db)
    assert turn.PENDING_MUSIC == ""

    # a new turn starts empty — the old module globals carried the last song forever
    assert new_turn().PENDING_MUSIC is None
    print("tools ok: Turn is per-turn, actuators act, no registry left to read")
