# Tiwa · ทิวา

Thai/English Discord companion with music queues, voice commands, conversational memory,
web search, and Google Calendar. The runtime uses a main conversation pipeline and
specialist DJ, Search, Calendar, and Memory passes.

## Start here

- **New developer:** [architecture and file map](docs/architecture.md), then [development workflow](docs/development.md).
- **Run Tiwa:** [setup](docs/setup.md) and [deployment checklist](DEPLOYMENT.md).
- **Change behavior:** [configuration](docs/configuration.md) and [feature guides](docs/index.md).
- **Check a change:** [testing](docs/testing.md).

Use Python 3.12 and the project virtual environment:

```powershell
./setup.ps1
.\.venv\Scripts\python.exe -X utf8 bot.py
# Optional local dashboard:
.\.venv\Scripts\python.exe -X utf8 dashboard.py --open
```

Copy `.env.example` to `.env` on a new installation and fill in the required keys.
Do not overwrite an existing `.env`. Voice wake detection needs the optional voice
dependencies and a separately trained local ONNX model; see [voice setup](docs/voice.md).

## Repository layout

| Path | Responsibility |
|---|---|
| `bot.py` | Discord events and application wiring |
| `dashboard.py` | Local Gradio dashboard and callbacks |
| `chat.py`, `graph_view.py`, `gcal_auth.py` | Terminal chat, memory graph, Google authorization |
| `tiwa/` | Runtime logic; see the architecture map |
| `prompts/` | Tiwa's conversation persona |
| `assets/` | Dashboard stylesheet |
| `tests/` | Repeatable regression checks |
| `scripts/` | Explicit live-service checks and local wake training |
| `docs/` | Current developer documentation |
| `data/`, `qa-results/` | Local runtime data and generated reports; ignored by Git |

One process and one music deck are supported. Pending approvals and music queues are
in memory and do not survive restart. Test results are evidence for the checks run,
not a guarantee of Discord, YouTube, or provider availability.
