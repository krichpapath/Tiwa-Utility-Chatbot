"""One human ingress check: owner sends @Tiwa QA hello in the approved channel.

No voice, calendar changes, idle posts, or private memory. Stops after one reply
or four minutes. Run with TIWA_DATA_DIR set to a disposable directory.
"""

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import memory
import bot

bot.db = memory.connect(":memory:")

bot.player.db = bot.calendar.db = bot.db
handler = bot.on_message
result = {"status": "no human input received"}
ready = False


@bot.client.event
async def on_ready():
    global ready
    if ready:
        return
    ready = True
    bot.calendar.owner_id = str((await bot.client.application_info()).owner.id)
    channel = await bot.client.fetch_channel(int(os.environ["TIWA_HOME_CHANNEL"]))
    await channel.send(
        "[QA] Final human ingress check: owner may send @Tiwa QA hello here within four minutes. No voice/calendar actions."
    )
    print("READY for owner: @Tiwa QA hello", flush=True)
    async for message in channel.history(
        limit=20, after=datetime.now(timezone.utc) - timedelta(minutes=15)
    ):
        if await check_message(message, "channel_history"):
            break


@bot.client.event
async def on_message(message):
    await check_message(message, "gateway")


async def check_message(message, source):
    text = (
        message.content.replace(f"<@{bot.client.user.id}>", "")
        .replace(f"<@!{bot.client.user.id}>", "")
        .strip()
    )
    if (
        message.author.bot
        or str(message.author.id) != bot.calendar.owner_id
        or str(message.channel.id) != os.environ["TIWA_HOME_CHANNEL"]
        or bot.client.user not in message.mentions
        or text.lower() != "qa hello"
    ):
        return False
    await handler(message)
    replied = any(row["role"] == "assistant" for row in bot.history[message.channel.id])
    result.update(
        status="pass" if replied else "failed",
        gateway_event_received=source == "gateway",
        human_message_verified=True,
        message_source=source,
        channel_id=message.channel.id,
        reply_delivered=replied,
    )
    await bot.client.close()
    return True


async def main():
    try:
        await asyncio.wait_for(bot.client.start(os.environ["DISCORD_TOKEN"]), timeout=240)
    except asyncio.TimeoutError:
        pass
    finally:
        await bot.client.close()
        out = (
            Path(__file__).resolve().parents[1] / "qa-results" / "acceptance" / "discord-human.json"
        )
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result), flush=True)


asyncio.run(main())
