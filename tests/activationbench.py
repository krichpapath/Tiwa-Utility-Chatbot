"""Offline acceptance for activated recording and paid transcription boundary."""

import asyncio
import base64
import io
import os
from pathlib import Path
import sys
from unittest.mock import patch
from collections import deque
from types import SimpleNamespace
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa.listening import Gate, Ears, command_from_transcript
from tiwa import stt

frame = np.ones(1280, dtype=np.int16) * 1000
gate = Gate(silence=0.4, maximum=3)
for _ in range(100):
    assert gate.feed(frame, False, True) is None
assert len(gate.pre) == 25 and gate.clip is None
assert gate.feed(frame, True, True) is None and gate.clip is None  # mid-sentence
for _ in range(10):
    gate.feed(frame, False, False)
assert gate.feed(frame, True, True) is None
for _ in range(20):
    assert gate.feed(frame, False, True) is None
assert gate.feed(frame, False, False) is None
assert gate.feed(frame, False, True) is None  # a short pause does not cut
clip = None
for _ in range(7):
    result = gate.feed(frame, False, False)
    if result is not None:
        clip = result
assert clip is not None and len(clip) > 2 * 16000
assert gate.clip is None
gate = Gate(maximum=2)
gate.feed(frame, True, True)
assert any(gate.feed(frame, False, True) is not None for _ in range(30))
other = Gate()
assert other.feed(frame, False, True) is None  # another speaker never activates
assert command_from_transcript("Hey Tiwa, play Judas") == "play Judas"
assert command_from_transcript("Hey, Tiwa, play Judas") == "play Judas"
assert command_from_transcript("เฮ้, ที่ว่า เปิดเพลง") == "เปิดเพลง"
assert command_from_transcript("เฮ้ ชิว่า เปิดเพลง") == "เปิดเพลง"
assert command_from_transcript("เฮ้ ทีวีหยุดเพลงให้หน่อย") == "หยุดเพลงให้หน่อย"
assert command_from_transcript("เมื่อกี้พูดว่า เฮ้ ทีวีหยุดเพลงให้หน่อย") is None
assert command_from_transcript("ทีวีหยุดเพลงให้หน่อย") is None
for text in [
    "I said Hey Tiwa play Judas",
    "โอ๊ย กำลังโดนยิง Hey Tiwa เปิดเพลง",
    "Tiwa play music",
    "Hey teacher play music",
    "Hey Tiwa",
]:
    assert not command_from_transcript(text), text

calls = []


class Response:
    is_error = False

    def json(self):
        return {"text": "ทิวา เปิดเพลง", "usage": {"cost": 0.001}}


def post(url, **kw):
    calls.append(kw)
    with wave.open(io.BytesIO(base64.b64decode(kw["json"]["input_audio"]["data"]))) as wav:
        assert wav.getnchannels() == 1 and wav.getframerate() == 16000
    return Response()


with patch.dict(os.environ, {"OPENROUTER_API_KEY": "fixture", "TIWA_STT_MODEL": ""}):
    with patch.object(stt.httpx, "post", post):
        assert stt.transcribe(frame / 32768, 16000) == "ทิวา เปิดเพลง"
        assert calls[-1]["json"]["model"] == stt.MODELS[0]
        with patch.dict(os.environ, {"TIWA_STT_MODEL": stt.MODELS[1]}):
            stt.transcribe(frame / 32768, 16000)
            assert calls[-1]["json"]["model"] == stt.MODELS[1]
        assert stt.transcribe([], 16000) == "" and len(calls) == 2
        try:
            stt.transcribe([float("nan")], 16000)
            assert False
        except ValueError:
            pass


async def missing_model():
    with patch.dict(os.environ, {"TIWA_WAKE_MODEL": ""}):
        try:
            Ears(None, asyncio.get_running_loop())
            assert False, "missing wake model must fail closed"
        except ValueError:
            pass


asyncio.run(missing_model())


# Exercise the actual Discord PCM adapter with deterministic detector output.
class Detector:
    def __init__(self):
        self.vad = SimpleNamespace(prediction_buffer=deque(maxlen=10))

    def predict(self, audio):
        level = int(np.max(audio))
        self.vad.prediction_buffer.append(float(level > 0))
        return {"hey_tiwa": 0.9 if level == 2000 else 0.0}

    def reset(self):
        self.vad.prediction_buffer.clear()


ears = object.__new__(Ears)
ears.first, ears.factory = Detector(), Detector
ears.speakers = {}
ears.threshold, ears.silence, ears.maximum = 0.6, 0.4, 3


def pcm(level):
    return (np.ones(7680, dtype="<i2") * level).tobytes()


assert ears.process(1, "same name", pcm(1000), 0) is None
assert ears.process(2, "same name", pcm(2000), 0) is None
assert ears.speakers[1][1].clip is None
assert ears.speakers[2][1].clip is not None
for i in range(1, 21):
    assert ears.process(2, "same name", pcm(1000), i * 0.08) is None
