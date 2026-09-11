"""Dashboard setting definitions and display labels. No I/O or UI construction."""
SETTINGS = [
    ("Where she thinks", [
        ("TIWA_MODE", "Mode",
         "local = everything on your GPU, ollama must be running, no cost. "
         "mixed = tools and memory local, her replies from the API (best quality). "
         "api = nothing local at all, GPU free for games.",
         "local", ["local", "mixed", "api"]),
        ("TIWA_PERSONA_API_MODEL", "Chat reply model",
         "Writes Tiwa's text replies in API or Mixed mode. Change this to try a different conversational model. This does not select her speaking voice.",
         "deepseek/deepseek-v4-flash", None),
        ("TIWA_TOOL_MODEL", "Tool model (API)",
         "Routes requests to the music, search and calendar minis when tools run "
         "on the API — that is api mode only. Unused in local and mixed.",
         "deepseek/deepseek-v4-flash", None),
        ("TIWA_EXTRACT_MODEL", "Memory model (API)",
         "Selects relevant memories before replies and extracts new memories afterward. "
         "Used in API mode; local and mixed modes use the local memory model.",
         "deepseek/deepseek-v4-flash", None),
        ("TIWA_PERSONA_MODEL", "Force a reply model",
         "Overrides her chat reply model on whichever path is active. Leave empty "
         "unless you are testing one specific model.",
         "", None),
        ("TIWA_DAILY_TOKENS", "Daily token ceiling",
         "Daily chat-token limit. When reached, Tiwa attempts local inference; Ollama must be available. Audio transcription is billed separately. This is not a dollar spending limit.",
         "2000000", None),
    ]),
    ("Discord", [
        ("TIWA_HOME_CHANNEL", "Home channel id",
         "The one channel she may speak in unprompted — at most once every 3 "
         "hours, and only between 09:00 and 23:00. Empty = she never starts a "
         "conversation, she only answers.",
         "", None),
    ]),
    ("Music", [
        ("TIWA_MUSIC_VOLUME", "Volume",
         "1.0 is however loud YouTube handed it over. Set 0.4-0.6 so you can "
         "still hear her talk while a song is playing.",
         "1.0", None),
        ("TIWA_MAX_TRACK_MIN", "Longest track (minutes)",
         "YouTube's top hit for a mood like 'hype gaming EDM' is a 2-3 hour mix, "
         "which outlives the whole conversation and starves the queue. Anything "
         "longer than this is skipped in favour of the next result. Raise it if "
         "you actually want long mixes.",
         "12", None),
    ]),
    ("Memory", [
        ("TIWA_EXTRACT_THINK", "Think before remembering",
         "Turns the model's reasoning on for the memory write pass — the only "
         "pass nobody waits on, since it runs after her reply is already sent. "
         "Measured and NOT recommended: no accuracy gain either provider, and "
         "on the local 8B it was 15x slower and leaked JSON into an entity name "
         "(tests/extractbench.py --think).",
         "0", ["0", "1"]),
    ]),
    ("Eyes", [
        ("TIWA_VISION", "See images",
         "1 = she looks at any image posted with her name and reacts to it. "
         "0 = images are ignored completely and she says she cannot see them. "
         "Costs nothing on messages with no picture — no image, no call.",
         "1", ["1", "0"]),
        ("TIWA_VISION_MODEL", "Vision model",
         "Always an API model, even in local mode: her 8B and a local vision "
         "model do not fit in 8 GB together, so ollama would swap on every "
         "image. About $0.00015 a look at the default.",
         "qwen/qwen3-vl-8b-instruct", None),
    ]),
    ("Her speaking voice", [
        ("TIWA_TTS_TH", "Thai voice",
         "edge-tts voice for Thai replies. Others: th-TH-NiwatNeural (male).",
         "th-TH-PremwadeeNeural", None),
        ("TIWA_TTS_EN", "English voice",
         "edge-tts voice for English replies. Others: en-US-JennyNeural, "
         "en-GB-SoniaNeural.",
         "en-US-AvaNeural", None),
    ]),
    ("Voice chat — activation and transcription", [
        ("TIWA_LISTEN", "Listen in voice chat",
         "1 = detect Hey Tiwa locally, then upload only the activated command. "
         "Requires a trained wake model. 0 = listening off. Restart after saving.",
         "0", ["0", "1"]),
        ("TIWA_STT_MODEL", "Transcription model",
         "OpenRouter model used after activation. GPT performed best on your two clips.",
         "openai/gpt-transcribe", ["openai/gpt-transcribe", "qwen/qwen3-asr-1.7b",
                                   "openai/whisper-large-v3-turbo", "fish-audio/transcribe-1"]),
        ("TIWA_VOICE_REPLY", "Answer activated commands",
         "0 = show transcript only. 1 = also let Tiwa answer and perform requested actions.",
         "0", ["0", "1"]),
        ("TIWA_VOICE", "Spoken replies",
         "dj = spoken replies off; music still works. full = speak replies aloud.",
         "dj", ["dj", "full"]),
        ("TIWA_WAKE_MODEL", "Hey Tiwa detector file",
         "Path to a custom openWakeWord ONNX model. Empty or missing keeps listening off.",
         "", None),
        ("TIWA_WAKE_THRESHOLD", "Activation sensitivity threshold",
         "Higher rejects more false activations but may miss your voice. Range 0.01–1.",
         "0.9", None),
        ("TIWA_RECORD_MAX_S", "Maximum command length",
         "Stop recording after this many seconds even if speech continues. Range 2–30.",
         "20", None),
        ("TIWA_SILENCE_S", "Silence before stopping",
         "Seconds without detected speech before finishing an activated command. Range 0.3–5.",
         "1.2", None),
    ]),
    ("Legacy local speech tests — not used by the wake listener", [
        ("TIWA_WHISPER_MODEL", "Speech model",
         "whisper-base is roughly realtime, English fine, Thai rough. "
         "whisper-small is ~3x slower but much better at Thai.",
         "onnx-community/whisper-base",
         ["onnx-community/whisper-base", "onnx-community/whisper-small"]),
        ("TIWA_WHISPER_LANG", "Language",
         "Pin it if you always speak one language. Empty = auto-detect, which is "
         "slower and sometimes returns nothing at all.",
         "", ["", "th", "en"]),
        ("TIWA_ONNX_THREADS", "CPU threads",
         "4 measured fastest on this machine — 2.3x faster than letting it "
         "choose. More threads is slower, not faster.",
         "4", None),
        ("TIWA_NOISE_FLOOR", "Noise gate",
         "Anything quieter than this is not speech. Measured here: speech 0.09, "
         "quiet speech 0.023, fan hum 0.021, room hiss 0.002. Raise it if she "
         "hears ghosts, lower it if she misses you.",
         "0.02", None),
        ("TIWA_WAKE_FUZZ", "Wake match (latin)",
         "How close a heard word must be to 'Tiwa', 0-1. Lower wakes her more "
         "often, including on the wrong word.",
         "0.72", None),
        ("TIWA_WAKE_FUZZ_TH", "Wake match (Thai)",
         "Same, for ทิวา. Thai is stricter because short Thai words collide easily.",
         "0.8", None),
        ("TIWA_VOICE_DEBUG", "Save what she heard",
         "1 = write every heard clip to data/voice_debug/*.wav so you can listen "
         "back and see why a transcript was nonsense.",
         "0", ["0", "1"]),
    ]),
    ("Logging", [
        ("TIWA_LOG_PROMPTS", "Record model calls",
         "1 = every prompt and reply goes to the model-calls page. Turn it off "
         "only if you want nothing on disk; the page goes empty.",
         "1", ["1", "0"]),
    ]),
]
EDITABLE = {k for _, rows in SETTINGS for k, *_ in rows}
SECRETS = ("DISCORD_TOKEN", "OPENROUTER_API_KEY")

MODE_WORDS = {
    "local": "everything on your GPU — ollama must be running, nothing is billed",
    "mixed": "tools and memory on your GPU, her replies from OpenRouter",
    "api": "nothing local — ollama can be closed, the GPU is free",
}
KIND_WORDS = {
    "turn": "a full reply to someone",
    "tool": "a tool she called",
    "music": "playback",
    "voice": "something heard in voice chat",
    "reflect": "an idle-time conclusion about someone",
    "recall": "Memory Mini selection or fallback",
}
