"""Every Discord entry point, end to end, offline.

`djbench` owns the deck and `outagebench` owns the failure paths. This owns the
GLUE — the part of `bot.py` between discord.py and the pipeline, which is where
the bugs that reach a real channel actually live: who gets answered, what gets
recorded, what a picture turns into, what a ✅ does, and whether a queued action
survives a turn that said nothing.

Nothing here calls a model. `pipeline.respond` is replaced, so every row is a
deterministic check and a failure is a real bug.

    py -X utf8 tests\\discordbench.py
"""

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, music, tools  # noqa: E402

from tiwa import gcal
import bot  # noqa: E402  (imports discord, does not connect)

# bot.db is the REAL data/tiwa.db and every turn logs to it. Benches never touch it.
bot.db = memory.connect(":memory:")
bot.player.db = bot.calendar.db = bot.db

ME = types.SimpleNamespace(id=999, bot=True, display_name="Tiwa")
bot.calendar.owner_id = "7"
gcal.prepare_change = lambda text: text
gcal.describe_change = lambda text: text
bot.client = types.SimpleNamespace(
    user=ME, loop=None, get_channel=lambda i: CHANNELS.get(i), fetch_channel=None
)
bot.player.client = bot.client
CHANNELS = {}


class FakeChannel:
    def __init__(self, cid=1, guild=None):
        self.id = cid
        self.guild = guild
        self.sent = []
        CHANNELS[cid] = self

    async def send(self, text):
        self.sent.append(text)
        return types.SimpleNamespace(id=len(self.sent) + 5000, add_reaction=self._noop)

    async def _noop(self, *a):
        pass

    def typing(self):
        return _Typing()


class _Typing:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def msg(text, channel, author="Krich", mentions=(), guild=..., files=()):
    """One discord.Message, only the attributes bot.py actually reads."""
    return types.SimpleNamespace(
        content=text,
        author=types.SimpleNamespace(bot=False, display_name=author, id=7),
        attachments=[types.SimpleNamespace(url=u, content_type=t) for u, t in files],
        guild=channel.guild if guild is ... else guild,
        mentions=list(mentions),
        channel=channel,
    )


REPLY = "sure"
seen_calls = []
extracted = []


async def fake_respond(db, hist, author, text, images=(), on_late=None):
    seen_calls.append(
        {
            "author": author,
            "text": text,
            "images": list(images),
            "hist": len(hist),
            "on_late": on_late,
        }
    )
    return REPLY


def fake_extract(db, user, said, reply, ctx=""):
    extracted.append((user, said, reply))


bot.pipeline.respond = fake_respond
bot.memory.extract = fake_extract


def reset(cid=1, guild=None):
    bot.history.clear()
    bot.locks.clear()
    bot.calendar.pending.clear()
    seen_calls.clear()
    extracted.clear()
    tools.new_turn()
    music.NOW["title"], music.QUEUE[:] = None, []
    return FakeChannel(cid, guild)


# ---------------------------------------------------------------- who is answered


def who_gets_answered():
    """In a server she answers a MENTION and nothing else. Everything said in the
    channel is still written down — that is what makes "he" resolve three
    messages later, and it costs nothing because no model runs."""
    guild = types.SimpleNamespace(id=1, voice_client=None)
    ch = reset(guild=guild)

    asyncio.run(bot.on_message(msg("just chatting", ch)))
    assert not seen_calls, "she answered a message that did not mention her"
    assert len(bot.history[ch.id]) == 1, "the channel line was not recorded"
    assert not ch.sent, "she spoke uninvited"

    asyncio.run(bot.on_message(msg("<@999> hey", ch, mentions=[ME])))
    assert len(seen_calls) == 1, "a mention went unanswered"
    assert seen_calls[0]["text"] == "hey", "the mention was not stripped"
    assert ch.sent == [REPLY], ch.sent

    # her own messages never come back round
    asyncio.run(
        bot.on_message(
            types.SimpleNamespace(
                content="x",
                author=types.SimpleNamespace(bot=True),
                attachments=[],
                guild=guild,
                mentions=[],
                channel=ch,
            )
        )
    )
    assert len(seen_calls) == 1, "she answered a bot"

    # a DM has no guild, so no mention is needed
    dm = reset(cid=2, guild=None)
    asyncio.run(bot.on_message(msg("hello", dm, guild=None)))
    assert len(seen_calls) == 1, "a DM went unanswered"
    print("who ok      — mention in a server, anything in a DM, bots never, everything recorded")


