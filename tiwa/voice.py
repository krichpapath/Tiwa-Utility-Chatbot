"""Voice channel: join/leave (G5) and listening (G6).

G6 rule: transcribe EVERYTHING into a chatlog, run no model. Whisper is cheap,
the LLM is not. Waking her on the wake word is G7.
"""
import asyncio
import difflib
import os
import re
import shutil
import time
import traceback
from pathlib import Path

import numpy as np
from discord.ext import voice_recv

from . import llm

llm.load_env()  # our settings below are read at import — do not rely on import order

SAMPLE_RATE = 48000  # what Discord sends: 48kHz, 16-bit, stereo
# Real speech has pauses. Cutting at 0.8s chops sentences into fragments and
# Whisper is far worse on fragments than on whole sentences.
SILENCE_S = float(os.environ.get("TIWA_SILENCE_S", "1.2"))
MIN_UTTERANCE_S = 0.4  # shorter than this is a cough or a keyboard
MAX_UTTERANCE_S = 20  # an open mic never goes quiet — cut anyway and move on

# Auto-detection on a short noisy clip guesses the language wrong and then
# transcribes confident nonsense. Pin it if you know what you speak.
WHISPER_LANG = os.environ.get("TIWA_WHISPER_LANG") or None  # "th" | "en" | None
# Writes every heard utterance to data/voice_debug/*.wav so you can listen to
# exactly what she got. Turn on when transcripts look insane.
VOICE_DEBUG = os.environ.get("TIWA_VOICE_DEBUG", "0") != "0"

# Noise gate. Measured on this setup (scripts in PLAN.md):
#   speech 0.09-0.11 · speech at 25% volume 0.023 · fan hum 0.021
#   room hiss 0.002 · digital silence 0.000
# 0.02 sits in the gap. Raise it if she still hears ghosts, lower it if she
# misses you when speaking quietly. Every drop is printed with its level.
NOISE_FLOOR = float(os.environ.get("TIWA_NOISE_FLOOR", "0.02"))

# Whisper's greatest hits when handed noise. Cheap veto, cheaper than the LLM.
_GHOSTS = ("thanks for watching", "thank you for watching", "thanks for the video",
           "subscribe", "ขอบคุณที่รับชม", "ขอบคุณครับ", "ขอบคุณค่ะ", "you", "bye")


def rms(audio) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0


def is_junk(text: str) -> bool:
    """Repetition loops and Whisper's stock hallucinations."""
    t = text.strip()
    low = t.lower().strip(" .!?,")
    if not t or low in _GHOSTS:
        return True
    words = t.split()
    if len(words) >= 4 and len(set(words)) <= 2:  # "no no no no"
        return True
    flat = t.replace(" ", "")  # Thai has no spaces, so check characters too
    for n in range(1, 7):
        unit, reps = flat[:n], len(flat) // n
        if reps >= 5 and unit * reps == flat[: n * reps]:
            return True
    return False

# faster-whisper is NOT usable here: Smart App Control (enforced on this machine)
# blocks ctranslate2.dll for being unsigned. onnx-asr runs on Microsoft-signed
# onnxruntime, so it loads. Measured on this CPU (see PLAN.md):
#   base  ~1-2x realtime, English good, Thai rough
#   small ~2.8-3.8x realtime, Thai good
# int8 quantization measured SLOWER, do not bother.
WHISPER_MODEL = os.environ.get("TIWA_WHISPER_MODEL", "onnx-community/whisper-base")
# 4 is the measured sweet spot on this 10-core CPU; more threads is SLOWER.
ONNX_THREADS = int(os.environ.get("TIWA_ONNX_THREADS", "4"))

_model = None


