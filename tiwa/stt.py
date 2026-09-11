"""Activated clips only. OpenRouter transcription is separate from chat tokens."""
import base64
import io
import os
import wave

import httpx
import numpy as np

MODELS = ("openai/gpt-transcribe", "qwen/qwen3-asr-1.7b",
          "openai/whisper-large-v3-turbo", "fish-audio/transcribe-1")


def transcribe(audio, rate=48000):
    model = os.getenv("TIWA_STT_MODEL") or MODELS[0]
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OpenRouter key required for transcription")
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim != 1 or not np.isfinite(audio).all() or rate <= 0:
        raise ValueError("invalid mono audio")
    if not len(audio):
        return ""
    if len(audio) / rate > 35:
        raise ValueError("transcription clip exceeds 35 seconds")
    data = io.BytesIO()
    with wave.open(data, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes((np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes())
    response = httpx.post(
        "https://openrouter.ai/api/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "input_audio": {
            "data": base64.b64encode(data.getvalue()).decode(), "format": "wav"}},
        timeout=30,
    )
    if response.is_error:
        raise RuntimeError(f"transcription provider returned HTTP {response.status_code}")
    result = response.json()
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise RuntimeError("transcription provider returned no valid text")
    usage = result.get("usage") or {}
    print(f"[stt] {model}: {usage.get('seconds', '?')}s, ${usage.get('cost', '?')}")
    return result["text"].strip()
