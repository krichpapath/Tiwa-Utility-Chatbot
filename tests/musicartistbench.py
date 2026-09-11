"""Korone selection regression. --live uses public YouTube/AI services and decodes audio locally."""
import json
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tiwa import llm,music
entries=json.loads(Path(__file__).with_name('korone_candidates.json').read_text(encoding='utf-8'))
request={'request':'เปิดเพลงของอินุกามิ โคโรเนะให้หน่อย','keywords':'Inugami Korone song'}
if '--live' in sys.argv:
    hit=music.find(request)
    stream=music.Stream(hit['url'],vid=hit['id'])
    try:
        frames=sum(bool(stream.read()) for _ in range(100))
        assert frames==100,frames
        print('PASS live search, evidence selection, stream resolution, 100 audio frames:',hit['title'],hit['id'])
    finally:
        stream.cleanup()
else:
    def review(**kwargs):
        payload=json.loads(kwargs['messages'][1]['content'])
        assert payload['request']==request['request']
        assert payload['search_keywords']=='Inugami Korone song'
        assert payload['candidates']==entries
        assert 'Cross-reference' in kwargs['messages'][0]['content']
        return {'content':'{"index":0,"query":""}'}
    with patch.object(music,'_music_queries',return_value=['Inugami Korone song']), patch.object(music,'_search',return_value=entries), patch.object(llm,'chat',side_effect=review), patch.object(music,'find',return_value=dict(entries[0],url='test-stream')) as resolve:
        result=music._discover(**{'request':request['request'],'keywords':request['keywords']})
        assert result['id']==entries[0]['id']
        resolve.assert_called_once_with(music.watch_url(entries[0]['id']))
    print('PASS original intent + canonical keywords + native/romanized evidence reach selection; exact candidate resolved')

assert music._music_request('อีทีสุดแซด เฮ้ย ชีวาเล่นเดสไปชิโต้ให้หน่อย') == 'เล่นเดสไปชิโต้ให้หน่อย'
assert music._music_request('thanks hey Tiwa play Despacito') == 'play Despacito'
assert music._music_request('play Hey Jude by The Beatles') == 'play Hey Jude by The Beatles'
assert music._music_request('เปิดเพลงของอินุกามิ โคโรเนะให้หน่อย') == 'เปิดเพลงของอินุกามิ โคโรเนะให้หน่อย'
