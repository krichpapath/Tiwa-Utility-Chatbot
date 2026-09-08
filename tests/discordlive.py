"""Gateway + real delivery smoke test in the explicitly authorized home channel.

Inputs below are labeled synthetic fixtures passed to real on_message handlers.
This is not evidence of human-client ingress, reactions, or voice transport.
Never approves a Google write. Uses isolated memory and disables idle messages.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace as Obj
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import memory, pipeline, tools
import bot

bot.db = memory.connect(":memory:")
ran = False
results = []


@bot.client.event
async def on_ready():
    global ran
    if ran:
        return
    ran = True
    bot.OWNER_ID = str((await bot.client.application_info()).owner.id)
    channel = await bot.client.fetch_channel(int(os.environ["TIWA_HOME_CHANNEL"]))
    try:
        await channel.send("[QA acceptance] Synthetic conversation tests starting. No calendar writes or voice calls.")
        author = Obj(id=0, bot=False, display_name="QA Fixture", voice=None)
        for text in ("hello Tiwa, please answer in English", "ทิวา วันนี้เป็นไงบ้าง",
                     "my cousin QA Steven plays guitar", "what instrument does QA Steven play?",
                     "from now on your favourite singer is BLACKPINK", "play Mili Hero"):
            await channel.send(f"[QA synthetic input] {text}")
            await bot.on_message(Obj(author=author, channel=channel, guild=channel.guild,
                                     content=f"<@{bot.client.user.id}> {text}",
                                     mentions=[bot.client.user], attachments=[]))
            pending = pipeline._pending.get(author.display_name)
            if pending:
                await asyncio.gather(pending, return_exceptions=True)
            results.append({"input": text, "handler": "returned"})
        async def fail(*args, **kwargs):
            raise ConnectionError("QA simulated provider outage")
        await channel.send("[QA synthetic input] Provider outage: expected visible error, then recovery.")
        with patch.object(pipeline, "respond", fail):
            await bot.on_message(Obj(author=author, channel=channel, guild=channel.guild,
                                     content="QA outage", mentions=[bot.client.user], attachments=[]))
        await bot.on_message(Obj(author=author, channel=channel, guild=channel.guild,
                                 content="back now, say hello", mentions=[bot.client.user], attachments=[]))
        results.append({"outage_recovery": "handlers returned"})
        await channel.send("[QA acceptance] Synthetic delivery checks finished. Voice and calendar writes were not exercised.")
    except Exception as error:
        results.append({"error": type(error).__name__, "detail": str(error)[:200]})
        raise
    finally:
        out = Path(__file__).resolve().parents[1] / "qa-results" / "acceptance" / "discord-live.json"
        out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        # Allow fire-and-forget fixture memory extraction to finish before loop shutdown.
        await asyncio.sleep(3)
        await bot.client.close()


bot.client.run(os.environ["DISCORD_TOKEN"])
