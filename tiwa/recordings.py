"""Local wake-word samples. IDs, never user-provided paths, select recordings."""

import json
import os
from pathlib import Path
import re
import uuid

import numpy as np
import soundfile as sf

ROOT = (
    Path(os.getenv("TIWA_DATA_DIR") or Path(__file__).resolve().parents[1] / "data")
    / "wake_recordings"
)
KINDS = ["Hey Tiwa", "Background / no wake word"]


def path(ident):
    if not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{32}", ident):
        raise ValueError("Select a recording first")
    target = ROOT / ident
    if target.with_suffix(".wav").is_symlink() or target.with_suffix(".json").is_symlink():
        raise ValueError("Recording links are not supported")
    return target


def metadata(label, kind, notes):
    label, notes = str(label or "").strip(), str(notes or "").strip()
    if not label or len(label) > 100 or len(notes) > 1000 or kind not in KINDS:
        raise ValueError(
            "Use a name (1–100 characters), a sample type, and notes under 1,000 characters"
        )
    return dict(label=label, kind=kind, notes=notes)


def edit(ident, label, kind, notes):
    base = path(ident)
    if not base.with_suffix(".wav").is_file():
        raise ValueError("Recording no longer exists")
    value = metadata(label, kind, notes)
    tmp = base.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    tmp.replace(base.with_suffix(".json"))


def save(audio, label, kind, notes):
    value = metadata(label, kind, notes)
    if audio is None:
        raise ValueError("Record or upload audio first")
    rate, samples = audio
    samples = np.asarray(samples)
    if (
        not 8000 <= rate <= 192000
        or samples.ndim not in (1, 2)
        or not 0.3 <= len(samples) / rate <= 60
    ):
        raise ValueError("Record between 0.3 and 60 seconds of audio")
    if not np.isfinite(samples).all():
        raise ValueError("Audio contains invalid samples")
    if np.issubdtype(samples.dtype, np.integer):
        samples = samples.astype(np.float32) / max(
            abs(np.iinfo(samples.dtype).min), np.iinfo(samples.dtype).max
        )
    if samples.ndim == 2:
        samples = samples.mean(axis=1)
    if np.max(np.abs(samples)) < 0.0001:
        raise ValueError("This recording is silent. Check your microphone and try again")
    ROOT.mkdir(parents=True, exist_ok=True)
    ident = uuid.uuid4().hex
    base = path(ident)
    try:
        sf.write(base.with_suffix(".wav"), np.clip(samples, -1, 1), rate, subtype="PCM_16")
        edit(ident, **value)
    except Exception:
        base.with_suffix(".wav").unlink(missing_ok=True)
        raise
    return ident


def listing():
    result = []
    for file in sorted(ROOT.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            audio, label, kind, notes = load(file.stem)
            duration = sf.info(audio).duration
            result.append((f"{label} · {kind} · {duration:.1f}s", file.stem))
        except (ValueError, OSError, RuntimeError, KeyError, TypeError):
            continue
    return result


def load(ident):
    base = path(ident)
    item = json.loads(base.with_suffix(".json").read_text(encoding="utf-8"))
    return str(base.with_suffix(".wav").resolve()), item["label"], item["kind"], item["notes"]


def delete(ident):
    base = path(ident)
    base.with_suffix(".wav").unlink(missing_ok=True)
    base.with_suffix(".json").unlink(missing_ok=True)


def trim(ident, start, end):
    audio, label, kind, notes = load(ident)
    samples, rate = sf.read(audio, dtype="float32")
    start, end = float(start), float(end)
    if not 0 <= start < end <= len(samples) / rate:
        raise ValueError("Choose start and end times within the recording")
    return save(
        (rate, samples[int(start * rate) : int(end * rate)]), label[:85] + " (trimmed)", kind, notes
    )
