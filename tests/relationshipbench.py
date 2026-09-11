"""Regression for Thai family -> name recall. Synthetic data, --live tests replies."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1]))
scratch = tempfile.TemporaryDirectory(prefix='tiwa-relationships-')
os.environ['TIWA_DATA_DIR'] = scratch.name
os.environ['TIWA_LOG_PROMPTS'] = '0'
from tiwa import memory, recall, pipeline, llm, minis

QUESTION = 'เหตุที่ว่าพ่อของหนูชื่ออะไร'


def fixture():
    db = memory.connect(':memory:')
    memory.remember(db, 'Tycoon', 'father of', memory.TIWA)
    memory.remember(db, 'Tycoon', 'real name', 'Gateaux')
    memory.remember(db, 'Somchai', 'father of', 'Tycoon')
    memory.remember(db, 'Mint', 'mother of', memory.TIWA)
    memory.remember(db, 'Mint', 'real name', 'May')
    memory.remember(db, 'Tycoon', 'likes', 'Mili', category='music')
    return db


async def offline():
    db = fixture()
    for speaker in ('Tycoon', 'Someone else'):
        for question in (QUESTION, 'พ่อหนูชื่ออะไร', 'พ่อของทิวาชื่ออะไร', "What is your father's name?"):
            with patch.object(llm, 'chat', side_effect=RuntimeError('offline')):
                block = await recall.retrieve(db, speaker, question)
            assert 'Tycoon father of ทิวา' in block and 'Tycoon real name Gateaux' in block, block
            assert 'Somchai' not in block and 'Mili' not in block and 'Mint' not in block
            assert len(block.encode()) <= recall.CONTEXT_BYTES
    for question in ('พ่อของผมชื่ออะไร', "What is my father's name?"):
        block = await recall.retrieve(db, 'Tycoon', question)
        assert 'Somchai father of Tycoon' in block and 'Gateaux' not in block, block
    block = await recall.retrieve(db, 'Tycoon', 'แม่หนูชื่ออะไร')
    assert 'Mint mother of ทิวา' in block and 'Mint real name May' in block and 'Gateaux' not in block
    before = await recall.retrieve(db, 'Tycoon', QUESTION)
    async def dispatch(*args):
        return {'dispatch': [], 'ask': ''}
    sent = []
    def persona(**kw):
        sent.append(kw['messages'])
        return {'content': 'พ่อชื่อ Gateaux'}
    with patch.object(minis, 'dispatch', side_effect=dispatch), patch.object(llm, 'chat', side_effect=persona):
        reply = await pipeline.respond(db, [{'role': 'user', 'content': 'Tycoon: ' + QUESTION}], 'Tycoon', QUESTION)
    assert reply == 'จำได้ว่าพ่อของหนูชื่อ Gateaux (Tycoon)' and not sent
    # Other conversational replies still receive memory as separate answer evidence.
    with patch.object(llm, 'chat', side_effect=persona):
        await pipeline.say(db, [{'role': 'user', 'content': QUESTION}],
                           pipeline._state(db, 'Tycoon', QUESTION, extra=before))
    assert 'Gateaux' not in sent[0][1]['content']  # not hidden inside inner-state
    assert sent[0][-1]['role'] == 'system' and 'Tycoon real name Gateaux' in sent[0][-1]['content']
    assert sum(m['content'].count('Tycoon real name Gateaux') for m in sent[0]) == 1
    for i in range(1000):
        memory.remember(db, f'Other{i}', 'father of', f'Child{i}')
    assert await recall.retrieve(db, 'Tycoon', QUESTION) == before
    assert 'Gateaux' not in await recall.retrieve(db, 'Tycoon', 'hello')
    memory.remember(db, 'Other father', 'father of', memory.TIWA)
    ambiguous = await recall.retrieve(db, 'Tycoon', QUESTION)
    assert 'Other father' in ambiguous and 'Tycoon father of' in ambiguous
    assert 'competing names need clarification' in ambiguous
    assert 'หลายชื่อ' in recall.relationship_reply(db, 'Tycoon', QUESTION, ambiguous)
    print('PASS exact Thai regression, English, speaker/relative ownership, mother, provider failure, ambiguity, 1000 unrelated families')
    db.close()


async def live():
    db = fixture()
    report = []
    for question in (QUESTION, 'พ่อหนูชื่ออะไร', "What is your father's name?"):
        block = await recall.retrieve(db, 'Tycoon', question)
        reply = await pipeline.respond(db, [{'role': 'user', 'content': 'Tycoon: ' + question}], 'Tycoon', question)
        ok = any(n in reply.casefold() for n in ('gateaux', 'กาโต', 'กาโต้', 'กาโตว์')) and not any(n in reply.casefold() for n in ('สมชาย', 'somchai'))
        report.append({'question': question, 'memory': block, 'bytes': len(block.encode()),
                       'reply': reply, 'mode': 'stored relationship answer; real dispatch', 'passed': ok})
        print(json.dumps(report[-1], ensure_ascii=False), flush=True)
    Path('qa-results/memory/relationship.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    assert all(r['passed'] for r in report), 'Persona failed to use the recalled name; see relationship.json'
    db.close()


if __name__ == '__main__':
    asyncio.run(offline())
    if '--live' in sys.argv:
        asyncio.run(live())
