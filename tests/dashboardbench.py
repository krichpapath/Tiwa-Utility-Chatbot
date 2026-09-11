"""Dashboard browsing checks; fake rows only, no real memory changes."""
import sys
import asyncio
import csv
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
scratch = tempfile.TemporaryDirectory(prefix='tiwa-dashboard-')
os.environ['TIWA_DATA_DIR'] = scratch.name
os.environ['TIWA_LOG_PROMPTS'] = '0'
import dashboard as d
import pandas as pd

facts = pd.DataFrame([(7, False, 'Krich', 'plays', 'Fortnite', 'Thai ไทย note', 'today'),
                      (9, False, 'Tiwa', 'likes', 'Miku', '', 'today')],
                     columns=['#', 'forget', 'who', 'what', 'about', 'note', 'updated'])
with patch.object(d, 'facts_df', return_value=facts):
    rows, _ = d.memory_browser('ไทย', 'Krich')
    assert list(rows.ID) == [7]
    assert d.memory_browser('Miku', 'Krich')[0].empty
    assert d.memory_browser('[.*')[0].empty  # literal, not regex
    selected, text, confirmed = d.memory_detail('Facts', SimpleNamespace(row_value=[7]))
    assert selected[:2] == ('Facts', 7) and 'Thai ไทย note' in text and not confirmed
    with patch.object(d.memory, 'delete_relation') as delete:
        try:
            d.forget_selected(selected, False, '', 'Everyone', 'Facts')
        except d.gr.Error:
            pass
        else:
            raise AssertionError('Deletion needs confirmation')
        delete.assert_not_called()
        d.forget_selected(selected, True, '', 'Everyone', 'Facts')
        delete.assert_called_once_with(d.DB, 7)
choices = d.model_choices(list(d.CHAT_MODELS), 'custom/model', 'deepseek/deepseek-v4-flash')
assert any(value == 'custom/model' for label, value in choices)
assert any('GPT-5.4 Mini' in label and value == 'openai/gpt-5.4-mini' for label, value in choices)
d.dashboard_theme()
print('PASS memory search, person filter, full details, deletion confirmation, model IDs, theme')

# Exercise real current-schema rows, not only a mocked old dataframe.
d.memory.remember(d.DB, 'Krich', 'likes', 'Mili', 'Thai ไทย storytelling', category='music',
                  evidence='explicit', source='I love Mili — ไทย')
d.memory.remember(d.DB, 'Krich', 'hates', 'Mili', 'too loud now', category='music',
                  evidence='explicit', source='I hate Mili now')
d.memory.remember(d.DB, d.memory.TIWA, 'likes', 'Gojo', 'confidence', evidence='stance', source='I like Gojo')
d.memory.remember(d.DB, 'Mint', 'often requests', 'ABBA', category='music', evidence='observed', source='three requests')
d.memory.remember(d.DB, 'Tycoon', 'also known as', 'Gateaux', evidence='reported', source='Krich: Tycoon is called Gateaux')
claims, _ = d.memory_browser('', 'Tycoon', 'Facts', evidence='reported')
assert len(claims) == 1 and claims.iloc[0]['Evidence'] == 'Reported by someone else'
assert 'Krich: Tycoon is called Gateaux' in d.memory_detail('Facts', SimpleNamespace(row_value=list(claims.iloc[0])))[1]
d.DB.execute("INSERT INTO episodes(user,text,ts,evidence,source) VALUES('Krich','Krich fell off the ledge',1,'explicit','I fell off the ledge')")
d.DB.commit()
rows, status = d.memory_browser('I hate Mili', 'Krich', 'Facts', 'music', 'explicit')
assert len(rows) == 1 and rows.iloc[0]['Evidence'] == 'Stated'
assert d.memory_browser('', 'Krich', 'Facts', 'games')[0].empty
assert len(d.memory_browser('', 'Mint', 'Facts', evidence='observed')[0]) == 1
selected, detail, _ = d.memory_detail('Facts', SimpleNamespace(row_value=list(rows.iloc[0])))
assert 'Source evidence:\nI hate Mili now' in detail and 'Category: music' in detail
assert 'also removes superseded history' in detail
assert d.memory_people('Krich').value == 'Krich'
assert d.memory_people('deleted person').value == 'Everyone'
history, status = d.memory_browser('I love Mili', 'Krich', 'History')
assert len(history) == 1 and 'not current beliefs' in status
historical, detail, _ = d.memory_detail('History', SimpleNamespace(row_value=list(history.iloc[0])))
assert 'Superseded' in detail and 'I love Mili' in detail
exported = json.loads(Path(d.export_memory()).read_text(encoding='utf-8'))
assert exported['history'] and exported['relations'][0]['evidence']
for export in (d.export_facts, d.export_episodes, d.export_history):
    path = Path(export())
    with path.open(encoding='utf-8-sig', newline='') as f:
        headers = next(csv.reader(f))
    assert 'evidence' in headers and 'source' in headers
    assert path.read_bytes().startswith(b'\xef\xbb\xbf')
d.forget_selected(historical, True, '', 'Krich', 'History')
assert d.history_df().empty and 'hates Mili' in d.memory.lookup(d.DB, 'Krich')

# A refreshed/replaced row must not be deleted using an old confirmation.
d.DB.execute('UPDATE relations SET note=? WHERE rowid=?', ('changed after selection', selected[1]))
d.DB.commit()
try:
    d.forget_selected(selected, True, '', 'Krich', 'Facts')
except d.gr.Error:
    pass
else:
    raise AssertionError('stale selection deleted a changed memory')
assert 'hates Mili' in d.memory.lookup(d.DB, 'Krich')
episodes, _ = d.memory_browser('', 'Krich', 'Conversations', evidence='explicit')
episode, detail, _ = d.memory_detail('Conversations', SimpleNamespace(row_value=list(episodes.iloc[0])))
assert 'I fell off the ledge' in detail
d.forget_selected(episode, True, '', 'Krich', 'Conversations')
assert d.episodes_df().empty
assert d.clear_memory_selection() == (None, d.MEMORY_HINT, False)
assert d.which_pass('You are Memory Mini, not Tiwa voice') == 'recalling'
assert d.which_pass('You decide which of Tiwa minis should handle a message') == 'dispatching'
assert d.which_pass('You are Tiwa memory noticing a pattern') == 'noticing tastes'

def select_ids(**kw):
    payload = json.loads(kw['messages'][-1]['content'])
    return {'content': json.dumps({'ids': [r['id'] for r in payload['candidates']]})}
before = len(d.facts_df())
with patch.object(d.llm, 'chat', side_effect=select_ids):
    block, status = asyncio.run(d.preview_recall('Krich', 'What do you think of Gojo?'))
    assert 'likes Gojo' in block and 'Mili' not in block and 'ABBA' not in block
    assert '/1200 bytes' in status and len(block.encode()) <= d.recall.CONTEXT_BYTES
    assert len(d.facts_df()) == before
    empty, _ = asyncio.run(d.preview_recall('Nobody', 'hello'))
    assert empty == 'No memory selected for this message.'
with patch.object(d.recall, 'select', side_effect=RuntimeError('offline')):
    block, status = asyncio.run(d.preview_recall('Krich', 'Gojo'))
    assert 'unavailable or timed out' in status and 'No memory selected' in block
print('PASS current evidence/source browsing, history, CSV/JSON exports, stale deletion, episode deletion, pass names, exact recall preview/failure')
d.DB.close()
scratch.cleanup()
