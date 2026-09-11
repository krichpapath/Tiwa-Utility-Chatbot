# Activated transcription experiment

Branch: `codex/voice-transcription`. Listening remains off by default. The
local model is now trained experimentally from the recorded speaker's samples.
It is **not certified for other speakers or long live gaming sessions**.

## Controls

Dashboard Settings → Voice chat — activation and transcription:

- Listen in voice chat: off/on, independent of music and spoken replies.
- Transcription model: defaults to `openai/gpt-transcribe`; Qwen, Whisper
  Turbo and Fish are selectable. Uses the existing OpenRouter key.
- Answer activated commands: off by default; only displays transcripts.
- Spoken replies: `dj` keeps TTS off; `full` permits spoken replies.
- Silence before stopping: 1.2 seconds by default, range 0.3–5.
- Maximum command length: 20 seconds by default, range 2–30, plus preroll.
- Hey Tiwa detector file and threshold: custom openWakeWord ONNX model.
  The personal experiment uses `data/wake_training/hey_tiwa.experimental.onnx`
  with threshold 0.9. This path is selected locally; listening remains off.

Save settings and restart the bot. These are startup settings, not live mute
buttons. To disable reception immediately, disconnect Tiwa from voice.
No existing `.env` values are changed by this implementation.

## Data flow

Discord user ID → bounded receive queue → local detector and VAD → two-second
RAM preroll → activated speaker recording → silence/max-length boundary →
OpenRouter transcription → channel transcript. Transcript-only mode does not
invoke the persona, memory extraction, tools, or TTS. Transcripts enter the
existing history/log; idle audio is neither uploaded nor saved by this listener.

Per-speaker detectors are capped at eight; concurrent transcription jobs at two.
Excess/stale queued audio is dropped. This is an experimental small-channel
limit, not guaranteed lossless recording. In-flight HTTP calls can finish after
cancellation; timeout is 30 seconds and there is no automatic paid retry.
STT usage is printed separately; the chat token budget does not meter audio.

## Detector preparation still required

Use the dashboard's **Wake recordings** tab to record from a microphone or
upload audio, preview it, and save a named sample. Samples can be labeled
Hey Tiwa or background/no wake word, annotated, renamed, trimmed into a new
copy, or deleted. Files live under `data/wake_recordings/` (or the configured
data directory). Recording samples does not upload them to a model or train
a detector. If the embedded browser has no microphone, use Chrome or Edge
at `http://127.0.0.1:8787` and allow microphone access.

Install the optional `requirements-voice.txt` in the virtual environment and
download openWakeWord's required feature/VAD assets using its documented setup.
Supply a **custom** trained `Hey Tiwa` ONNX model as `TIWA_WAKE_MODEL`.
Missing model/dependencies leave listening off with an explicit status.
Do not substitute the bundled Hey Jarvis detector or transcript fuzzy matching.

The upstream training workflow is English-focused. A Thai-accented personal
Hey Tiwa detector requires recordings and held-out evaluation before enabling.
Use varied positive examples (normal, excited, quiet, different distances) and
negative audio (ordinary Thai, ที่ว่า, game sounds, unrelated conversation).
Keep training and evaluation recordings separate. Two earlier command clips
are a useful regression check, not sufficient training/validation data.

Measure missed activations and false activations per hour separately from STT
accuracy. A detector recognizes an acoustic pattern, not an exact written word.
No model can promise perfect separation of acoustically similar phrases.

## Verification

September 9 experiment: 22 positive and 8 real background samples were present.
Training used 17 positives and 13 negatives (real plus synthetic). The five
remaining positives and six remaining negatives were held out from model fitting.
At threshold 0.6, all five positives activated but three negatives also did.
Threshold 0.9 was selected after looking at those results: recorder replay then
accepted 5/5 positives and rejected 6/6 negatives. This is calibration, not an
independent test of the selected threshold. GPT replay of the new recordings
requires explicit upload approval; live Discord ingress remains unverified.

The gate considers only the first two seconds of detected speech and rearms
after a silence boundary. A mandatory transcript prefix check then rejects
short lead-ins before Hey Tiwa. Known transcription spellings are accepted only
after acoustic detection; idle audio is never activated via fuzzy text matching.

`python tests/activationbench.py` exercises the recording state machine and
provider boundary offline. It does not validate the actual trained detector,
its VAD assets, CPU performance, or Discord microphone ingress.
