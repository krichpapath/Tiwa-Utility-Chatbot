# Setup

Use Python 3.12 on Windows. Run commands from the repository root.

```powershell
./setup.ps1
# If Python cannot be found:
./setup.ps1 -Python C:\path\to\python.exe
```

The script creates `.venv`, installs `requirements-lock.txt`, runs `pip check`, and
runs offline acceptance. It does not replace `.env` or connect the Discord bot.
`requirements.txt` describes runtime dependencies; the lock captures tested versions.

On a fresh installation, copy `.env.example` to `.env`. Set `DISCORD_TOKEN` and, for
API inference, `OPENROUTER_API_KEY`. Enable Message Content Intent in the Discord
application. Give the bot access to the chosen text/voice channels.

```powershell
.\.venv\Scripts\python.exe -X utf8 bot.py
.\.venv\Scripts\python.exe -X utf8 dashboard.py --open
```

The dashboard listens on `127.0.0.1:8787`. It can edit secrets-bearing configuration
and delete memory, so keep it local. Settings take effect after bot restart.

For local inference install Ollama and pull `huihui_ai/qwen3-abliterated:8b`.
For wake detection install `requirements-voice.txt` and configure an existing ONNX
model. These recordings/models are private local files and are not in Git.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m mkdocs serve
```

See [configuration](configuration.md) for provider modes and [operations](operations.md)
for launch and recovery. Do not use a different system Python to start an existing
installation: native audio packages must come from the same virtual environment.
