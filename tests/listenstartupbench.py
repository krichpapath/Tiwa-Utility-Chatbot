"""Slow wake model startup must not block Discord's event loop."""

import asyncio
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import voice, listening


async def main():
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    built = []
    attached = []
    cleaned = []
    ticks = []

    class SlowSink:
        def __init__(self, *args):
            assert threading.get_ident() != main_thread
            time.sleep(0.3)
            built.append(self)

        def start(self, loop):
            assert threading.get_ident() == main_thread

        def cleanup(self):
            cleaned.append(self)

    vc = Obj(is_listening=lambda: bool(attached), is_connected=lambda: True, listen=attached.append)
    guild = Obj(voice_client=vc)

    async def heartbeat():
        for _ in range(12):
            await asyncio.sleep(0.02)
            ticks.append(loop.time())

    with patch.object(voice, "LISTEN", True), patch.object(listening, "Ears", SlowSink):
        results = await asyncio.gather(
            voice.listen(guild, None, loop), voice.listen(guild, None, loop), heartbeat()
        )
        assert results[:2] == ["listening", "listening"]
        assert len(built) == len(attached) == 1
        assert max(b - a for a, b in zip(ticks, ticks[1:])) < 0.15
        attached.clear()
        job = asyncio.create_task(voice.listen(guild, None, loop))
        await asyncio.sleep(0.05)
        guild.voice_client = None
        assert "changed" in await job
        assert not attached and cleaned
        guild.voice_client = vc
        with patch.object(listening, "Ears", side_effect=RuntimeError("model broken")):
            assert "model broken" in await voice.listen(guild, None, loop)
        assert await voice.listen(guild, None, loop) == "listening"
    print(
        "PASS heartbeat responsive, concurrent starts build once, leave during load, failure then retry"
    )


async def real_start():
    loop = asyncio.get_running_loop()
    attached = []
    vc = Obj(is_listening=lambda: bool(attached), is_connected=lambda: True, listen=attached.append)
    guild = Obj(voice_client=vc)
    gaps = []

    async def heartbeat():
        last = loop.time()
        while True:
            await asyncio.sleep(0.05)
            now = loop.time()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    start = loop.time()
    try:
        with patch.object(voice, "LISTEN", True):
            assert await voice.listen(guild, None, loop) == "listening"
        assert len(attached) == 1 and attached[0].task is not None
        print(
            f"PASS real voice.listen startup {loop.time() - start:.2f}s; {len(gaps)} heartbeat ticks; max pause {max(gaps, default=0):.3f}s",
            flush=True,
        )
        assert gaps and max(gaps) < 1
    finally:
        for sink in attached:
            sink.cleanup()
        beat.cancel()
        await asyncio.gather(beat, return_exceptions=True)


# Catch callers forgetting to await startup, including recovery paths.
import ast

source = ast.parse((Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8"))
parents = {child: parent for parent in ast.walk(source) for child in ast.iter_child_nodes(parent)}
for node in ast.walk(source):
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "voice"
        and node.func.attr == "listen"
    ):
        assert isinstance(parents[node], ast.Await), "voice.listen must be awaited"


async def guarded():
    with (
        patch.object(listening.Path, "is_file", return_value=True),
        patch.dict("os.environ", {"TIWA_WAKE_MODEL": "fake.onnx"}),
    ):
        try:
            listening.Ears(None, asyncio.get_running_loop())
        except RuntimeError as error:
            assert "worker" in str(error)
        else:
            raise AssertionError("constructor allowed model loading on the event loop")
    await main()


asyncio.run(real_start() if "--real" in sys.argv else guarded())
