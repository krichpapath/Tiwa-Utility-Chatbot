# First deployment

This checkout is a deployment candidate for a supervised, single-server launch.
See [the acceptance report](qa-results/acceptance/REPORT.md) for evidence and limits.
Live voice was explicitly skipped. Actual Google Calendar writes still need an
owner-approved end-to-end check. Do not describe those paths as live-certified.

## Install and verify

Use Python 3.12 on Windows. The current machine already has `.venv` configured.
On a fresh machine, run `./setup.ps1` from PowerShell. If Python is not on PATH,
use `./setup.ps1 -Python 'C:\path\to\python.exe'`. This creates `.venv`, installs
the captured dependency versions, checks dependencies and runs offline acceptance.
It never overwrites `.env` or connects Discord.

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -X utf8 tests\acceptance.py
.\.venv\Scripts\python.exe -X utf8 tests\servicecheck.py
```

The last command uses configured services and a small paid model probe, reads
Calendar, and searches YouTube/web. It does not post messages or change Calendar.
`requirements-lock.txt` records the tested Windows environment; update deliberately
and rerun checks. Successful package installation alone does not prove native DLLs load.

On a new install, copy `.env.example` to `.env` and fill the existing bot/API keys.
Keep `TIWA_MODE=api`, `TIWA_VOICE=dj`, `TIWA_LISTEN=0` for this launch profile.
`TIWA_HOME_CHANNEL` enables unsolicited messages; leave empty for a quiet launch.
Existing system environment values override `.env`; startup reports conflicts.

## Start

```powershell
.\.venv\Scripts\python.exe -X utf8 bot.py
```

Check the login banner and `music ready` before asking for music. If Windows reports
Application Control blocking a decoder, keep protection enabled and resolve the
runtime's trust/compatibility with the machine administrator. This was observed
transiently during QA; a later fresh process decoded real audio successfully.

For the local panel:

```powershell
.\.venv\Scripts\python.exe -X utf8 dashboard.py --open
```

The panel binds only `127.0.0.1:8787`. It edits local settings and memory. Changes to
settings need a bot restart. Panel chat reports music/voice/calendar actions but does
not perform them. Search follow-ups are included in its returned answer.

Terminal chat: `.\.venv\Scripts\python.exe -X utf8 chat.py Krich`.
Memory graph: `.\.venv\Scripts\python.exe -X utf8 graph_view.py --open`.

After a supervised session, `run_forever.cmd` restarts the bot after a nonzero exit.
It uses this project's `.venv`, waits five seconds, and exits after a clean stop.
Automatic login startup and a long-running soak have not been acceptance-tested.

## Calendar approval

Calendar reads use the existing Google authorization. Reauthorize only when needed:
`.\.venv\Scripts\python.exe -X utf8 gcal_auth.py`.

Only `TIWA_OWNER_ID`, or the Discord application's owner when unset, can approve or
reject changes. The proposal shows the parsed title, action, start, end and timezone.
Approval applies that exact plan without another model interpretation. Proposals
expire after ten minutes, are one-shot, and are lost safely on process restart.
Use a private/trusted channel for personal calendar questions; reads are shared with
whoever can converse with the bot, not protected by the write-approval gate.

For a real write acceptance check, ask for an unmistakably named future test event,
inspect all details, approve with the owner's account, verify it in Google Calendar,
then request its exact cancellation and approve that separately. Do not approve a
plan whose date differs from the request. Do not repeat a write after a network
timeout until checking Google Calendar for an already-created event.

## Supported limits and recovery

- One bot process and one server/deck for the first deployment. Music state remains
  global. Multi-server music and concurrent identity isolation are not certified.
- The daily token ceiling prevents subsequent paid calls once recorded usage reaches
  the threshold; calls already in flight can exceed it. Fallback needs local Ollama.
  Without Ollama, the failed call is reported. Usage writes are locked across processes.
- Back up `.env`, OAuth files and `data` privately with the bot and panel stopped.
  For rollback, stop both, restore the prior source/environment and matching data backup.
  Do not delete the memory database to repair a provider or decoder problem.
- Supervise model answers: memory guards reject tested invented facts, but generated
  dialogue can still invent anecdotes or give weak search summaries. These tests are
  evidence for the exercised cases, not a proof against every possible prompt.
- Home Assistant, saved routines, correction-learning and fine-tuning remain roadmap
  work; this acceptance pass did not implement features absent from the project.

QA uses `TIWA_DATA_DIR` for disposable storage. `TIWA_SPEND_FILE` can keep paid usage
in the shared real ledger while QA memory stays isolated. Do not carry QA overrides
into the production shell.
