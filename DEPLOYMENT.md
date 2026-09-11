# Deployment checklist

Use [setup](docs/setup.md) and [configuration](docs/configuration.md) for a new install.
One process and one server/music deck are the supported starting profile.

Before starting:

```powershell
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -X utf8 tests/acceptance.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mkdocs build --strict
```

Install optional voice dependencies and supply a trained ONNX model if listening is
enabled. Start with `start_tiwa.cmd` or `.venv\Scripts\python.exe -X utf8 bot.py`.
The dashboard remains local. Keep credentials, voice data and database backups private.

After restart, verify a real Discord message, music search/playback, a voice activation,
and a calendar proposal/approval/readback in a designated test channel/calendar. Check
that reconnecting does not lose receiver health. Offline acceptance and local stream
decoding do not substitute for those live checks.

Generated results are in `qa-results/acceptance/`. Do not claim live certification
from historical reports. See [operations](docs/operations.md) for failure stages,
restart behavior, unsupported multi-server state, and uncertain-write handling.
