# Environment variables

Everything she reads from `.env` at startup. **Twenty-two variables.** All of these are
also on the [control panel](../surfaces/panel.md) **Settings** tab with the same explanations.

!!! note "Two rules for all of them"
    1. **Read once, at import.** Changing `.env` needs a restart.
    2. **Empty or absent = the default.** You never have to set one to get sane behaviour.

## Secrets

Never rendered by the panel, never committed, gitignored.

| Variable | What it is | Needed when |
|---|---|---|
| `DISCORD_TOKEN` | Your bot's token from the developer portal | Running `bot.py` |
| `OPENROUTER_API_KEY` | API key, `sk-or-v1-…` | `mixed` or `api` mode |

## Where she thinks

| Variable | Default | What it means |
|---|---|---|
| `TIWA_MODE` | `local` | `local` = everything on your GPU, Ollama must run, free. `mixed` = tools and memory local, her replies from the API. `api` = nothing local, GPU free, costs money. [Full comparison](../concepts/modes.md) |
| `TIWA_PERSONA_API_MODEL` | `deepseek/deepseek-v4-flash` | The API model that writes her actual replies. **This is the one worth shopping for** — it decides how she sounds |
| `TIWA_TOOL_MODEL` | `deepseek/deepseek-v4-flash` | Picks which tools to call, *only* when tools run on the API — i.e. `api` mode. Unused in `local` and `mixed` |
| `TIWA_EXTRACT_MODEL` | `deepseek/deepseek-v4-flash` | Decides what she remembers, when that runs on the API (`api` mode only) |
| `TIWA_PERSONA_MODEL` | auto | Force one specific model for her replies on whichever path is active. Leave empty unless testing |
| `TIWA_DAILY_TOKENS` | `2000000` | Runaway insurance, **not a budget**. Past this she falls back to local for the rest of the day and says so. 2M ≈ $0.30 and is far more than a day of chatting |
| `TIWA_TURN` | `serial` | Which shape a turn has. `serial` = [the three passes](../concepts/three-passes.md). `concurrent` = [the swarm](../concepts/the-swarm.md) — dispatch plus Mini Tiwas, measured 2.7s p50 against serial's 5.1s. Not the default yet |
| `TIWA_VOICE` | `dj` | `dj` = the voice channel is a speaker for music and nothing else; join/leave tools answer honestly instead of acting. `full` = she can also decide to join or leave mid-conversation. Also gates `TIWA_LISTEN` |

## Discord

| Variable | Default | What it means |
|---|---|---|
| `TIWA_HOME_CHANNEL` | off | Channel ID where she may speak **unprompted** — at most once every 3 hours, only 09:00–23:00. Empty means she never starts a conversation, she only answers |

## Music

| Variable | Default | What it means |
|---|---|---|
| `TIWA_MUSIC_VOLUME` | `1.0` | `1.0` is however loud YouTube handed it over. Set `0.4`–`0.6` so you can still hear her talk over a song |
| `TIWA_MAX_TRACK_MIN` | `12` | Longest result she'll accept as a song. YouTube's top hit for a mood is a 2–3 hour mix, which outlives the conversation and starves the queue — anything longer is skipped for the next result. Raise it if you want long mixes back |

## Memory

| Variable | Default | What it means |
|---|---|---|
| `TIWA_EXTRACT_THINK` | `0` | Model reasoning on the memory **write** pass — the one pass nobody waits on, because it runs after her reply is sent. **Measured and not recommended**: no accuracy gain on either provider, and on the local 8B it was ~15× slower and leaked JSON into an entity name. See [ADR-019](decisions.md#adr-019) |

## Her eyes

| Variable | Default | What it means |
|---|---|---|
| `TIWA_VISION` | `1` | `1` = she looks at images posted with her name and reacts. `0` = images ignored entirely, and she says she can't see them. Messages with no picture cost nothing either way — no image, no call |
| `TIWA_VISION_MODEL` | `qwen/qwen3-vl-8b-instruct` | Always an API model, even in `local` mode: her 8B and a local vision model don't fit in 8 GB together. ~$0.00015 a look. See [her eyes](../surfaces/eyes.md) |

## Her speaking voice

| Variable | Default | What it means |
|---|---|---|
| `TIWA_TTS_TH` | `th-TH-PremwadeeNeural` | edge-tts voice for Thai replies. Alternative: `th-TH-NiwatNeural` (male) |
| `TIWA_TTS_EN` | `en-US-AvaNeural` | edge-tts voice for English replies. Alternatives: `en-US-JennyNeural`, `en-GB-SoniaNeural` |

## Her ears — speech-to-text

!!! warning "All of these only matter when `TIWA_LISTEN=1`"
    Which is off by default. See [voice in](../surfaces/voice-in.md).

| Variable | Default | What it means |
|---|---|---|
| `TIWA_LISTEN` | `0` | `1` = transcribe voice chat and answer lines that **start** with her name. Currently `0`: Thai accuracy on CPU wasn't good enough |
| `TIWA_WHISPER_MODEL` | `onnx-community/whisper-base` | `whisper-base` is roughly realtime, English fine, Thai rough. `whisper-small` is ~3× slower and much better at Thai |
| `TIWA_WHISPER_LANG` | auto | Pin to `th` or `en` if you always speak one. Empty = auto-detect, which is slower and sometimes returns nothing at all |
| `TIWA_ONNX_THREADS` | `4` | CPU threads for transcription. **4 measured fastest here** — 2.3× faster than letting onnxruntime choose. More is slower |
| `TIWA_NOISE_FLOOR` | `0.02` | Below this loudness it isn't speech. Measured: speech 0.09, quiet speech 0.023, fan hum 0.021, room hiss 0.002. Raise it if she hears ghosts, lower it if she misses you |
| `TIWA_SILENCE_S` | `1.2` | Seconds of quiet that end a sentence. Lower feels snappier but chops sentences, and Whisper is much worse on fragments |
| `TIWA_WAKE_FUZZ` | `0.72` | How close a heard word must be to "Tiwa", 0–1. Lower wakes her more often, including on the wrong word |
| `TIWA_WAKE_FUZZ_TH` | `0.8` | Same for ทิวา. Stricter, because short Thai words collide easily |
| `TIWA_VOICE_DEBUG` | `0` | `1` = write every heard clip to `data/voice_debug/*.wav` so you can hear why a transcript was nonsense |

## Logging

| Variable | Default | What it means |
|---|---|---|
| `TIWA_LOG_PROMPTS` | `1` | `1` = every prompt and reply goes to the model-calls page. Turn off only if you want nothing on disk; the page then goes empty |

## Example `.env`

```ini
# Secrets — never commit this file
DISCORD_TOKEN=REPLACE_ME
OPENROUTER_API_KEY=sk-or-v1-REPLACE_ME

# Where she thinks
TIWA_MODE=mixed

# Music quiet enough to talk over
TIWA_MUSIC_VOLUME=0.5
```

Everything else can stay unset.

## Not environment variables

Things you might expect here but that live in code:

| Setting | Where | Why |
|---|---|---|
| Her personality | `prompts/tiwa.md` | Too big for an env var; belongs in version control as prose |
| Local model name | `tiwa/llm.py` `LOCAL_MODEL` | Changing it means re-testing every pass |
| Episode cap, log caps | `tiwa/memory.py` | Chosen, not tuned — see [conventions](../work/conventions.md#numbers-in-the-code-are-mostly-chosen-not-measured) |
| Calendar timezone | `tiwa/gcal.py` `TZ` | Hardcoded `Asia/Bangkok` |
| Quiet hours for idle chat | `bot.py` | 09:00–23:00, hardcoded |