def _whisper():
    """Loaded once, lazily — first call also downloads the models.

    HF_HUB_DISABLE_XET: the hf_xet accelerator ships an unsigned DLL that Smart
    App Control blocks, which makes some model downloads fail outright. Plain
    HTTP works fine and is only slower once.

    VAD is NOT optional. Without it Whisper invents words out of nothing:
    measured "Thanks for watching!" from pure silence, from room hiss and from
    60Hz fan hum — which would spam the chatlog and could false-trigger the
    wake word. With Silero in front, all three return empty.
    """
    global _model
    if _model is None:
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        import onnx_asr
        import onnxruntime as ort

        # Thread count matters more than anything else here. Measured on a 3s
        # Thai clip with whisper-base: 4 threads 0.58s, default(auto) 1.32s,
        # 16 threads 2.18s. onnxruntime oversubscribes and thrashes.
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = ONNX_THREADS
        # CPU only, explicitly: the GPU belongs to the persona model (or to your
        # game). Never let onnxruntime quietly pick a CUDA provider.
        cpu = ["CPUExecutionProvider"]
        _model = onnx_asr.load_model(
            WHISPER_MODEL, providers=cpu, sess_options=opts
        ).with_vad(onnx_asr.load_vad(providers=cpu))
    return _model


def to_audio(pcm: bytes) -> np.ndarray:
    """Discord stereo int16 -> mono float32. onnx-asr resamples for us."""
    a = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    if a.size % 2:
        a = a[:-1]
    return a.reshape(-1, 2).mean(axis=1)


def transcribe(audio, rate: int = SAMPLE_RATE) -> str:
    """audio: mono float32. Returns "" when there is no speech — that is the VAD.

    Language is auto-detected unless TIWA_WHISPER_LANG pins it.
    """
    kw = {"language": WHISPER_LANG} if WHISPER_LANG else {}
    segments = _whisper().recognize(audio, sample_rate=rate, **kw)
    return " ".join(s.text.strip() for s in segments).strip()


def safe_filename(s: str, limit: int = 30) -> str:
    r"""Windows rejects  < > : " / \ | ? *  and trailing dots or spaces.

    A transcript goes straight into the debug filename, so "Hello how are you?"
    used to raise OSError and take the whole listener down with it.
    """
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s)
    s = re.sub(r"\s+", " ", s).strip()[:limit].strip(" .")
    return s or "nothing"


def dump(audio, rate: int, name: str, text: str):
    """Save what she heard, so you can listen and judge audio vs model.

    Debug aids must never be able to break listening — hence the catch-all.
    """
    import wave

    try:
        d = Path(__file__).parents[1] / "data" / "voice_debug"
        d.mkdir(parents=True, exist_ok=True)
        stamp = f"{time.strftime('%H%M%S')}{int(time.time() * 1000) % 1000:03d}"
        base = f"{stamp}_{safe_filename(name, 20)}_{safe_filename(text)}"
        # never overwrite: back-to-back quiet clips all sanitize to "nothing",
        # and the clock is not fine-grained enough to separate them
        path, n = d / f"{base}.wav", 1
        while path.exists():
            path, n = d / f"{base}_{n}.wav", n + 1
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        print(f"[voice] saved {path.name}")
    except Exception as e:
        print(f"[voice] could not save debug wav: {type(e).__name__}: {e}")


_seen_reasons = {}


def _why(reason: str):
    """Say each distinct failure once, then count it. Per-packet logging at
    50 packets/second would be useless."""
    n = _seen_reasons.get(reason, 0) + 1
    _seen_reasons[reason] = n
    if n == 1 or n % 500 == 0:
        print(f"[voice] undecryptable audio — {reason} (x{n})")


def enable_resilient_router() -> bool:
    """One undecodable packet must not deafen her.

    voice_recv's PacketRouter.run() wraps the whole loop and does
    `finally: stop_listening()`, so a single OpusError ends the session and
    drops the voice connection. Same loop, but a bad packet is skipped.

    Remove if voice_recv ever handles per-packet errors itself.
    """
    from discord.ext.voice_recv import router as rt

    if getattr(rt.PacketRouter, "_tiwa_resilient", False):
        return False

    def _do_run(self) -> None:
        while not self._end_thread.is_set():
            self.waiter.wait()
            with self._lock:
                for decoder in self.waiter.items:
                    try:
                        data = decoder.pop_data()
                    except Exception as e:
                        _why(f"opus rejected a packet: {type(e).__name__}: {e}")
                        continue  # drop this frame, keep her ears open
                    if data is not None:
                        self.sink.write(data.source, data)

    rt.PacketRouter._do_run = _do_run
    rt.PacketRouter._tiwa_resilient = True
    return True


