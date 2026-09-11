"""Local wake gating; only activated clips may reach paid transcription.

Requires a custom openWakeWord ONNX model trained for Hey Tiwa. No generic
model or transcript spelling is substituted when that model is unavailable.
"""
import asyncio
from collections import deque
import os
from pathlib import Path
import queue
import re
import time

import numpy as np
from discord.ext import voice_recv

from . import stt

FOLLOWUPS = {}  # requester id -> deadline for one wake-free response


def command_from_transcript(text):
    """Optionally strip a recognizable opening greeting; never an activation gate."""
    greeting = re.match(r'^\s*["“\']?\s*(?:hey[\s,!.:—-]+|เฮ้ย?[\s,!.:—-]*)', text, re.IGNORECASE)
    if not greeting:
        return None
    tail = text[greeting.end():]
    # Match variants without rewriting any words in the actual command.
    name = r'(?:t(?:i|ee|e)[wv](?:a|ah)\b|ทิวา|ทีวี|คิวอ่ะ|คิวอะ|ชิว่า|ชิวา|ชื่อว่า|ที่ว่า|[ทตธชคซ][ิีื][่้๊๋]?ว[่้๊๋]?[าอี][่้๊๋]?ะ?)'
    match = re.match(name + r'[\s,!.:—-]*(.*)$', tail, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else None



def number(key, default, low, high):
    try:
        value = float(os.getenv(key) or default)
    except ValueError:
        raise ValueError(f"{key} must be a number") from None
    if not low <= value <= high:
        raise ValueError(f"{key} must be between {low} and {high}")
    return value


class Gate:
    """One speaker; 80 ms mono 16 kHz frames, detector supplied separately."""
    def __init__(self, silence=1.2, maximum=20):
        self.silence, self.maximum = silence, maximum
        self.pre = deque(maxlen=25)  # two seconds, including wake detection delay
        self.clip = None
        self.elapsed = self.quiet = 0.0
        self.utterance = self.idle_quiet = 0.0

    def feed(self, frame, wake, speech):
        self.pre.append(frame.copy())
        if self.clip is None:
            if speech:
                if self.utterance == 0:
                    # Keep onset padding, but not the previous finished utterance.
                    self.pre = deque(list(self.pre)[-4:], maxlen=25)
                self.utterance += .08
                self.idle_quiet = 0
            else:
                self.idle_quiet += .08
                if self.idle_quiet >= self.silence:
                    self.utterance = 0
            # Acoustic boundary: do not wake late in an ongoing utterance.
            # The local detector is the only activation gate.
            if not wake or self.utterance > 2:
                return None
            self.clip = list(self.pre)
            self.elapsed = self.quiet = 0.0
            return None
        self.clip.append(frame.copy())
        self.elapsed += .08
        self.quiet = 0.0 if speech else self.quiet + .08
        if self.elapsed < self.maximum and (self.elapsed < 1.5 or self.quiet < self.silence):
            return None
        result = np.concatenate(self.clip)
        self.clip = None
        self.pre.clear()
        # A max-length cut must not re-arm during the same uninterrupted speech.
        self.utterance = 3 if self.quiet < self.silence else 0
        self.idle_quiet = self.quiet
        return result


class Ears(voice_recv.AudioSink):
    def __init__(self, on_text, loop):
        path = Path(os.getenv("TIWA_WAKE_MODEL") or "")
        if not path.is_file() or path.suffix != ".onnx":
            raise ValueError("set TIWA_WAKE_MODEL to a trained Hey Tiwa .onnx file; listening stays off")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # worker thread: safe to load native libraries
        else:
            raise RuntimeError("wake model must load in a worker; use await voice.listen()")
        try:
            from openwakeword.model import Model
        except ImportError:
            raise RuntimeError("install optional openwakeword dependency before enabling listening") from None
        self.threshold = number("TIWA_WAKE_THRESHOLD", .9, .01, 1)
        self.silence = number("TIWA_SILENCE_S", 1.2, .3, 5)
        self.maximum = number("TIWA_RECORD_MAX_S", 20, 2, 30)
        self.factory = lambda: Model(wakeword_models=[str(path.resolve())], inference_framework="onnx",
                                     vad_threshold=.5)
        # Validate models/dependencies now, before Discord starts delivering audio.
        self.first = self.factory()
        super().__init__()
        self.on_text = on_text
        self.frames = queue.Queue(maxsize=500)
        self.speakers = {}
        self.pending = set()
        self.commands = asyncio.Queue(maxsize=5)
        self.resume_at = {}
        self.last_outcome = "No voice request yet"
        self.failures = {}
        self.paused = set()
        # Construction runs in a worker; bind/start asyncio work only after attachment.
        self.task = None

    def start(self, loop):
        self.task = loop.create_task(self.watch())

    def wants_opus(self):
        return False

    def write(self, user, data):
        if user is None or getattr(user, "bot", False):
            return
        try:
            self.frames.put_nowait((user.id, user.display_name, bytes(data.pcm), time.monotonic()))
        except queue.Full:
            pass  # bounded receiver queue: worker discards stale audio, never uploads idle audio

    def process(self, uid, name, pcm, now):
        if uid not in self.speakers:
            if len(self.speakers) >= 8:
                return None
            detector = self.first or self.factory()
            self.first = None
            self.speakers[uid] = [detector, Gate(self.silence, self.maximum), bytearray(), now, name]
        state = self.speakers[uid]
        detector, gate, buf, last, name = state
        # Fill missing packets with silence, bounded by one maximum command.
        if now - last > .16:
            buf.extend(b'\0' * (min(int((now-last)/.08), 400) * 15360))
        state[3] = now
        buf.extend(pcm)
        while len(buf) >= 15360:  # 80 ms of Discord's stereo int16 48 kHz
            stereo = np.frombuffer(bytes(buf[:15360]), dtype='<i2').reshape(-1, 2)
            del buf[:15360]
            # Three-sample box filter before exact 48 -> 16 kHz decimation.
            frame = stereo.astype(np.float32).mean(axis=1).reshape(-1, 3).mean(axis=1).astype(np.int16)
            scores = detector.predict(frame)
            triggered = max(scores.values(), default=0) >= self.threshold
            speech = max(list(detector.vad.prediction_buffer)[-3:], default=0) >= .5
            if speech and gate.clip is None and FOLLOWUPS.get(uid, 0) > time.monotonic():
                FOLLOWUPS.pop(uid, None)
                triggered = True
                gate.utterance = 0
            clip = gate.feed(frame, triggered, speech)
            if clip is not None:
                detector.reset()
                return name, clip.astype(np.float32) / 32768
        return None

    async def deliver(self, uid, name, clip):
        if uid in self.paused:
            if time.monotonic() < self.resume_at.get(uid, float("inf")):
                self.pending.discard(uid)
                return
            self.paused.discard(uid)
            self.failures.pop(uid, None)
        try:
            self.last_outcome = "Transcribing"
            text = await asyncio.to_thread(stt.transcribe, clip, 16000)
            # Acoustic activation already passed. STT spelling cannot veto it.
            # Keep a bare greeting too, so Tiwa can acknowledge being called.
            command = command_from_transcript(text) or text.strip()
            if command:
                self.last_outcome = "Answering / executing command"
                await asyncio.wait_for(self.on_text(name, command, activated=True, user_id=uid), timeout=90)
                self.failures.pop(uid, None)
                self.last_outcome = "Command finished"
                print(f"[voice] command delivered for speaker {uid}")
            else:
                self.last_outcome = "No speech returned by transcription"
                print("[voice] empty transcript; listener remains active")
            # Empty transcripts are intentionally silent. No retry,
            # no user lockout, and no command execution.
        except Exception as error:
            self.last_outcome = f"Command failed: {type(error).__name__}"
            print(f"[voice] activated clip failed: {type(error).__name__}")
            try:
                await self.failed(uid, name, "Voice command failed. No automatic retry was made. Check the bot console.")
            except Exception:
                self.paused.add(uid)
                self.resume_at[uid] = time.monotonic() + 30
                print(f"[voice] cannot reach text channel; paid listening paused for speaker {uid}. Retry after 30 seconds.")
        finally:
            self.pending.discard(uid)

    async def failed(self, uid, name, reason):
        self.failures[uid] = self.failures.get(uid, 0) + 1
        if self.failures[uid] >= 2:
            self.paused.add(uid)
            self.resume_at[uid] = time.monotonic() + 30
            reason += " Paid listening is paused for 30 seconds after two service failures, then resumes automatically."
        print(f"[voice] speaker {uid}: {reason}")
        await asyncio.wait_for(self.on_text(name, "", activated=True, error=reason), timeout=10)

    async def run_commands(self):
        while True:
            uid, name, clip = await self.commands.get()
            try:
                await self.deliver(uid, name, clip)
            finally:
                self.commands.task_done()

    async def watch(self):
        worker = asyncio.create_task(self.run_commands())
        try:
            while True:
                await asyncio.sleep(.02)
                now = time.monotonic()
                batch = []
                for _ in range(100):
                    try:
                        batch.append(self.frames.get_nowait())
                    except queue.Empty:
                        break
                seen = {item[0] for item in batch}
                for uid, state in list(self.speakers.items()):
                    if now-state[3] > 60:
                        del self.speakers[uid]
                    elif uid not in seen and state[1].clip is not None and now-state[3] > .16:
                        batch.append((uid, str(uid), b'', now))
                for uid, name, pcm, stamp in batch:
                    if now-stamp > 2 or (uid in self.paused and now < self.resume_at.get(uid, 0)):
                        continue
                    try:
                        result = await asyncio.to_thread(self.process, uid, name, pcm, stamp)
                        if result:
                            try:
                                self.commands.put_nowait((uid, *result))
                                print(f"[voice] command queued for speaker {uid} ({self.commands.qsize()} waiting)")
                            except asyncio.QueueFull:
                                # No STT charge for an overflowed clip.
                                print("[voice] command queue full; clip skipped without transcription charge")
                    except Exception as error:
                        self.speakers.pop(uid, None)
                        print(f"[voice] local detector failed: {type(error).__name__}")
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    def cleanup(self):
        task = getattr(self, "task", None)
        if task:
            self.task = None
            loop = task.get_loop()
            if not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(task.cancel)
                except RuntimeError:
                    pass  # loop can close between the check and finalizer callback
