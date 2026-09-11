# Operations and limits

Start one bot process with `start_tiwa.cmd` or the virtual-environment Python.
`run_all.cmd` also launches the dashboard; `run_forever.cmd` restarts the bot after
an abnormal exit. Avoid two bot instances receiving the same Discord events.

## Diagnose the failing stage

| Symptom | Check |
|---|---|
| No transcript | Wake model, microphone input, `TIWA_LISTEN`, receiver health |
| Transcript but no action | DJ/Calendar mini result and pending-action dispatch |
| `selected index -1` | Search evidence was rejected; inspect query and candidates |
| Correct song but cannot play | Stream resolution, YouTube availability, Opus/PyAV |
| Calendar says saved without a link/result | Inspect the actual controller/API result, not persona text |
| Heartbeat blocked during model loading | Confirm new code is running; all `voice.listen` calls must be awaited |
| Extra voice gateway `seq` key | Informational schema drift, not itself a failure |
| Repeated DAVE/Opus failures | Receiver recovery logs, connection state and dependency versions |

Wake loading runs in a worker thread and may take tens of seconds on a cold system.
The receiver can recover from packet failures, but live transport compatibility
must still be tested against Discord. Native crashes are captured in `data/crash.log`.

## Current boundaries

- One process, one music deck; multi-server playback is not isolated.
- Music queues and pending approvals do not survive process restart.
- A calendar write with an uncertain network result must be checked in Google
  before retrying; automatic retries could create a duplicate.
- Calendar approval uses the requester Discord ID, not display-name matching.
- A proposal opens one wake-free voice response for that requester for 60 seconds;
  the proposal itself expires after ten minutes.
- The trained personal wake detector is not a general speaker-independent model.
- Local dashboard chat reports Discord-only actions; it does not execute them.

Keep dashboard access local. Back up private `data/` and credentials outside Git.
