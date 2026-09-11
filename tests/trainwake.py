"""Experimental personal wake classifier. Recordings stay local.

Requires requirements-voice.txt plus onnx. Never enables listening or updates .env.
File-level holdout is fixed before augmentation. Synthetic negatives are only
a smoke test, not a measured real-world false-activation rate.
"""
import json
from pathlib import Path
import sys
import math
import argparse

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
import onnx
from onnx import helper, numpy_helper, TensorProto
from openwakeword.utils import AudioFeatures
from openwakeword.model import Model

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import recordings

OUT = Path('data/wake_training')


def audio(path):
    a, rate = sf.read(path, dtype='float32')
    if a.ndim == 2:
        a = a.mean(axis=1)
    g = math.gcd(rate, 16000)
    return resample_poly(a, 16000//g, rate//g)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--varied', action='store_true', help='Augment speaking speed, gain and background noise')
    args = parser.parse_args()
    out = args.output
    rng = np.random.default_rng(42)
    positive, negative = [], []
    for _, ident in recordings.listing():
        path, label, kind, _ = recordings.load(ident)
        (positive if kind == 'Hey Tiwa' else negative).append(Path(path))
    positive.sort()
    negative += sorted((OUT/'negatives').glob('*.mp3'))
    negative = [p for p in negative if p.stat().st_size > 1000]
    rng.shuffle(positive)
    rng.shuffle(negative)
    if len(positive) < 10 or len(negative) < 6:
        raise ValueError('Need at least 10 positive recordings and 6 negative recordings')
    npos, nneg = max(3, len(positive)//4), max(2, len(negative)//3)
    testpos, trainpos = positive[:npos], positive[npos:]
    testneg, trainneg = negative[:nneg], negative[nneg:]
    examples, labels = [], []
    for files, label in [(trainpos, 1), (trainneg, 0)]:
        for p in files:
            a = audio(p)
            if label:
                # Trim only surrounding near-silence, preserving a 100 ms margin.
                active = np.flatnonzero(abs(a) > max(.005, float(abs(a).max())*.08))
                if len(active):
                    a = a[max(0, active[0]-1600):active[-1]+1600]
            chunks = [a] if label else [a[i:i+32000] for i in range(0,len(a),16000)]
            for chunk in chunks:
                chunk = chunk[:30000]
                for _ in range(8):
                    augmented = chunk
                    if args.varied:
                        augmented = resample_poly(chunk, 100, int(rng.integers(85,116)))[:30000]
                    sample = rng.normal(0, rng.uniform(.0001,.003) if args.varied else .0003,32000).astype(np.float32)
                    start = int(rng.integers(0,32000-len(augmented)+1))
                    sample[start:start+len(augmented)] += augmented * rng.uniform(.25 if args.varied else .5,1.2)
                    examples.append((np.clip(sample,-1,1)*32767).astype(np.int16))
                    labels.append(label)
    for _ in range(30):
        examples.append((rng.normal(0,rng.uniform(.0001,.015),32000)*32767).astype(np.int16))
        labels.append(0)
    features = AudioFeatures(inference_framework='onnx')
    x = features.embed_clips(np.stack(examples), batch_size=32).reshape(len(examples),-1)
    scaler = StandardScaler().fit(x)
    classifier = LogisticRegression(C=.01, class_weight='balanced', max_iter=1000).fit(scaler.transform(x), labels)
    w = (classifier.coef_[0]/scaler.scale_).astype(np.float32).reshape(-1,1)
    b = (classifier.intercept_ - np.sum(classifier.coef_[0]*scaler.mean_/scaler.scale_)).astype(np.float32)
    graph = helper.make_graph([
        helper.make_node('Flatten',['features'],['flat'],axis=1),
        helper.make_node('MatMul',['flat','w'],['product']),
        helper.make_node('Add',['product','b'],['logit']),
        helper.make_node('Sigmoid',['logit'],['score'])], 'hey_tiwa',
        [helper.make_tensor_value_info('features',TensorProto.FLOAT,[1,16,96])],
        [helper.make_tensor_value_info('score',TensorProto.FLOAT,[1,1])],
        [numpy_helper.from_array(w,'w'),numpy_helper.from_array(b,'b')])
    model = helper.make_model(graph,opset_imports=[helper.make_opsetid('',13)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    out.mkdir(parents=True,exist_ok=True)
    path = out/'hey_tiwa.experimental.onnx'
    onnx.save(model,path)
    detector = Model(wakeword_models=[str(path)], inference_framework='onnx',vad_threshold=.5)
    results = []
    for files,label in [(testpos,1),(testneg,0)]:
        for p in files:
            detector.reset()
            a = np.concatenate([np.zeros(16000),audio(p),np.zeros(24000)])
            scores = []
            for i in range(0,len(a)-1279,1280):
                scores.append(float(max(detector.predict((a[i:i+1280]*32767).astype(np.int16)).values())))
            row = dict(file=p.name,expected=label,peak=round(max(scores),4),detected=max(scores)>=.6)
            results.append(row)
            print(row,flush=True)
    report = dict(model=str(path),threshold=.6,train_positive=[p.name for p in trainpos],
                  train_negative=[p.name for p in trainneg],results=results,
                  limitation='Personal experimental model. Synthetic negatives do not certify live false activation rate.')
    (out/'evaluation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Experimental detector saved; not enabled.',flush=True)


if __name__ == '__main__':
    main()