def the_reply_reaches_the_channel():
    """Split at 2000, because discord rejects longer. And an EMPTY reply must
    still let queued actions run — a `return` here once swallowed a song that
    was already searched for."""
    global REPLY
    ch = reset()
    was, REPLY = REPLY, "x" * 4500
    try:
        asyncio.run(bot.on_message(msg("<@999> long", ch, mentions=[ME])))
        assert len(ch.sent) == 3, f"a 4500-char reply went out in {len(ch.sent)} parts"
        assert "".join(ch.sent) == was * 0 + "x" * 4500
    finally:
        REPLY = was

    ch = reset()
    REPLY, keep = "", REPLY
    try:
        tools.new_turn()

        async def go():
            await bot.on_message(msg("<@999> play x", ch, mentions=[ME]))
            return list(bot.history[ch.id])

        hist = asyncio.run(go())
        assert not extracted, "an empty reply was still written to memory"
        assert len(hist) == 1, "an empty reply was appended to history as hers"
    finally:
        REPLY = keep
    print("send ok     — >2000 chars split, an empty reply still runs the flush")


def what_she_can_see():
    """Only real images cost a vision call. A caption-less picture still has to
    reach memory as something, or 'ดูสิ' three messages later resolves to nothing."""
    ch = reset()
    asyncio.run(
        bot.on_message(
            msg("<@999> look", ch, mentions=[ME], files=[("http://x/a.png", "image/png")])
        )
    )
    assert seen_calls[-1]["images"] == ["http://x/a.png"], seen_calls[-1]

    ch = reset()
    asyncio.run(
        bot.on_message(
            msg(
                "<@999> here",
                ch,
                mentions=[ME],
                files=[("http://x/a.zip", "application/zip"), ("http://x/b.txt", None)],
            )
        )
    )
    assert seen_calls[-1]["images"] == [], "a .zip cost a vision call"

    ch = reset()
    asyncio.run(
        bot.on_message(msg("<@999>", ch, mentions=[ME], files=[("http://x/c.png", "image/png")]))
    )
    assert extracted and extracted[-1][1] == "[image]", (
        f"a caption-less image reached memory as {extracted[-1][1]!r}"
    )
    print("eyes ok     — images pass, a .zip does not, a bare picture is '[image]'")


# ---------------------------------------------------------------- the ✅ gate


def the_calendar_gate():
    """calendar_write only ever queues a sentence. The reaction IS the write,
    it is one-shot, and her own reaction must not count."""
    ch = reset()
    applied = []
    gcal.apply_change = lambda text: applied.append(text) or f"added {text}"

    tools.PENDING_CALENDAR.append("add dentist tomorrow 15:00")
    asyncio.run(bot.calendar.propose(ch))
    assert len(ch.sent) == 1 and "ให้บันทึกตามนี้เลยไหม" in ch.sent[0], ch.sent
    assert not applied, "the write happened without a reaction"
    assert len(bot.calendar.pending) == 1, bot.calendar.pending
    mid = next(iter(bot.calendar.pending))

    def react(emoji, uid=7, message_id=None):
        return types.SimpleNamespace(
            message_id=message_id or mid, user_id=uid, emoji=emoji, channel_id=ch.id
        )

    asyncio.run(bot.on_raw_reaction_add(react("✅", uid=ME.id)))
    assert not applied, "her OWN reaction confirmed the write"
    asyncio.run(bot.on_raw_reaction_add(react("🎉")))
    assert not applied and bot.calendar.pending, "an unrelated emoji consumed the gate"
    asyncio.run(bot.on_raw_reaction_add(react("✅", uid=8)))
    assert not applied and bot.calendar.pending, "a stranger approved the owner's calendar"

    asyncio.run(bot.on_raw_reaction_add(react("✅")))
    assert applied == ["add dentist tomorrow 15:00"], applied
    assert not bot.calendar.pending, "the gate is not one-shot"
    asyncio.run(bot.on_raw_reaction_add(react("✅")))
    assert len(applied) == 1, "a second ✅ wrote the event twice"

    # ...and ❌ drops it without touching Google
    tools.PENDING_CALENDAR.append("cancel friday")
    asyncio.run(bot.calendar.propose(ch))
    mid = next(iter(bot.calendar.pending))
    asyncio.run(bot.on_raw_reaction_add(react("❌", message_id=mid)))
    assert len(applied) == 1, "❌ still wrote the event"
    assert "dropped" in ch.sent[-1], ch.sent[-1]
    print("gate ok     — ✅ writes once, ❌ drops, her own reaction and 🎉 do nothing")


# ---------------------------------------------------------------- late follow-up


