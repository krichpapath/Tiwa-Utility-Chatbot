import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tiwa import llm,minis,tools,memory
with patch.object(llm,'chat',return_value={'content':'{"steps":[]}'}):
    tools.new_turn()
    result=minis.dj(memory.connect(':memory:'),'อ่า เพศที่ว่าเล่นเพลงเขาระบควายของนานาฮิระสิ')
    assert result['action']=='play' and len(tools.DJ)==1
    assert tools.DJ[0][1]['request']=='เล่นเพลงเขาระบควายของนานาฮิระสิ'
    for text in ('ไม่ต้องเล่นเพลงนี้','เล่นเพลงนี้พรุ่งนี้','ไม่รู้จักนานาฮิระ','play Despacito later'):
        tools.new_turn();minis.dj(memory.connect(':memory:'),text);assert not tools.DJ,text
print('PASS empty DJ plan still queues explicit play; negation, future plans and artist chat stay inactive')
