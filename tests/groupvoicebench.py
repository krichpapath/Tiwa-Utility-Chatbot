"""Group voice acceptance: synthetic PCM and fake STT; no network or Discord posts."""

import asyncio
from collections import deque
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import patch
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa.listening import Ears
from tiwa import stt


class Detector:
    def __init__(self):
        self.vad = Obj(prediction_buffer=deque(maxlen=10))

    def predict(self, frame):
        level = int(np.max(frame))
        self.vad.prediction_buffer.append(float(level > 0))
        return {"wake": float(level == 2000)}

    def reset(self):
        self.vad.prediction_buffer.clear()


def fixture():
    ears = object.__new__(Ears)
    ears.first, ears.factory = Detector(), Detector
    ears.speakers = {}
    ears.threshold, ears.silence, ears.maximum = 0.9, 0.4, 3
    ears.commands = asyncio.Queue(maxsize=5)
    ears.pending, ears.paused = set(), set()
    ears.failures, ears.resume_at = {}, {}
    return ears


def pcm(level):
    return np.full(7680, level, dtype="<i2").tobytes()


def overlap():
    ears = fixture()
    clips = {}
    # Two people with the same display name activate while a third keeps talking.
    for tick in range(32):
        for uid in (1, 2, 3):
            level = (2000 if tick == 0 else uid * 300) if uid != 3 else 900
            if tick >= 21 and uid != 3:
                level = 0
            result = ears.process(uid, "same name", pcm(level), tick * 0.08)
            if result:
                clips[uid] = result[1]
    assert set(clips) == {1, 2}, "background speaker must never activate"
    assert np.any(np.isclose(clips[1], 300 / 32768))
    assert not np.any(np.isclose(clips[1], 600 / 32768)), "speaker audio mixed"
    assert np.any(np.isclose(clips[2], 600 / 32768))
    assert not np.any(np.isclose(clips[2], 900 / 32768)), "background leaked into command"
    print("PASS overlapping speakers, duplicate names, independent silence, background isolation")


async def ordering():
    ears = fixture()
    entered, release = asyncio.Event(), asyncio.Event()
    replies, errors = [], []
    active = 0

    async def reply(name, command, **kwargs):
        nonlocal active
        if kwargs.get("error"):
            errors.append(name)
            return
        active += 1
        assert active == 1, "overlapping command execution"
        if command == "first":
            entered.set()
            await release.wait()
        replies.append((name, command))
        active -= 1

    ears.on_text = reply
    responses = [
        "Hey Tiwa first",
        "Hey Tiwa second",
        RuntimeError("provider offline"),
        "unrelated conversation",
        "Hey Tiwa third",
        "Hey Tiwa fourth",
    ]
    worker = asyncio.create_task(ears.run_commands())
    with patch.object(stt, "transcribe", side_effect=responses) as transcribe:
        ears.commands.put_nowait((1, "A", np.zeros(1280)))
        await asyncio.wait_for(entered.wait(), 2)
        for uid, name in [(2, "B"), (1, "A"), (3, "C"), (2, "B"), (1, "A")]:
            ears.commands.put_nowait((uid, name, np.zeros(1280)))
        try:
            ears.commands.put_nowait((4, "overflow", np.zeros(1280)))
        except asyncio.QueueFull:
            pass
        else:
            raise AssertionError("queue must remain bounded")
        assert transcribe.call_count == 1, "queued requests must wait"
        release.set()
        await asyncio.wait_for(ears.commands.join(), 3)
        assert transcribe.call_count == 6, "overflow must not incur STT call"
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert replies == [
        ("A", "first"),
        ("B", "second"),
        ("C", "unrelated conversation"),
        ("B", "third"),
        ("A", "fourth"),
    ], replies
    assert errors == ["A"] and not ears.paused
    print(
        "PASS sequential group commands, five waiting, overflow unpaid, failure/rejection recovery"
    )


if __name__ == "__main__":
    overlap()
    asyncio.run(ordering())