def enable_dave_decrypt() -> bool:
    """Teach discord-ext-voice-recv to decrypt Discord's E2EE (DAVE).

    Discord made DAVE mandatory for non-stage voice on 2026-03-02: declaring
    `max_dave_protocol_version = 0` now gets the connection rejected outright
    with close code 4017. So opting out is not an option.

    voice_recv (0.5.2a179) has no DAVE support, so it hands opus the still-
    encrypted payload -> `OpusError: corrupted stream` -> its router does
    `finally: stop_listening()` and her ears are dead.

    discord.py already maintains the DAVE session (it needs it to SEND audio),
    and `davey.DaveSession.decrypt(user_id, media_type, packet)` is right there.
    So: after voice_recv finishes transport decryption, run the payload through
    the DAVE session. E2EE stays fully on for everyone in the call.

    Remove this once voice_recv ships DAVE support of its own.
    """
    import davey
    from discord.ext.voice_recv import reader as vr

    if getattr(vr.AudioReader, "_tiwa_dave", False):
        return False
    original_init = vr.AudioReader.__init__

    def __init__(self, sink, voice_client, **kw):
        original_init(self, sink, voice_client, **kw)
        transport_decrypt = self.decryptor.decrypt_rtp  # bound in ITS __init__

        def decrypt_rtp(packet):
            data = transport_decrypt(packet)
            session = getattr(voice_client._connection, "dave_session", None)
            if session is None or not session.ready:
                _why("dave session not ready")  # then opus WILL reject this packet
                return data
            user_id = voice_client._get_id_from_ssrc(packet.ssrc)
            if not user_id:
                _why("ssrc not mapped to a user yet")
                return data
            try:
                out = session.decrypt(user_id, davey.MediaType.audio, data)
            except Exception as e:
                _why(f"dave decrypt raised: {type(e).__name__}: {e}")
                return data
            if not out:
                _why("dave decrypt returned nothing")
                return data
            return out

        self.decryptor.decrypt_rtp = decrypt_rtp

    vr.AudioReader.__init__ = __init__
    vr.AudioReader._tiwa_dave = True
    return True


enable_resilient_router()  # both must run before any listening starts
enable_dave_decrypt()


async def join(author) -> str:
    """Join the voice channel `author` is sitting in. Returns what to say back."""
    ch = getattr(getattr(author, "voice", None), "channel", None)
    if ch is None:
        return "you're not in a voice channel"
    vc = author.guild.voice_client
    try:
        if vc is not None:
            await vc.move_to(ch)
        else:
            await ch.connect(cls=voice_recv.VoiceRecvClient)
    except Exception as error:
        return f"couldn't join voice: {type(error).__name__}; check connection and channel permissions"
    return f"joined {ch.name}"


async def leave(guild) -> str:
    vc = getattr(guild, "voice_client", None)
    if vc is None:
        return "not in a voice channel"
    try:
        await vc.disconnect()
    except Exception as error:
        return f"couldn't leave voice: {type(error).__name__}"
    return "left"


