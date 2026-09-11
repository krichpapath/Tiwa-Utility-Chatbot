"""Name claims and one-hop recall; synthetic database only. Add --live for models."""
import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).parents[1]))
scratch = tempfile.TemporaryDirectory(prefix='tiwa-names-')
os.environ['TIWA_DATA_DIR'] = scratch.name
os.environ['TIWA_LOG_PROMPTS'] = '0'
from tiwa import memory, recall, pipeline

CLAIM = 'Tycoon user is actually name Gateaux remember that'


def fixture():
    db = memory.connect(':memory:')
    memory.remember(db, 'Tycoon', 'likes', 'Mili', 'storytelling', category='music', evidence='explicit')
    memory.remember(db, memory.TIWA, 'likes', 'Gojo', 'confidence', evidence='stance')
    return db


def store(db, speaker='Krich', subject='Tycoon', alias='Gateaux'):
    quote = CLAIM if subject == 'Tycoon' else f'{subject} is called {alias}'
    memory.store_extraction(db, speaker, {'memories': [{'subject': subject,
        'relation': 'also known as', 'object': alias, 'note': '', 'category': 'general',
        'quote': quote}], 'episode': None, 'episode_quote': ''}, said=f'{speaker}: {quote}')


def block(db, text):
    return recall.pack(r['text'] for r in recall.candidates(db, 'Krich', text))


def offline():
    db = fixture()
    store(db)
    assert db.execute("SELECT evidence,source FROM relations WHERE rel='also known as'").fetchone() == ('reported', 'Krich: ' + CLAIM)
    for question in ('Who is Gateaux?', 'What is Tycoon called?'):
        assert 'Tycoon also known as Gateaux' in block(db, question)
    for question in ('What music does Gateaux like?', 'What does Gateaux like?'):
        result = block(db, question)
        assert 'Tycoon likes Mili' in result and 'via reported name link' in result, result
    assert 'Mili' not in block(db, 'What does Gateaux think about Gojo?')
    assert 'Gateaux' not in block(db, 'What music do I like?')
    before = block(db, 'What music does Gateaux like?')
    for i in range(1000):
        memory.remember(db, f'Stranger{i}', 'likes', f'Band{i}', category='music')
    assert block(db, 'What music does Gateaux like?') == before
    assert len(before.encode()) <= recall.CONTEXT_BYTES
    store(db, subject='Another', alias='Gateaux')
    assert 'Mili' not in block(db, 'What music does Gateaux like?')
    db.close()
    db = fixture()
    store(db)
    memory.remember(db, 'Gateaux', 'likes', 'ABBA', category='music')
    assert 'Mili' not in block(db, 'What music does Gateaux like?')
    assert 'Gateaux likes ABBA' in block(db, 'What music does Gateaux like?')
    db.close()
    db = fixture()
    store(db, speaker='Tycoon')
    assert db.execute("SELECT evidence,category FROM relations WHERE rel='also known as'").fetchone() == ('explicit', 'interaction')
    assert 'Gateaux' in recall.context(db, 'Tycoon', 'hello')
    db.close()
    print('PASS name evidence, future recall, ownership, topic isolation, ambiguity, existing person, self-name, 1000 unrelated facts')


async def live():
    db = fixture()
    await asyncio.to_thread(memory.extract, db, 'Krich', CLAIM, 'Got it, you mean Tycoon is called Gateaux.')
    facts = memory.export_all(db)
    assert any(r['evidence'] == 'reported' and 'Krich:' in r['source'] for r in facts['relations']), facts
    report = {'claim': CLAIM, 'stored': facts, 'conversations': []}
    for question in ('Who is Gateaux?', 'What music does Gateaux like?', 'What does Gateaux think about Gojo?'):
        recalled = await recall.retrieve(db, 'Krich', question)
        if 'music' in question:
            assert 'Tycoon likes Mili' in recalled and 'reported name link' in recalled, recalled
        elif 'Who' in question:
            assert 'Tycoon' in recalled and 'Gateaux' in recalled, recalled
        else:
            assert 'Mili' not in recalled, recalled
        reply = await pipeline.say(db, [{'role': 'user', 'content': 'Krich: ' + question}],
                                   pipeline._state(db, 'Krich', question, extra=recalled))
        report['conversations'].append({'question': question, 'recall': recalled,
                                       'bytes': len(recalled.encode()), 'reply': reply})
        print(json.dumps(report['conversations'][-1], ensure_ascii=False), flush=True)
    Path('qa-results/memory/name-link.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    db.close()


if __name__ == '__main__':
    offline()
    if '--live' in sys.argv:
        asyncio.run(live())
