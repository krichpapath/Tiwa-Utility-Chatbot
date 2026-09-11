# Manual tools

These commands are not the offline acceptance suite. Run from the repository root
with `.venv\Scripts\python.exe -X utf8 scripts/<name>.py`. Read the script first.

| Script | Purpose / external effects |
|---|---|
| `servicecheck.py` | Live configured providers, Calendar read, web/music search; API costs possible |
| `discordlive.py` | Explicit Discord integration checks; can post test messages |
| `discordhuman.py` | Human-ingress test session in Discord |
| `calendarlive.py` | Calendar integration exercise; can create/edit/delete QA events |
| `searchqualitylive.py` | Live search evidence checks; API costs possible |
| `trainwake.py` | Local training; use `--output` to protect the active model |
| `wakereplay.py` | Local detector replay; `--transcribe` uses configured paid STT |
| `wakecompare.py` | Compare a candidate model with the current model on held-out replay variants |

Private recordings and credentials stay in `data/` and `.env`; generated output goes
to ignored local directories. The old continuous-transcription voice exercise was
removed with its unused listener; active voice checks are under `tests/`.
