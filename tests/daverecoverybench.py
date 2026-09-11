"""DAVE recovery tests: fake connections, no Discord or STT traffic."""

import asyncio
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import voice


async def main():
    channel = Obj(connect=AsyncMock())
    vc = Obj(channel=channel, disconnect=AsyncMock(), _tiwa_decrypt_failures={1: (90, 99, 2)})
    guild = Obj(id=999999, voice_client=vc)
    voice._dave_recoveries.clear()
    with patch.object(voice.time, "monotonic", return_value=100):
        assert await voice.recover_decryption(guild) is None
        vc.disconnect.assert_not_called()
        vc._tiwa_decrypt_failures = {1: (90, 99, 30)}
        assert "reconnected" in await voice.recover_decryption(guild)
        vc.disconnect.assert_awaited_once_with(force=True)
        channel.connect.assert_awaited_once()
        assert await voice.recover_decryption(guild) is None
    with patch.object(voice.time, "monotonic", return_value=170):
        assert await voice.recover_decryption(guild) is None  # old errors are not evidence
        vc._tiwa_decrypt_failures = {1: (160, 169, 30)}
        channel.connect.side_effect = RuntimeError("offline")
        assert "failed" in await voice.recover_decryption(guild)
    with patch.object(voice.time, "monotonic", return_value=240):
        vc._tiwa_decrypt_failures = {1: (230, 239, 30)}
        assert await voice.recover_decryption(guild) is None  # bounded attempts
        assert channel.connect.await_count == 2
    print(
        "PASS isolated/stale errors ignored; persistent failures reconnect; cooldown, cap and outage handling"
    )


asyncio.run(main())
