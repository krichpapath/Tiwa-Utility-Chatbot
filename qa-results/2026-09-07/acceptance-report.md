# Tiwa acceptance verdict: BLOCKED

Date: 2026-09-07. Checkout: C:\Work\Tiwa, branch swarm, existing uncommitted changes.

Acceptance cannot be granted. One offline script passed; 21 could not reach their checks. This is an execution-environment blocker, not evidence of 21 application defects. No application fixes, dependency installs, system-variable changes, Discord messages, or calendar writes were performed.

## Blocking findings

1. The documented command cannot launch here: `py` is not recognized. Neither `python` nor `python3` resolved through Get-Command. Targeted standard install-directory checks found no alternative interpreter. This establishes a limitation of this agent shell, not that Python is absent everywhere on the host.
2. The available bundled Python lacks Tiwa dependencies. Observed missing imports: httpx, discord, gradio, ddgs, google. Pre-flight runtime imports fail before startup completes.
3. Calendar probe returned exactly `calendar unavailable: No module named 'google'`. Google was not reached. Previously reported invalid_grant remains unverified, neither confirmed nor resolved.

## Pre-flight

| Item | Observed |
|---|---|
| Branch | swarm |
| Working tree | Pre-existing edits to dashboard.py, graph_view.py and docs; untracked .claude/ and .codex/ |
| Inherited OPENROUTER_API_KEY | Not set in agent process; no shadowing observed |
| File API key | Present; suffix 7b80; expected suffix not supplied |
| Startup banner | Not reached: ModuleNotFoundError for httpx |
| TIWA_MODE | api in .env; runtime value not reached |
| TIWA_VOICE | Absent/empty in parsed configuration; tools.py selects DJ-only unless value is full |
| TIWA_LISTEN | 0 |
| DISCORD_TOKEN | Present in .env; validity not tested |
| TIWA_HOME_CHANNEL | Present; user authorized this channel for tests |
| music.ready(), runtime mini registry | Not reached due to import failure |
| Calendar | calendar unavailable: No module named 'google' |

Key presence checked without printing secrets. Requested initial Python check failed because py is unavailable; equivalent PowerShell process-environment check established absence. File configuration is not presented as successful runtime validation.

## Layer 1: all 22 requested scripts attempted

Interpreter: C:\Users\krich\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe

Command pattern: & '<interpreter>' -X utf8 'tests\<script>.py'

| Script | Result | Exit | Evidence |
|---|---|---|---|
| test_memory | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| djbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'discord' |
| discordbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'discord' |
| panelbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'gradio' |
| turnbench | PASS | 0 | concurrent ok — neither turn saw the other's music, DJ list or calendar |
| minibench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| djminibench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| forkbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| latebench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| searchminibench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| calminibench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| growthbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| recordbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| tastebench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| calbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| searchbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'ddgs' |
| routerbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'discord' |
| wakebench | BLOCKED | 1 | ModuleNotFoundError: No module named 'discord' |
| dumpbench | BLOCKED | 1 | ModuleNotFoundError: No module named 'discord' |
| noisebench | BLOCKED | 1 | ModuleNotFoundError: No module named 'discord' |
| outagebench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |
| eyebench | BLOCKED | 1 | ModuleNotFoundError: No module named 'httpx' |

turnbench passed: `concurrent ok — neither turn saw the other's music, DJ list or calendar`. This proves only that bench's isolated concurrent-state assertions. It does not prove live Discord behavior.

Individual .log files preserve raw stdout/stderr. offline-results.csv provides machine-readable results.

## Remaining acceptance coverage

| Layer | Status | Reason |
|---|---|---|
| Live model benches | NOT RUN | Explicit offline-green gate not met |
| Live Discord interaction | NOT RUN | Gate not met; no bot started |
| Error injection and visible warning recovery | NOT RUN | Gate not met; outagebench itself blocked |
| Heartbeat, taste pass, ask-nudge over time | NOT RUN | Gate not met; no timed scenario started |
| Google Calendar end-to-end | BLOCKED | Missing client dependencies; OAuth not exercised |
| Voice listening and speech | UNPROVEN | Listening disabled in config; no live call tested |
| Late search second message | UNPROVEN | Neither offline latebench nor live Discord test completed |
| Panel, graph viewer, auth helper, restart runner end-to-end | NOT RUN | Environment incomplete; documented launcher unavailable |

The supplied spec ended after Layer 1. Exact interaction/error matrices, live bench commands and report template were not provided. User directed use of .env and TIWA_HOME_CHANNEL. No substitute interaction results were invented.

## Maintainer action

Provide an existing Tiwa Python environment with requirements installed and accessible to this task, or request environment setup. Then rerun pre-flight, confirm expected key suffix and actual startup banner, and rerun all 22 scripts. Advance only after this gate is green. Probe Calendar again before assuming OAuth is usable; invalid_grant, if reproduced, requires user reauthorization. Full acceptance still requires real Discord replies and timed-path evidence.
