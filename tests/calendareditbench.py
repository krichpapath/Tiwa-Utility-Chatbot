"""Offline edit guards. No Google calls or user-event changes."""
from pathlib import Path
import sys
from unittest.mock import patch, MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tiwa import gcal
plan=dict(action='edit',title='QA',start='2026-09-10T18:00:00',new_title='Updated')
hit=dict(id='fixture',summary='QA',etag='revision1',start={'dateTime':'2026-09-10T18:00:00+07:00'},end={'dateTime':'2026-09-10T18:15:00+07:00'})
with patch.object(gcal,'_service') as service:
    events=service.return_value.events.return_value
    events.list.return_value.execute.return_value={'items':[hit]}
    events.patch.return_value.execute.return_value={'summary':'Updated','htmlLink':'https://example.test/event'}
    assert gcal.apply_change(plan).startswith('edited:')
    body=events.patch.call_args.kwargs['body']
    assert body['start']['dateTime']==hit['start']['dateTime']
    assert body['end']['dateTime']==hit['end']['dateTime']
    events.patch.return_value.headers.__setitem__.assert_called_with('If-Match','revision1')
    events.patch.reset_mock()
    events.list.return_value.execute.return_value={'items':[hit,hit]}
    assert 'multiple matching' in gcal.apply_change(plan)
    events.patch.assert_not_called()
    events.list.return_value.execute.return_value={'items':[]}
    assert "couldn't find" in gcal.apply_change(plan)
    events.patch.assert_not_called()
for bad in [dict(plan,new_title=''),dict(plan,new_start='yesterday'),dict(plan,new_start='2026-09-10T19:00',new_end='2026-09-10T18:00')]:
    with patch.object(gcal,'_service') as service:
        assert gcal.apply_change(bad).startswith('calendar change failed:')
        service.assert_not_called()
assert 'Changes: title = Updated' in gcal.describe_change(gcal.validate_change(plan))
print('PASS edit preview, duration, revision guard, missing/ambiguous target, invalid changes')
