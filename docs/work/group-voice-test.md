# Group voice acceptance

Run the offline checks (synthetic audio, mocked STT, no charges):

```powershell
cd C:\Work\Tiwa
.\.venv\Scripts\python.exe -X utf8 tests\groupvoicebench.py
```

## Live setup

Use three consenting participants with headphones, one bot instance, and a test text channel. Restart the bot after code changes; join VC and send `@Tiwa join` in that text channel. Activated clips use paid STT. Start with short questions; then test music actions. Say Hey Tiwa at the start of EACH command and leave at least 1.2 seconds of silence between commands from the same person.

| Scenario | Steps | Pass condition |
|---|---|---|
| Each speaker alone | A, B, C separately say “Hey Tiwa, say hello”. | Each receives one transcript/reply. Establish this before overlapping voices. |
| Overlapping background | A asks “Hey Tiwa, what is two plus two?” while B and C chat normally. | Only A's request is acted on; B/C are not mixed into A's transcript. |
| Two simultaneous requests | A asks the arithmetic question; B asks “Hey Tiwa, say hello”. | Both accepted requests finish, one at a time. Order is completed-clip queue order, not who started speaking first. |
| Follow-up while answering | After A finishes a request, B asks another while Tiwa is processing A. | B's request is retained and answered after A. |
| Same person, several requests | A gives three separate greetings/requests with silence between them. | Three ordered responses, no missing request within queue capacity. |
| Rejected speech | Include “I said Hey Tiwa yesterday” in background conversation, then give a valid request. | Background mention causes no action or Discord rejection message. Next request works. |
| Music sequence | Ask to play a specific song, then queue a different specific song, then stop. | Commands execute in order; queue means play later, play means replace now, stop clears the deck. |
| Speaker leaves | A leaves after submitting; B makes a request. | B continues working. An already queued request from A may still execute. |

Record speaker, spoken request, resulting transcript/action, delay, and console errors for each case. Stop repeated live retries if a speaker cannot activate alone: collect that speaker's positive AND background recordings and retrain first.

## Limits / what these tests do not prove

- Offline checks use a fake wake detector and STT to isolate routing, speaker separation, and queue behavior. They do not prove live Thai/English recognition accuracy.
- The personal acoustic detector is experimental; other voices need their own samples and validation.
- Maximum eight tracked speakers; one active request and five waiting requests per listener. Queue overflow is skipped without STT charge and reported in the console.
- No guaranteed priority for stop commands: they wait their turn.
- Separate Discord streams cannot remove another person's voice physically leaking into your microphone. Use headphones.
- Source identity is isolated by Discord ID, but displayed names can be identical. Memory/persona attribution still uses display names.
- One music deck is shared in this implementation. This is a single-server group test, not multi-server certification.
