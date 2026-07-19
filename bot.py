"""Tiwa Discord bot — entrypoint. The brain lives in tiwa/."""
import asyncio
import os
from collections import defaultdict, deque
from pathlib import Path

import discord

from tiwa import memory, pipeline

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


@client.event
async def on_ready():
    print(f"logged in as {client.user}")


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


client.run(os.environ["DISCORD_TOKEN"])