class Ears(voice_recv.AudioSink):
    """Buffers each speaker separately, cuts on silence, hands text to `on_text`.

    One stream per speaker is the point: she needs to know WHO said it, which is
    what the per-user memory keys off.
    """

    def __init__(self, on_text, loop):
        super().__init__()
        self.on_text = on_text  # called (speaker_name, text) from the bot's loop
        self.loop = loop
        self.buf = {}  # name -> bytearray
        self.last = {}  # name -> time of last packet
        self.task = loop.create_task(self._watch())

    def wants_opus(self) -> bool:
        return False  # give us decoded PCM, not opus frames

    def write(self, user, data):
        if user is None:
            return
        name = getattr(user, "display_name", None) or str(user)
        self.buf.setdefault(name, bytearray()).extend(data.pcm)
        self.last[name] = time.monotonic()

    async def _watch(self):
        """Every 0.3s: whoever went quiet long enough gets their utterance cut.

        Wrapped so nothing can end this task. If it dies she stops hearing
        everyone, silently, until the bot restarts.
        """
        while True:
            await asyncio.sleep(0.3)
            try:
                await self._sweep()
            except asyncio.CancelledError:
                raise  # leave() is allowed to stop us
            except Exception:
                print("[voice] listener hiccup, still listening:")
                traceback.print_exc()

    async def _sweep(self):
        """One pass over the speakers; cut and transcribe whoever went quiet."""
        now = time.monotonic()
        for name in list(self.buf):
            if not self.buf[name]:
                continue
            held = len(self.buf[name]) / (SAMPLE_RATE * 4)
            quiet = now - self.last.get(name, 0) >= SILENCE_S
            if not quiet and held < MAX_UTTERANCE_S:
                continue  # still talking
            pcm = bytes(self.buf.pop(name))
            seconds = len(pcm) / (SAMPLE_RATE * 2 * 2)  # stereo, 2 bytes/sample
            if seconds < MIN_UTTERANCE_S:
                continue
            audio = to_audio(pcm)
            level = rms(audio)
            if level < NOISE_FLOOR:
                # gate 1: too quiet to be speech. Printed so you can tune it.
                print(f"[voice] ignored {seconds:.1f}s from {name} "
                      f"(level {level:.4f} < {NOISE_FLOOR})")
                continue
            # to_thread: whisper on CPU would otherwise block the bot
            text = await asyncio.to_thread(transcribe, audio)  # gate 2: VAD
            if VOICE_DEBUG:
                dump(audio, SAMPLE_RATE, name, text)
            if not text:
                continue
            if is_junk(text):  # gate 3: loops and stock hallucinations
                print(f"[voice] ignored junk from {name} (level {level:.4f}): {text!r}")
                continue
            # her turn must never be able to kill our ears: a raise here used to
            # end this task, and she would go deaf for the rest of the session
            try:
                await self.on_text(name, text)
            except Exception:
                print(f"[voice] turn failed for {name!r}, still listening:")
                traceback.print_exc()

    def cleanup(self):
        # voice_recv's AudioSink.__del__ calls this, so it runs even when __init__
        # died before its last line and there is no task to cancel. An exception
        # in a destructor is only ever "Exception ignored" noise on stderr.
        task = getattr(self, "task", None)
        if task is not None:
            task.cancel()


# TIWA_VOICE decides how much of this file is live.
#
#   dj   — the voice channel is a SPEAKER for music and nothing else. Ears off,
#          she does not talk out loud, and she does not decide to join or leave
#          mid-conversation. _flush_music still brings her in when a song needs
#          a channel, and `join` / `leave` typed by hand still work.
#   full — ears, TTS and conversational join/leave as well.
#
# Default is dj: everything above the DJ line is either unfinished (Thai STT
# garbles on whisper-base, ~10s on small) or unwanted while she is a text bot.
DJ_ONLY = os.environ.get("TIWA_VOICE", "dj") != "full"

# Listening was already OFF by default for the STT reason above; TIWA_VOICE=dj
# now holds it off regardless, so TIWA_LISTEN=1 alone cannot turn her ears on.
LISTEN = os.environ.get("TIWA_LISTEN", "0") != "0" and not DJ_ONLY


def listen(guild, on_text, loop) -> str:
    if not LISTEN:
        return ("not listening (TIWA_VOICE=dj)" if DJ_ONLY
                else "not listening (TIWA_LISTEN=0)")
    vc = guild.voice_client
    if vc is None:
        return "not in a voice channel"
    vc.listen(Ears(on_text, loop))
    return "listening"


