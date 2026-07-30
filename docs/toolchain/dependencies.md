# Dependency table

Every package, what it does, why it's here, and what replacing it would cost. Sixteen,
all declared in `requirements.txt`.

!!! tip "The bar for adding one"
    Standard library first, then a single-purpose package, then — reluctantly — a
    framework. There is no agent framework, no ORM, no web framework, and no vector
    database in this project, and each of those absences is deliberate. See
    [decision log](../reference/decisions.md).

## Declared

| Package | What it does | Why chosen | Used in | Cost to replace |
|---|---|---|---|---|
| **discord.py** | Discord client, events, voice send | The reference Python library; mature and typed | `bot.py`, `tiwa/voice.py`, `tiwa/music.py` | **Very high** — the event model is the app's shape |
| **discord-ext-voice-recv** | Receive voice audio (discord.py can't) | Only maintained option. Alpha, and patched twice | `tiwa/voice.py` | High, isolated to `voice.py` |
| **PyNaCl** | Voice transport encryption | Required by discord.py for voice | transitive | n/a |
| **ollama** | Local model client | Official client for the local server | `tiwa/llm.py`, `tiwa/gcal.py` | Low — one function |
| **httpx** | HTTP for OpenRouter | Modern, timeouts that work, already a discord.py dep | `tiwa/llm.py` | Low — `requests` or `urllib` |
| **ddgs** | DuckDuckGo search | No key, no quota, one call. `region`/`max_results` are all the tuning [search](../concepts/search.md) needs | `tiwa/tools.py` (`web_search`) | Low — one function, but every alternative wants a key |
| **google-api-python-client** | Calendar REST | Official | `tiwa/gcal.py` | Medium — raw REST is doable |
| **google-auth-oauthlib** | OAuth consent + token refresh | Official; handles the desktop flow | `gcal_auth.py`, `tiwa/gcal.py` | Medium — don't hand-roll OAuth |
| **onnx-asr** | Whisper on onnxruntime | Runs under Smart App Control; no PyTorch | `tiwa/voice.py` | Medium |
| **onnxruntime** | Inference engine | **Microsoft-signed** — the reason speech works here | via onnx-asr | High |
| **soundfile** | Decode mp3 → PCM | Avoids an ffmpeg binary | `tiwa/voice.py` | Low |
| **edge-tts** | Text to speech | Free, no key, has **Thai** neural voices | `tiwa/voice.py` | Low, but Thai narrows the field hard |
| **yt-dlp** | YouTube search + direct audio URL | The only thing that reliably keeps working | `tiwa/music.py` | High — nothing else is as maintained |
| **av** (PyAV) | Decode the audio stream | FFmpeg libs as a wheel, no binary | `tiwa/music.py` | Medium |

## Declared because the code imports them directly

These two are also transitive dependencies of packages above, which is how they got
missed. Relying on that was luck, not a contract — a slimmer resolve would have broken
music and voice on import.

| Package | What it does | Imported by | Cost to replace |
|---|---|---|---|
| **numpy** | Array maths on audio buffers — RMS for the noise gate, peak checks | `tiwa/voice.py`, `tiwa/music.py` | Medium. `array` + `math` could do the RMS, but onnx-asr wants numpy anyway |
| **davey** | Discord's DAVE (E2EE voice) session — `decrypt()` for received audio | `tiwa/voice.py` | High. Without it she cannot hear anything; see [transport](discord-transport.md) |

## Docs only

| Package | What it does |
|---|---|
| **mkdocs-material** | This site. In `requirements-docs.txt`, never imported by the bot |

## What is deliberately absent

| Not used | Why not |
|---|---|
| LangChain / LlamaIndex / any agent framework | The 30-line registry in `tools.py` does the job. A framework would add indirection and its own opinions |
| An ORM (SQLAlchemy, Peewee) | Five tables and hand-written SQL. An ORM would be more code, not less |
| A vector DB (Chroma, FAISS, pgvector) | Recall is a substring scan over a small graph. FTS5 comes first if that ever hurts — it's built into SQLite |
| A web framework (Flask, FastAPI) | The control panel is `http.server`, one localhost user, no dependency |
| `python-dotenv` | `llm.load_env()` is 8 lines and does exactly what's needed |
| `pytest` | Benches are runnable scripts that print tables you read. See [the bench suite](../work/testing.md) |
| ffmpeg (system binary) | PyAV and soundfile cover it. See [audio and speech](audio-speech.md) |
| `faster-whisper`, `hf_xet` | Blocked by Smart App Control — unsigned DLLs |
| Piper TTS | No Thai voices |
| Reinforcement learning / RLHF | Adaptation is memory-first. Fine-tuning is planned but needs months of real logs first |

## Counting the cost

Total runtime dependencies: **16**, for a bot that does
LLM chat, tool calling, a knowledge graph, Discord text, Discord voice send *and*
receive, speech-to-text, text-to-speech, YouTube streaming, Google Calendar, web search,
and a web control panel.

That ratio is the point. Each one has a job you can name in a sentence.
