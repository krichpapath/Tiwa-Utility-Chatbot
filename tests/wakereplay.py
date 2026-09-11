"""Replay held-out clips through the real local detector and Discord PCM adapter.

--transcribe sends activated positive commands to OpenRouter. Never joins Discord.
"""
import asyncio
import json
from pathlib import Path
import sys
import time
import os

import numpy as np
from openwakeword.model import Model

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tiwa import llm, stt
from tiwa.listening import Ears, command_from_transcript
from trainwake import audio

report = json.loads(Path('data/wake_training/evaluation.json').read_text(encoding='utf-8'))
threshold = .9
command = audio(Path('data/wake_training/negatives/synthetic-01.mp3'))
rows = []
for entry in report['results']:
    filename = entry['file']
    path = Path('data/wake_recordings')/filename if filename.endswith('.wav') else Path('data/wake_training/negatives')/filename
    utterance = audio(path)
    if entry['expected']:
        utterance = np.concatenate([utterance, np.zeros(3200), command])
    a = np.concatenate([np.zeros(16000),utterance,np.zeros(32000)])
    detector = Model(wakeword_models=[report['model']],inference_framework='onnx',vad_threshold=.5)
    ears = object.__new__(Ears)
    ears.first, ears.factory = detector, lambda: detector
    ears.speakers = {}
    ears.threshold, ears.silence, ears.maximum = threshold, 1.2, 20
    clips=[]
    started=time.monotonic()
    for i in range(0,len(a)-1279,1280):
        pcm=np.repeat(np.repeat((np.clip(a[i:i+1280],-1,1)*32767).astype('<i2'),3),2).tobytes()
        result=ears.process(123,'QA replay',pcm,i/16000)
        if result:clips.append(result[1])
    row=dict(file=filename,expected=entry['expected'],clips=len(clips),seconds=round(time.monotonic()-started,2))
    if '--transcribe' in sys.argv and entry['expected'] and clips:
        text=stt.transcribe(clips[0],16000)
        row.update(transcript=text,command=command_from_transcript(text))
    rows.append(row)
    print(json.dumps(row,ensure_ascii=False),flush=True)
Path('data/wake_training/replay.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')

