# Development workflow

## Find the right file

Start at the [architecture map](architecture.md), then follow a request from
`bot.py` through `pipeline.respond`. Change a specialist decision in `minis.py`;
change an external operation in `music.py` or `gcal.py`; change Discord execution in
the corresponding controller. Styling is in `assets/dashboard.css`, and dashboard
setting definitions are in `tiwa/dashboard_settings.py`.

Keep functions small enough to name their responsibility. Prefer existing libraries
and helpers over frameworks, adapters or generalized plugin systems. Keep comments
about invariants and failure modes; put project history in commit messages.

## Change and verify

```powershell
git switch -c codex/my-change
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -X utf8 tests/acceptance.py musicqueuebench
.\.venv\Scripts\python.exe -X utf8 tests/acceptance.py
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

Ruff is pinned in `requirements-dev.txt`; its rules and Python target are in
`pyproject.toml`. Run `ruff format .` to apply formatting. Keep formatting-only
commits separate from behavior changes when practical.

Tests are plain Python assertions, run in separate processes. Add a focused check
for changed behavior and register it in `tests/acceptance.py`. Avoid tests that
assert incidental source formatting. Mock provider calls in offline tests; use
an explicit live script for service checks. Never use real Discord accounts,
calendar events or private recordings as fixtures.

## Git and release

Use focused commits with a concrete change and validation in the message or PR.
Do not commit `.env`, Google credentials, SQLite databases, voice recordings,
trained models or generated QA output. Keep migrations and startup compatibility
visible in the docs. Before merging into `main`, fetch remote changes, run the
full offline suite and strict docs build, and inspect the final diff.

The earlier development histories remain in Git. `main` is the integration target;
old `swarm`/voice branch names do not select different runtime modes. The scripts
`bot.py`, `dashboard.py`, `chat.py`, `graph_view.py`, and `gcal_auth.py` remain stable
entry points.
