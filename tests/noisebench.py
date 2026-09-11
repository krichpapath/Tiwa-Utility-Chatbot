"""The three gates that stop her hearing ghosts.

Krich hit this live: silence produced repeated words. Gate order is
noise level -> VAD -> junk filter.

    py -X utf8 tests\\noisebench.py
"""

import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import voice  # noqa: E402

rng = np.random.default_rng(0)
with wave.open(str(Path(__file__).parents[1] / "data" / "vb_en.wav")) as w:
    speech = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    SPEECH_RATE = w.getframerate()  # fixture is 22kHz; noise below is 48kHz

AUDIO = [
    ("real speech", speech, SPEECH_RATE, True),
    ("digital silence", np.zeros(48000 * 3, dtype=np.float32), 48000, False),
    ("room hiss", (rng.standard_normal(48000 * 3) * 0.002).astype(np.float32), 48000, False),
    ("louder hiss", (rng.standard_normal(48000 * 3) * 0.02).astype(np.float32), 48000, False),
    (
        "fan hum",
        (0.03 * np.sin(2 * np.pi * 60 * np.arange(48000 * 3) / 48000)).astype(np.float32),
        48000,
        False,
    ),
]

JUNK = [
    ("Thanks for watching!", True),
    ("ขอบคุณที่รับชม", True),
    ("no no no no no", True),
    ("tututututututututututu", True),
    ("ครับครับครับครับครับครับ", True),
    ("Steven is coming over tonight", False),
    ("ทิวา เปิดเพลงหน่อย", False),
    ("ok", False),
]

print("| audio | level | passes gate? | want |")
print("|---|---|---|---|")
bad = 0
for label, audio, rate, want in AUDIO:
    level = voice.rms(audio)
    loud = level >= voice.NOISE_FLOOR
    text = voice.transcribe(audio, rate=rate) if loud else ""
    heard = bool(text) and not voice.is_junk(text)
    bad += heard != want
    print(
        f"| {label} | {level:.4f} | {'yes: ' + text[:40] if heard else 'no'} "
        f"| {'yes' if want else 'no'} |{'' if heard == want else '  **WRONG**'}"
    )

print("\n| transcript | junk? | want |")
print("|---|---|---|")
for text, want in JUNK:
    got = voice.is_junk(text)
    bad += got != want
    print(
        f"| `{text}` | {'yes' if got else 'no'} | {'yes' if want else 'no'} "
        f"|{'' if got == want else '  **WRONG**'}"
    )

assert bad == 0, f"{bad} cases wrong"
print(f"\nall clean — noise floor {voice.NOISE_FLOOR}, tune with TIWA_NOISE_FLOOR")
