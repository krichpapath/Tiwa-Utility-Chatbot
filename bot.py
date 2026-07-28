"""Tiwa Discord bot — entrypoint. The brain lives in tiwa/."""
import asyncio
import os
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

import discord
from discord.ext import tasks

from tiwa import gcal, memory, pipeline, tools

# minimal .env loader (KEY=VALUE lines), no dotenv dependency
env = Path(__file__).with_name(".env")
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

db = memory.connect()
history = defaultdict(lambda: deque(maxlen=40))  # channel_id -> chat messages
locks = defaultdict(asyncio.Lock)  # serialize replies per channel

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

HOME = os.environ.get("TIWA_HOME_CHANNEL")  # channel id for unprompted messages; unset = quiet
pending_confirms = {}  # confirm-message id -> plain-language calendar request


async def _flush_calendar_queue(channel):
    """calendar_write only queues; every write is gated behind Krich's ✅ here."""
    while tools.PENDING_CALENDAR:
        text = tools.PENDING_CALENDAR.pop(0)
        m = await channel.send(f"📅 calendar change: **{text}** — ✅ to confirm, ❌ to drop")
        await m.add_reaction("✅")
        await m.add_reaction("❌")
        pending_confirms[m.id] = text


last_unprompted = datetime.min  # ponytail: >=3h between unprompted messages, no spam


@tasks.loop(minutes=30)
async def idle_turn():
    global last_unprompted
    if not 9 <= datetime.now().hour < 23:  # quiet hours
        return
    if (datetime.now() - last_unprompted).total_seconds() < 3 * 3600:
        return
    thought = await pipeline.idle(db)
    channel = client.get_channel(int(HOME)) or await client.fetch_channel(int(HOME))
    if thought:
        last_unprompted = datetime.now()
        history[channel.id].append({"role": "assistant", "content": thought})
        await channel.send(thought)
    await _flush_calendar_queue(channel)  # idle turns may queue calendar changes too


@client.event
async def on_ready():
    print(f"logged in as {client.user}")
    if HOME and not idle_turn.is_running():
        idle_turn.start()


@client.event
async def on_raw_reaction_add(payload):
    text = pending_confirms.get(payload.message_id)
    if text is None or payload.user_id == client.user.id or str(payload.emoji) not in "✅❌":
        return
    del pending_confirms[payload.message_id]  # one shot
    channel = client.get_channel(payload.channel_id) or await client.fetch_channel(
        payload.channel_id
    )
    if str(payload.emoji) == "✅":
        result = await asyncio.to_thread(gcal.apply_change, text)
        await channel.send(result)
    else:
        await channel.send("dropped.")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    text = message.content.replace(f"<@{client.user.id}>", "").strip()
    if text:
        # record everything so Tiwa has channel context; reply only when addressed
        history[message.channel.id].append(
            {"role": "user", "content": f"{message.author.display_name}: {text}"}
        )
    if message.guild is not None and client.user not in message.mentions:
        return
    if not text:
        return
    author = message.author.display_name
    async with locks[message.channel.id]:
        async with message.channel.typing():
            reply = await pipeline.respond(db, list(history[message.channel.id]), author, text)
        if not reply:
            return
        history[message.channel.id].append({"role": "assistant", "content": reply})
        # post-turn memory write, off the reply path; prior lines let "he/she" resolve
        ctx = memory.history_context(list(history[message.channel.id]))
        asyncio.create_task(asyncio.to_thread(memory.extract, db, author, text, reply, ctx))
        for i in range(0, len(reply), 2000):
            await message.channel.send(reply[i : i + 2000])
        await _flush_calendar_queue(message.channel)


client.run(os.environ["DISCORD_TOKEN"])
