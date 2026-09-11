# Voice input and output

`voice.py` owns transport and recovery. `listening.py` owns the active listener.
The old continuous-transcription listener has been removed. `stt.py` selects the
configured transcription provider; local legacy STT helpers remain for provider
support and audio diagnostics.

1. Discord PCM is separated by user ID.
2. A local personal ONNX detector checks for the wake phrase at the utterance start.
3. Activated audio is captured until silence or the duration cap.
4. STT receives only accepted clips. Local wake acceptance does not require the
   transcript to spell Tiwa correctly.
5. The transcript is mirrored into the linked text channel, then routed normally.

A pending calendar proposal opens one wake-free response for its requester for
60 seconds. No other speaker inherits that permission. At most five activated
commands wait behind the active command; overflow is skipped without STT billing.

`await voice.listen(...)` constructs the detector in a worker thread. The constructor
rejects event-loop loading, and worker construction creates no asyncio tasks.
The sink starts on the loop only after the voice connection is checked again.
DAVE/Opus recovery never treats arbitrary undeciphered audio as valid plaintext.

## Personal model training

Save positive and negative samples in the dashboard. Include normal background
speech and phrases that resemble the wake word. Training runs locally:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts/trainwake.py --varied --output data/wake_training/candidate
.\.venv\Scripts\python.exe -X utf8 scripts/wakecompare.py data/wake_training/candidate
```

Use a separate output directory. Compare detection and false triggers before changing
`TIWA_WAKE_MODEL`. Existing holdout results are not independent evidence after repeated
tuning. Fresh samples from the actual microphone are needed to measure new failures.

Tests: `activationbench`, `groupvoicebench`, `listenstartupbench`, `routerbench`,
`daverecoverybench`. Spoken output is controlled separately through voice settings.
