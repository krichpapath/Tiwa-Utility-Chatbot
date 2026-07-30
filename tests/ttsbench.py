"""G8 — her voice comes out as playable audio, in both languages, no ffmpeg.

Writes data/tts_en.wav and data/tts_th.wav so you can actually listen.

    py -X utf8 tests\\ttsbench.py
"""
import asyncio
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import voice  # noqa: E402

OUT = Path(__file__).parents[1] / "data"
CASES = [
    ("en", "Steven's coming over? Tell him to bring his guitar this time."),
    ("th", "ทิวาเองแหละ มึงจะกินอะไรก็รีบเลือกซะ"),
]


async def main():
    OUT.mkdir(exist_ok=True)
    print("| lang | voice | text | audio | took | bytes |")
    print("|---|---|---|---|---|---|")
    for lang, text in CASES:
        t0 = time.perf_counter()
        pcm = await voice.tts_pcm(text)
        took = time.perf_counter() - t0
        seconds = len(pcm) / (voice.SAMPLE_RATE * 2 * 2)  # stereo int16
        path = OUT / f"tts_{lang}.wav"
        with wave.open(str(path), "wb") as w:  # so a human can verify by ear
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(voice.SAMPLE_RATE)
            w.writeframes(pcm)
        used = voice.THAI_VOICE if voice.is_thai(text) else voice.EN_VOICE
        print(f"| {lang} | {used} | {text[:38]}… | {seconds:.1f}s | {took:.1f}s "
              f"| {len(pcm):,} |")
        assert seconds > 1.0, "suspiciously short audio"
        assert len(pcm) % 4 == 0, "not 16-bit stereo frames"

    # the language switch must be automatic, not a flag anyone has to remember
    assert voice.is_thai("ทิวาเอง") and not voice.is_thai("hey there")
    print(f"\nwrote {OUT/'tts_en.wav'} and {OUT/'tts_th.wav'} — listen to check")
    print("G8 ok — speech generated at discord's format, no ffmpeg involved")


asyncio.run(main())