result = ears.process(2, "2", b"", 2.6)
assert result is not None and result[0] == "same name"
assert ears.speakers[1][1].clip is None
assert np.max(result[1]) < 0.1  # int16 normalized to float32


async def failures():
    messages = []

    async def callback(*args, **kwargs):
        messages.append((args, kwargs))

    ears.on_text = callback
    ears.pending = {2}
    ears.failures, ears.paused = {}, set()
    ears.resume_at = {}
    with patch.object(stt, "transcribe", side_effect=RuntimeError("fixture")):
        await ears.deliver(2, "QA", frame)
    assert messages[0][1]["error"] and not ears.pending
    messages.clear()
    with patch.object(stt, "transcribe", return_value="I said Hey Tiwa play music"):
        await ears.deliver(2, "QA", frame)
    assert messages[0][0] == ("QA", "I said Hey Tiwa play music"), (
        "local activation must not be vetoed by STT"
    )
    assert 2 not in ears.paused
    await ears.failed(2, "QA", "service failure")
    await ears.failed(2, "QA", "second service failure")
    assert 2 in ears.paused
    with patch.object(stt, "transcribe") as transcribe:
        await ears.deliver(2, "QA", frame)
        transcribe.assert_not_called()
    ears.paused.clear()
    ears.failures.clear()
    messages.clear()
    with patch.object(stt, "transcribe", return_value="Hey Tiwa, play Judas"):
        await ears.deliver(2, "QA", frame)
    assert messages[0][0] == ("QA", "play Judas") and messages[0][1]["activated"]

    async def broken_callback(*args, **kwargs):
        raise RuntimeError("Discord unavailable")

    ears.on_text = broken_callback
    with patch.object(stt, "transcribe", return_value="Hey Tiwa, play Judas"):
        await ears.deliver(2, "QA", frame)
    assert 2 in ears.paused and not ears.pending


asyncio.run(failures())
print(
    "PASS: idle isolation, preroll, silence, pause, maximum, speaker isolation, model selection, WAV, invalid audio, missing detector"
)


async def queued_commands():
    ears.commands = asyncio.Queue(maxsize=5)
    ears.paused.clear()
    ears.failures.clear()
    seen = []
    active = 0

    async def reply(name, command, **kwargs):
        nonlocal active
        active += 1
        assert active == 1, "commands must not overlap"
        await asyncio.sleep(0.01)
        seen.append(command)
        active -= 1

    ears.on_text = reply
    worker = asyncio.create_task(ears.run_commands())
    transcripts = [
        "เฮ้ คิวอ่ะ เปิดเพลงโซอีเตอร์ให้หน่อย",
        "unrelated speech",
        "เฮ้ ทีวีหยุดเพลงให้หน่อย",
        "Hey Tiwa, hello",
    ]
    with patch.object(stt, "transcribe", side_effect=transcripts) as transcribe:
        for _ in transcripts:
            ears.commands.put_nowait((2, "QA", frame))
        await asyncio.wait_for(ears.commands.join(), timeout=3)
        assert transcribe.call_count == 4
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)
    assert seen == ["เปิดเพลงโซอีเตอร์ให้หน่อย", "unrelated speech", "หยุดเพลงให้หน่อย", "hello"], seen
    assert not ears.paused
    ears.paused.add(2)
    ears.resume_at[2] = 0
    with patch.object(stt, "transcribe", return_value="Hey Tiwa, resumed"):
        await ears.deliver(2, "QA", frame)
    assert seen[-1] == "resumed" and not ears.paused
    print("PASS sequential commands, name variant, STT mismatch recovery, automatic resume")


asyncio.run(queued_commands())

for name in ["ที่ว่า", "ทีวา", "ตีวา", "ธีวา", "ชีว่า", "คีวา", "Tiwa", "Teeva"]:
    assert command_from_transcript(f"Hey {name} play music") == "play music", name
assert command_from_transcript("We said Hey Tiwa play music") is None
assert command_from_transcript("Hey teacher play music") is None
print("PASS similar name spellings with anchored greeting")


async def local_activation_authority():
    ears.paused.clear()
    messages = []

    async def callback(name, text, **kwargs):
        messages.append(text)

    ears.on_text = callback
    with patch.object(stt, "transcribe", side_effect=["เปิดเพลงให้หน่อย", "Hey Tiwa", "", "   "]):
        for _ in range(4):
            await ears.deliver(2, "QA", frame)
    assert messages == ["เปิดเพลงให้หน่อย", "Hey Tiwa"]
    print("PASS local wake authorizes missing/misheard greeting; empty STT stays silent")


asyncio.run(local_activation_authority())

# Interpreter shutdown may finalize the sink after asyncio closes its loop.
closed_loop = asyncio.new_event_loop()
finalized = object.__new__(Ears)


async def finished():
    pass


finalized.task = closed_loop.create_task(finished())
closed_loop.run_until_complete(finalized.task)
closed_loop.close()
finalized.cleanup()
finalized.cleanup()
assert finalized.task is None
print("PASS repeated cleanup after event loop shutdown")
