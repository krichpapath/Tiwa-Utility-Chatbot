"""G6 — audio in, chatlog out, no model calls.

Feeds real speech through the SAME sink the Discord client uses (fake packets
at Discord's format), so segmentation, silence cutting and transcription are
all exercised without a server.

    py -X utf8 tests\\voicebench.py
"""
import asyncio
import subprocess
import sys
import time
import types
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import voice  # noqa: E402

TMP = Path(__file__).parents[1] / "data"
SENTENCE = "Steven is coming over tonight and he is bringing his guitar"


def say(text: str, path: Path) -> Path:
    """Windows' own TTS — signed, no extra deps, good enough for a fixture."""
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{text}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True)
    return path


def as_discord_pcm(path: Path) -> bytes:
    """Whatever the fixture is -> Discord's format: 48kHz stereo int16."""
    with wave.open(str(path)) as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
        rate, ch = w.getframerate(), w.getnchannels()
    if ch == 2:
        a = a.reshape(-1, 2).mean(axis=1).astype(np.int16)
    idx = (np.arange(int(len(a) * voice.SAMPLE_RATE / rate)) * rate
           // voice.SAMPLE_RATE).astype(int)
    up = a[np.clip(idx, 0, len(a) - 1)]
    return np.repeat(up, 2).astype(np.int16).tobytes()  # mono -> stereo


async def main():
    TMP.mkdir(exist_ok=True)
    wav = say(SENTENCE, TMP / "voicebench.wav")
    pcm = as_discord_pcm(wav)
    print(f"fixture: {len(pcm)/(voice.SAMPLE_RATE*4):.1f}s of discord-format audio")

    heard = []

    async def on_text(name, text):
        heard.append((name, text))

    ears = voice.Ears(on_text, asyncio.get_running_loop())
    user = types.SimpleNamespace(display_name="Krich")

    # 20ms frames, exactly like the real client delivers them
    frame = voice.SAMPLE_RATE // 50 * 4
    t0 = time.perf_counter()
    for i in range(0, len(pcm), frame):
        ears.write(user, types.SimpleNamespace(pcm=pcm[i : i + frame]))
    print("fed all frames, now going quiet...")

    # a too-short blip must be ignored entirely
    ears.write(types.SimpleNamespace(display_name="Noise"),
               types.SimpleNamespace(pcm=b"\x00" * (frame * 5)))

    for _ in range(120):  # up to ~36s for CPU whisper
        if heard:
            break
        await asyncio.sleep(0.3)
    ears.cleanup()

    print(f"\ntranscribed in {time.perf_counter()-t0:.1f}s")
    for name, text in heard:
        print(f"  {name}: {text}")
    assert heard, "nothing transcribed"
    assert heard[0][0] == "Krich", "wrong speaker attributed"
    words = set(SENTENCE.lower().split()) & set(heard[0][1].lower().split())
    print(f"  word overlap: {len(words)}/{len(SENTENCE.split())}")
    assert len(words) >= 7, f"transcript too far off: {heard[0][1]}"
    assert all(n != "Noise" for n, _ in heard), "short blip should have been dropped"
    print("\nG6 ok — speech became text, speaker kept, blips dropped, no model called")


asyncio.run(main())
