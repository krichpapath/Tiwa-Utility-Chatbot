# Testing

## Repeatable checks

```powershell
.\.venv\Scripts\python.exe -X utf8 tests/acceptance.py --list
.\.venv\Scripts\python.exe -X utf8 tests/acceptance.py
.\.venv\Scripts\python.exe -X utf8 tests/acceptance.py musicqueuebench calendarconversationbench
```

The runner uses isolated temporary data and subprocesses. Results and individual
logs go to `qa-results/acceptance/`; these are generated and not versioned. The
suite is a curated list of offline checks; not every file in `tests/` is offline.
Tests using live providers must be invoked explicitly.

| Area | Start with |
|---|---|
| Discord routing | `discordbench`, `deploymentbench`, `outagebench` |
| Music | `musicqueuebench`, `musicintentbench`, `musicartistbench`, `searchqualitybench` |
| Voice | `activationbench`, `groupvoicebench`, `listenstartupbench`, `routerbench`, `daverecoverybench` |
| Calendar | `calendarconfirmbench`, `calendarconversationbench`, `calendarproofbench`, `calendareditbench` |
| Memory | `test_memory`, `memoryrecallbench`, `namebench`, `relationshipbench`, `tastebench` |
| Dashboard | `panelbench`, `dashboardbench`, `recordingsbench` |
| Turn orchestration | `forkbench`, `latebench`, `turnbench`, `minibench` |

## Explicit integration checks

`scripts/` contains service/Discord/calendar probes and wake training/replay tools.
Read each script's docstring and arguments before running it. Live checks can spend
API credit, post Discord messages, or create/delete calendar events. Recording
replay and training stay local unless a transcription option is explicitly used.

`tests/listenstartupbench.py --real` loads the actual wake model while measuring loop
responsiveness against a fake voice connection. `tests/musicartistbench.py --live`
uses public search/AI and decodes audio locally. Neither proves real Discord audio
transport. A live VC check must cover join, wake, transcription, action and reconnect.

## Release evidence

Report the exact checks, whether services were mocked, and any outstanding live
verification. Offline success is not proof of provider availability, live DAVE
interoperability, current search results or correct wake behavior for every speaker.
