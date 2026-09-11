"""Conversational approval reaches writes only for the requesting account."""
import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace as Obj
from unittest.mock import AsyncMock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
from tiwa import gcal

async def main():
    plan=gcal.validate_change(dict(action='add',title='Checkup',start='2026-09-17T13:00:00'))
    channel=Obj(id=567,send=AsyncMock())
    def propose():
        bot.calendar.pending.clear()
        bot.calendar.pending[12]=(plan,channel.id,time.monotonic()+600)
        bot.calendar.requesters[12]=42
    with patch.object(gcal,'confirmation_reply') as classify, patch.object(gcal,'apply_change',return_value='added: Checkup') as apply:
        for answer,decision in [('yes','approve'),('เอาเลย','approve'),('sounds good to me','approve'),('ไม่เอาแล้ว','decline'),('maybe','unclear')]:
            propose(); classify.return_value=decision; apply.reset_mock()
            assert await bot.calendar.answer(channel,42,answer)
            assert apply.call_count == (decision=='approve')
        propose(); classify.reset_mock(); apply.reset_mock()
        assert not await bot.calendar.answer(channel,99,'yes')
        classify.assert_not_called(); apply.assert_not_called()
        classify.return_value='revise'
        revised=dict(plan,start='2026-09-17T14:00:00',end='2026-09-17T15:00:00')
        with patch.object(gcal,'prepare_change',return_value=revised):
            assert await bot.calendar.answer(channel,42,'yes but at two')
        apply.assert_not_called()
        assert bot.calendar.pending[12][0]==revised
        classify.return_value='approve'
        await bot.calendar.answer(channel,42,'that works')
        apply.assert_called_once_with(revised)
        propose(); apply.reset_mock(); classify.side_effect=RuntimeError('offline')
        await bot.calendar.answer(channel,42,'go ahead')
        apply.assert_not_called(); assert 12 in bot.calendar.pending
    print('PASS conversational approval, refusal, uncertainty, revision then approval, wrong speaker, provider failure')
asyncio.run(main())
from tiwa import llm
with patch.object(llm, 'chat', return_value={'content':'{"decision":"approve"}'}) as chat:
    assert gcal.confirmation_reply({'title':'Synthetic'}, 'sounds good') == 'approve'
    args=chat.call_args.kwargs
    assert 'json' in str(args['messages']).lower()
    assert args['fmt']['additionalProperties'] is False

