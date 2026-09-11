# Configuration

`.env` is loaded by `tiwa.llm`. Existing process/system environment variables take
precedence; startup reports conflicts. The dashboard changes `.env`; restart the
bot to apply settings. Never put credentials in documentation or test fixtures.

| Setting | Meaning |
|---|---|
| `DISCORD_TOKEN` | Discord application bot credential |
| `OPENROUTER_API_KEY` | API inference credential |
| `TIWA_MODE` | `local`: local inference; `mixed`: local tools/memory with API persona; `api`: API inference |
| `TIWA_TOOL_MODEL` | API specialist/dispatch model |
| `TIWA_EXTRACT_MODEL` | API memory model |
| `TIWA_PERSONA_API_MODEL` | API conversation model |
| `TIWA_HOME_CHANNEL` | Text channel for unsolicited activity; empty keeps it quiet |
| `TIWA_OWNER_ID` | Fallback owner for proposals without a requester; normal approval uses requester ID |
| `TIWA_CALENDAR_ID` | Calendar ID from Google settings; default `primary` |
| `TIWA_VOICE` | Spoken-reply behavior: `dj` or `full` |
| `TIWA_LISTEN` | Enable incoming voice processing independently of spoken replies |
| `TIWA_VOICE_REPLY` | Allow accepted transcriptions to trigger replies/actions |
| `TIWA_WAKE_MODEL` | Path to personal wake ONNX model |
| `TIWA_WAKE_THRESHOLD` | Local activation score threshold; validate changes with negative audio |
| `TIWA_SILENCE_S` | Silence required to end a captured utterance |
| `TIWA_RECORD_MAX_S` | Maximum captured utterance length |
| `TIWA_DATA_DIR` | Alternate private runtime-data directory, used by tests |

See `.env.example` for a starting profile and `tiwa/dashboard_settings.py` for the
full editable setting catalog and defaults. STT provider/model controls are there
as well. `api` refers to language-model inference; local wake/audio processing still
runs on this machine. Chat token limits and STT accounting are separate.
