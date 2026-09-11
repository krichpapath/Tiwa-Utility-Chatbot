"""Calendar evidence must arrive before the first persona reply. No Google calls."""
import asyncio
from pathlib import Path
import sys
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tiwa import pipeline, minis, memory

async def main():
    db=memory.connect(':memory:')
    for result in ({'action':'read','events':'calendar empty for the next 7 days'},
                   {'action':'none','events':'calendar unavailable: reconnect Google'}, {}):
        finished=False
        def run(db, name, task):
            nonlocal finished
            assert name == 'calendar'
            finished=True
            return result
        async def say(db, hist, state):
            assert finished, 'persona ran before calendar check'
            assert 'CALENDAR THIS TURN' in state
            assert ('calendar empty' if result.get('action')=='read' else
                    'reconnect Google' if result else 'could not be verified') in state
            assert 'Old chat and memories are not proof' in state
            return 'fixture'
        with patch.object(minis,'dispatch',return_value={'dispatch':[], 'ask':''}), \
             patch.object(minis,'run',side_effect=run), patch.object(pipeline,'say',side_effect=say), \
             patch.object(pipeline,'_maybe_music',return_value=False), \
             patch.object(memory,'should_ask',return_value=False):
            out=await pipeline.respond(db,[{'role':'assistant','content':'Old dentist appointment'},
                                          {'role':'user','content':'check my calendar'}], 'QA','check my calendar')
            assert out=='fixture'
    print('PASS fresh calendar before reply, router fallback, empty/outage evidence, no old-event proof')
asyncio.run(main())
