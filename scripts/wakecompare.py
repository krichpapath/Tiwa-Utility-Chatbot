"""Compare two wake models locally through Discord's PCM adapter; no STT/network."""

import json, sys
from pathlib import Path
import numpy as np
from openwakeword.model import Model

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa.listening import Ears
from trainwake import audio
from scipy.signal import resample_poly


def compare(folder):
    report = json.loads((folder / "evaluation.json").read_text(encoding="utf-8"))
    command = audio(Path("data/wake_training/negatives/synthetic-01.mp3"))
    rows = []
    for name, model_path in [
        ("current", Path("data/wake_training/hey_tiwa.experimental.onnx")),
        ("candidate", folder / "hey_tiwa.experimental.onnx"),
    ]:
        detector = Model(
            wakeword_models=[str(model_path)], inference_framework="onnx", vad_threshold=0.5
        )
        for threshold in (0.9, 0.75):
            for entry in report["results"]:
                filename = entry["file"]
                path = (
                    Path("data/wake_recordings") / filename
                    if filename.endswith(".wav")
                    else Path("data/wake_training/negatives") / filename
                )
                for variant, gain, speed in [
                    ("normal", 1, 100),
                    ("quiet", 0.4, 100),
                    ("fast", 1, 115),
                ]:
                    utterance = resample_poly(audio(path), 100, speed) * gain
                    if entry["expected"]:
                        utterance = np.concatenate([utterance, np.zeros(3200), command])
                    a = np.concatenate([np.zeros(16000), utterance, np.zeros(32000)])
                    detector.reset()
                    ears = object.__new__(Ears)
                    ears.first = detector
                    ears.factory = lambda: detector
                    ears.speakers = {}
                    ears.threshold = threshold
                    ears.silence = 1.2
                    ears.maximum = 20
                    clips = 0
                    for i in range(0, len(a) - 1279, 1280):
                        pcm = np.repeat(
                            np.repeat((np.clip(a[i : i + 1280], -1, 1) * 32767).astype("<i2"), 3), 2
                        ).tobytes()
                        if ears.process(123, "QA", pcm, i / 16000):
                            clips += 1
                    rows.append(
                        dict(
                            model=name,
                            threshold=threshold,
                            file=filename,
                            variant=variant,
                            expected=entry["expected"],
                            clips=clips,
                        )
                    )
            group = [r for r in rows if r["model"] == name and r["threshold"] == threshold]
            print(
                name,
                threshold,
                "positive",
                sum(r["clips"] > 0 for r in group if r["expected"]),
                "/",
                sum(r["expected"] for r in group),
                "false triggers",
                sum(r["clips"] > 0 for r in group if not r["expected"]),
                flush=True,
            )
    (folder / "comparison.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    compare(Path(sys.argv[1]))
