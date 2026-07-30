"""Debug wavs must never crash the listener.

A real transcript, "Hello how are you?", took her ears down: the '?' went into
a Windows filename and raised OSError inside the sweep loop.

    py -X utf8 tests\\dumpbench.py
"""
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import voice  # noqa: E402

NASTY = [
    "Hello  how are you?",                 # THE one that broke it
    'she said "what" then left',
    "who/what\\where",
    "ratio 50:50 <best> |ever|",
    "trailing dots...",
    "trailing space ",
    "ทิวา เปิดเพลงหน่อย",                   # Thai must survive
    "*" * 40,
    "",
    "   ",
    "a" * 300,
]

audio = (np.sin(np.arange(16000) * 0.05) * 0.3).astype(np.float32)
out = Path(__file__).parents[1] / "data" / "voice_debug"

print("| transcript | filename produced |")
print("|---|---|")
before = set(out.glob("*.wav")) if out.exists() else set()
for text in NASTY:
    voice.dump(audio, 16000, "Tycoon", text)   # must never raise
    print(f"| `{text[:34]}` | {voice.safe_filename(text)!r} |")

made = sorted(set(out.glob("*.wav")) - before)
print(f"\nwrote {len(made)} files, all readable:")
for p in made:
    with wave.open(str(p)) as w:
        assert w.getnframes() > 0, p
assert len(made) == len(NASTY), f"expected {len(NASTY)} files, got {len(made)}"

# a name that cannot be written must still not raise
voice.dump(audio, 16000, "Tycoon", "x" * 5)
print("\nok — every case written, nothing raised, listener safe")