def the_late_line():
    """A mini that finishes after she spoke sends a SECOND message, never an
    edit — and it has to land in history, or the next turn cannot see what she
    just said."""
    ch = reset()
    send = bot._later(ch)
    asyncio.run(send("turns out it was Arsenal"))
    assert ch.sent == ["turns out it was Arsenal"], ch.sent
    assert bot.history[ch.id][-1] == {"role": "assistant", "content": "turns out it was Arsenal"}
    asyncio.run(send("y" * 3000))
    assert len(ch.sent[-1]) == 2000, "a long follow-up was not truncated"

    # respond() gets a real sender on the Discord path, or nothing can follow up
    ch = reset()
    asyncio.run(bot.on_message(msg("<@999> who won", ch, mentions=[ME])))
    assert callable(seen_calls[-1]["on_late"]), "the turn had no way to follow up"
    print("late ok     — a second message, capped, and it reaches history")


# ---------------------------------------------------------------- voice + music


def voice_commands_cost_nothing():
    """`join` and `leave` typed exactly are handled before the pipeline — no
    model call at all. Anything longer is a conversation, not a command."""
    guild = types.SimpleNamespace(id=1, voice_client=None)
    ch = reset(guild=guild)
    joined = []

    async def fake_join(author):
        joined.append(author)
        return "joined general"

    async def fake_leave(guild):
        return "left"

    bot.voice.join = fake_join
    bot.voice.leave = fake_leave
    bot.voice.listen = AsyncMock(return_value="listening")

    for cmd in ("join", "เข้ามา", "JOIN "):
        asyncio.run(bot.on_message(msg(f"<@999> {cmd}", ch, mentions=[ME])))
    assert len(joined) == 3, f"an exact join command went to the model: {joined}"
    assert not seen_calls, "a command spent tokens"

    asyncio.run(bot.on_message(msg("<@999> leave", ch, mentions=[ME])))
    assert not seen_calls, "leave spent tokens"

    # ...but a sentence containing the word is a conversation
    asyncio.run(bot.on_message(msg("<@999> wanna join us later?", ch, mentions=[ME])))
    assert len(seen_calls) == 1, "a sentence was eaten as a command"
    print("cmd ok      — join/leave are free and exact; a sentence still reaches her")


def music_pulls_her_in():
    """Asking for a song IS asking her into the channel — nobody should have to
    type `join` first. And with nowhere to go she says so instead of silently
    dropping the song."""
    guild = types.SimpleNamespace(id=1, voice_client=None)
    ch = reset(guild=guild)
    author = object()

    async def ok_join(a):
        return "joined general"

    bot.voice.join = ok_join
    bot.voice.listen = AsyncMock(return_value="listening")

    async def fake_find(channel, query):
        return {"title": f"{query} (video)", "url": "http://x", "query": query}

    bot._find = fake_find
    music.source_for = lambda hit: object()

    tools.DJ.append(("play", "bad apple"))
    asyncio.run(bot.player.flush(ch, author))
    assert bot.voice_channel.get(1) is ch, "asking for music did not bring her in"

    # she is in the call but the join fails -> the reason reaches the channel
    ch = reset(guild=types.SimpleNamespace(id=2, voice_client=None))

    async def bad_join(a):
        return "you're not in a voice channel"

    bot.voice.join = bad_join
    tools.DJ.append(("play", "x"))
    asyncio.run(bot.player.flush(ch, author))
    assert "not in a voice channel" in ch.sent[-1], ch.sent
    print("music ok    — a song brings her into the call, and a failure is spoken")


def a_dead_turn_is_never_silent():
    """The failure this codebase minds most. A raising turn used to print a
    traceback to a console nobody was reading and post NOTHING."""
    ch = reset()

    async def boom(*a, **k):
        raise RuntimeError("401 Unauthorized — User not found.")

    was, bot.pipeline.respond = bot.pipeline.respond, boom
    try:
        asyncio.run(bot.on_message(msg("<@999> hey", ch, mentions=[ME])))
    finally:
        bot.pipeline.respond = was
    assert ch.sent, "a dead turn said nothing at all"
    assert "OPENROUTER_API_KEY" in ch.sent[0], ch.sent[0]
    print("outage ok   — a dead turn names the cause in the channel")


who_gets_answered()
the_reply_reaches_the_channel()
what_she_can_see()
the_calendar_gate()
the_late_line()
voice_commands_cost_nothing()
music_pulls_her_in()
a_dead_turn_is_never_silent()
print("\ndiscord ok — every entry point drives the real bot.py, no model involved")
