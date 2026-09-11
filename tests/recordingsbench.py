"""Local recording CRUD and unsafe-input checks, no microphone needed."""

import tempfile
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import recordings as r

with tempfile.TemporaryDirectory() as d:
    r.ROOT = Path(d)
    audio = (16000, (np.sin(np.arange(16000) * 0.1) * 10000).astype(np.int16))
    ident = r.save(audio, "normal", "Hey Tiwa", "headset")
    assert len(r.listing()) == 1 and Path(r.load(ident)[0]).is_file()
    r.edit(ident, "quiet", "Hey Tiwa", "test")
    assert r.load(ident)[1] == "quiet"
    trimmed = r.trim(ident, 0.1, 0.6)
    assert len(r.listing()) == 2 and Path(r.load(ident)[0]).exists()
    r.delete(trimmed)
    for bad in ["../secret", "", None]:
        try:
            r.delete(bad)
            raise AssertionError("unsafe path accepted")
        except ValueError:
            pass
    for bad_audio in [None, (16000, np.zeros(16000)), (16000, np.array([float("nan")]))]:
        try:
            r.save(bad_audio, "bad", "Hey Tiwa", "")
            raise AssertionError("invalid audio accepted")
        except ValueError:
            pass
    (r.ROOT / "broken.json").write_text("{}")
    assert len(r.listing()) == 1
    r.delete(ident)
    assert not r.listing()
print(
    "PASS recording creation, playback file, metadata editing, deletion, malformed data and path guards"
)