# G7. Whisper NEVER writes "ทิวา" — measured output is "ที่ว่า" / "ที่วับ" on both
# model sizes, and the engine offers no way to bias it toward a name (only
# `language`). So: known spellings anywhere in the line, plus a fuzzy check on
# the opening, because people say her name first. A miss is the expensive
# failure — she just ignores you — so recall beats precision here.
WAKE = ("tiwa", "teewa", "tewa", "tiva", "ทิวา", "ที่ว่า", "ที่วับ", "ที่วา", "ทิว")
# fuzzy fallback, split by script: a character window over Latin text matched
# "the water is cold", and over Thai it matched "ที่บ้าน" — ที่ opens a huge
# number of Thai phrases. So Latin compares whole words, Thai compares exactly
# the first four characters (her name's length) at a stricter threshold.
LATIN_FORMS = ("tiwa", "teewa", "tiva", "tewa")
THAI_FORMS = ("ทิวา", "ทีวา")
WAKE_FUZZ = float(os.environ.get("TIWA_WAKE_FUZZ", "0.72"))
THAI_FUZZ = float(os.environ.get("TIWA_WAKE_FUZZ_TH", "0.8"))


def _like(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


# optional opener before her name. Whisper drops or mangles these constantly,
# so they are allowed but never required.
GREETINGS = ("hey", "hay", "hi", "hello", "yo", "ok", "okay", "เฮ้ย", "เห้ย",
             "เฮ้", "เฮ", "เฮ้ย", "นี่", "โย่")


def _strip_prefix(low: str, raw: str, prefixes) -> tuple:
    """If `low` starts with one of `prefixes`, return (rest_low, rest_raw)."""
    for p in prefixes:
        if low.startswith(p):
            n = len(p)
            while n < len(low) and low[n] in " ,.!?":
                n += 1
            return low[n:], raw[n:]
    return None, None


def wake(text: str):
    """-> the message with 'hey tiwa' stripped, or None if she was not addressed.

    She answers ONLY when a line OPENS with her name (an optional greeting in
    front is fine). Her name later in a sentence is people talking ABOUT her,
    not TO her — "I told tiwa yesterday" is not a command.
    """
    raw = text.strip()
    low = raw.lower()
    if not low:
        return None
    # optional "hey"/"เฮ้" first
    rest_low, rest_raw = _strip_prefix(low, raw, GREETINGS)
    if rest_low is None:
        rest_low, rest_raw = low, raw
    # then her name, however Whisper spelled it
    after_low, after_raw = _strip_prefix(rest_low, rest_raw, WAKE)
    if after_low is not None:
        return _clean_rest(after_raw) or raw
    # fuzzy: same position, for spellings not in the list
    first = (rest_low.split() or [""])[0].strip(",.!?")
    if first and any(_like(first, f) >= WAKE_FUZZ for f in LATIN_FORMS):
        return _clean_rest(rest_raw.split(" ", 1)[1] if " " in rest_raw else "") or raw
    head = rest_low.replace(" ", "")[:4]  # ท-ิ-ว-า
    if any(_like(head, f) >= THAI_FUZZ for f in THAI_FORMS):
        return _clean_rest(rest_raw[4:]) or raw
    return None


def _clean_rest(s: str) -> str:
    s = re.sub(r"\s+([,.!?])", r"\1", s)
    return re.sub(r"\s{2,}", " ", s).strip(" ,.!?ๆฯ")


_LATER = ("later", "tonight", "tomorrow", "sometime", "next week", "in a bit",
          "afterward", "after that", "เดี๋ยว", "พรุ่งนี้", "คืนนี้", "ทีหลัง",
          # Thai was thin enough that "ออกไปตอนดึกนะ" passed the veto and would
          # have hung up on a live call (tests/leavebench.py now asserts these)
          "ตอนดึก", "ตอนเย็น", "ตอนบ่าย", "สักพัก", "อีกที", "วันหลัง", "ค่อย")


def wants_now(text: str) -> bool:
    """Veto on the model's join/leave decision when the ask is about a FUTURE time.

    The tool description alone could not stop 'join us later tonight' from
    joining immediately (4/5 both runs) — the 8B sees 'join' and fires. A wrong
    veto just means you type `join`; a wrong one drags her into, or out of, a
    live call.
    """
    low = text.lower()
    return not any(w in low for w in _LATER)


# ---- G8: she speaks -------------------------------------------------------
# No ffmpeg anywhere: edge-tts gives mp3, soundfile decodes it, discord.PCMAudio
# plays raw PCM. One less system dependency, and it works under Smart App Control.
THAI_VOICE = os.environ.get("TIWA_TTS_TH", "th-TH-PremwadeeNeural")
EN_VOICE = os.environ.get("TIWA_TTS_EN", "en-US-AvaNeural")


def is_thai(text: str) -> bool:
    return any("฀" <= c <= "๿" for c in text)


async def tts_pcm(text: str) -> bytes:
    """Her words -> raw PCM at Discord's format. Voice follows the language."""
    import io

    import edge_tts
    import soundfile as sf

    mp3 = bytearray()
    voice_name = THAI_VOICE if is_thai(text) else EN_VOICE
    async for chunk in edge_tts.Communicate(text, voice_name).stream():
        if chunk["type"] == "audio":
            mp3 += chunk["data"]
    audio, rate = sf.read(io.BytesIO(bytes(mp3)), dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if rate != SAMPLE_RATE:  # edge-tts is 24kHz, so this is a clean 2x
        n = int(len(audio) * SAMPLE_RATE / rate)
        audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio)
    stereo = np.repeat((audio * 32767).astype(np.int16), 2)
    return stereo.tobytes()


async def say(guild, text: str) -> bool:
    """Speak in the voice channel. Music is paused for the duration (ducking)."""
    import io

    import discord

    if DJ_ONLY:
        return False  # the channel is a speaker for music, not a mouth
    vc = guild.voice_client
    if vc is None or not text.strip():
        return False
    pcm = await tts_pcm(text)
    was_playing = vc.is_playing()
    if was_playing:
        vc.pause()  # ponytail: pause, not volume duck — she talks over nothing
    loop = asyncio.get_running_loop()  # captured HERE: `after` runs off-loop
    done = asyncio.Event()

    def finished(error):
        if error:
            print(f"[voice] playback error: {error!r}")
        loop.call_soon_threadsafe(done.set)

    seconds = len(pcm) / (SAMPLE_RATE * 4)
    try:
        vc.play(discord.PCMAudio(io.BytesIO(pcm)), after=finished)
        # NEVER wait forever. This await used to run under the channel lock, so
        # one missed callback made her deaf for the rest of the session: she
        # answered once and then every later utterance queued behind the lock.
        await asyncio.wait_for(done.wait(), timeout=seconds + 15)
    except asyncio.TimeoutError:
        print(f"[voice] speech timed out after {seconds + 15:.0f}s — moving on")
        vc.stop()
    except Exception as e:
        print(f"[voice] could not speak: {type(e).__name__}: {e}")
    finally:
        if was_playing and vc.is_connected():
            vc.resume()
    return True


def ffmpeg_ready() -> bool:
    """Not needed any more — kept so the self-check can say so out loud."""
    return shutil.which("ffmpeg") is not None


if __name__ == "__main__":  # self-check: deps present, no discord needed
    assert voice_recv.VoiceRecvClient
    assert wants_now("tiwa come join the vc")
    assert wants_now("get in here")
    assert not wants_now("join us later tonight")
    assert not wants_now("we might vc tomorrow")
    assert not wants_now("เดี๋ยวเข้ามานะ")
    assert wake("ที่ว่าเปิดเพลง") and wake("tiwa hi") and wake("nothing here") is None
    print("voice deps ok (pynacl + voice_recv), join veto ok, wake word ok")
    print("ffmpeg:", "present" if ffmpeg_ready() else "absent — and not needed, "
          "playback is raw PCM (soundfile/PyAV)")
