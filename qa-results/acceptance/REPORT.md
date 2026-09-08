# Tiwa acceptance report — 2026-09-07

## Verdict

**Deployment candidate prepared. Conditional GO for a supervised single-server text
launch; full-feature production acceptance is not granted.** The runnable environment,
repeatable offline suite, locked dependencies and launcher are delivered. Live voice
was skipped at the user's request. Real Calendar writes and long-running operation
remain unverified. Human ingress status is recorded separately in `discord-human.json`.
The user reported sending the QA greeting, but the listener timed out. A follow-up
REST read found 33 messages in the configured home channel, all bot-authored;
human ingress therefore remains unverified. No unrelated human messages were printed.

This report supersedes the earlier `2026-09-07/acceptance-report.md`, which stopped at
missing dependencies. Work was performed on `swarm` with pre-existing uncommitted
dashboard, graph and documentation edits preserved. No production memory reset,
security-policy change, calendar write or persistent deployment was performed.

## Search update — 2026-09-08

Search improvements are included in the refreshed deployment candidate. The latest
24-script offline run passes. Final live DJ intent scored 14/14 and recommendation
routing 8/8. Three named-song searches and two mood-to-song requests resolved to
matching tracks; no voice playback was attempted in this update.

Web search now plans or clarifies, compares up to five excerpts per query, and may
refine twice when evidence is insufficient. Answers retain selected source URLs and
use enough detail to answer the question. English technical lookup reached official
Python documentation; Thai game lookup retained useful details; an unidentified
match request asked for clarification without issuing a speculative search.

See [the search change report](../SEARCH-IMPROVEMENTS.md) and the public fixture
outputs `../search-quality-live.json` and `../search-dj-mood-live.json`. These update
the older search observations below. All other deployment limitations remain.

## Evidence and expected interaction

| Feature | Expected interaction / failure | Evidence and result |
|---|---|---|
| Runtime/setup | Install, import and start from project environment | `.venv` created; declared dependencies installed; `pip check` clean; frozen in `requirements-lock.txt` |
| Discord authentication | Valid bot/channel accessible; gateway connects | `services.json`; authenticated gateway connected; synthetic messages delivered in user-approved home channel |
| Discord routing | Mention in server; ordinary DMs; ignore bots; preserve channel context | `discordbench.log`: pass; real handler-driven delivery recorded in `discord-live.json` |
| Long and empty replies | Split long messages; empty reply still flushes queued actions | `discordbench.log`: pass |
| Thai/English persona | Match language, refuse imposed tastes, continue conversation | `live-smoke.log`, `live-moodbench.log`: exercised and manually read; subjective quality is not a binary guarantee |
| Ordinary chat | No unnecessary mini; answer available memory context | `live-chatbench.log`: final run passed, no spurious mini; synthetic known-person memory correctly recalled |
| Memory facts/recall | Learn stated relation; recall it next turn; avoid unsupported taste inference | `test_memory.log`, `live-test_memory.log`, `live-extractbench.log`: pass; extraction measured 8/8 clean |
| Memory guards | Coercion, invented history, malformed fields, reversed roles | `test_memory.log`, `deploymentbench.log`; `live-factbench.log`: 0 invented facts, 0 required facts missed in final measure |
| Worth/episodes | Requests alone do not imply taste; meaningful episodes retained and capped | `live-worthbench.log`, `live-episodebench.log`: pass; six music asks stored no taste; episode noise 0, missed meaningful events 0 |
| Taste/curiosity/reflection | Evidence thresholds, cooldown, watermarks, no self-coercion | `tastebench.log`, `live-tastebench.log`, `test_memory.log`: pass; live pattern measure 5/5 |
| Swarm | Concurrent work, per-turn actions, bounded waits, strict mini results, stable main prompt | `turnbench`, `minibench`, `forkbench`, `latebench`, `growthbench`, `recordbench`: pass |
| Search | Rewrite queries; search in relevant language; deliver late reply | `searchbench.log`, `live-searchbench.log`, `live-livechat.log`; six-turn synthetic conversation produced a late message |
| Images | Image/caption routed; no call for ordinary text; failures admitted | `eyebench.log`, `live-eyebench.log`: pass; 4/4 real image fixtures described |
| DJ intent | Play/queue/skip/stop; veto non-music asks | `djbench.log`, `djminibench.log`, `live-djminibench.log`; all eight negative asks vetoed; bare skip strengthened to deterministic handling |
| Music recommendation | Pick a song without asking for another permission | `live-pickbench.log`: improved 7/8 to 8/8, repeated 8/8 |
| Music retrieval/decoder | Resolve actual video and decode non-silent Discord PCM | `music-decode.log`: 500/500 frames, 10 seconds, peak amplitude 26105; no voice connection or audible playback |
| Calendar read | Read existing calendar, report auth/network failure | Real read succeeded in `services.json`; offline mini failure and clarification cases pass |
| Calendar routing | Concrete tomorrow/time request queues proposal; ambiguity can clarify | Initial 0/7 caused by router asking questions instead of delegation; after fix live 7/7 then 6/7 handled; explicit English tomorrow/1pm cases asserted |
| Calendar approval | Owner-only, exact preview, one-shot, right channel, expiration | `discordbench.log`, `deploymentbench.log`: pass with mocked Google boundary; **no real Google mutation tested** |
| Calendar errors | Bad JSON, wrong action, missing title, invalid interval, multiple cancel matches | `deploymentbench.log`: pass; rejected before mutation |
| Voice code | Wake variants, noise filtering, transport patches, errors, source-channel isolation | `wakebench`, `noisebench`, `dumpbench`, `routerbench`, `outagebench`, `deploymentbench`: pass; **live voice skipped** |
| Panel | Six tabs render; settings preserve secrets; tables, exports and memory operations | `panelbench.log`: pass; browser inspected all six tabs; provider failure visibly reported while chat remained usable |
| Terminal | Same pipeline, calendar approval, late results, outage recovery | Shared pipeline/guard checks and compile check; **full terminal interactive conversation not certified** |
| Graph | Generate HTML; stored values cannot close script or inject tooltip markup | Graph generation smoke and `deploymentbench.log`: pass; interactive graph layout not certified |
| Provider/cost | API responds; errors visible; usage survives concurrency; threshold changes routing | `services.json`, `outagebench.log`, `deploymentbench.log`: pass for exercised paths; actual local/mixed inference not run |
| Heartbeat/restart | Outage does not kill loop; startup runner uses known interpreter | Three injected heartbeat outages passed; runner revised; real quiet-hour timing, crash/reboot recovery and 24-hour soak not run |
| Documentation | Build without broken internal references | `mkdocs build --strict` passed |

## Fixes delivered

1. Calendar reactions previously accepted any user's approval. They now require the
   configured/application owner, correct channel and unexpired one-shot proposal.
2. Calendar parsing occurred after approval and provider exceptions escaped handling.
   Exact validated details are now shown before approval; application uses that dict.
   Cancellation no longer deletes the first fuzzy title match on an arbitrary date.
3. Router clarification requests silently swallowed calendar jobs. Calendar intent now
   delegates to Calendar Tiwa, including answers to prior calendar clarification.
4. DM music/leave requests could dereference a nonexistent guild or voice state.
   They now explain the required server context. Join/leave permission failures return errors.
5. Voice replies could discard actions on empty text, omit calendar confirmations,
   or stop action flushing when TTS failed. These paths now preserve pending actions.
6. Voice callbacks now carry their source channel instead of broadcasting transcripts
   across connected servers. An unscoped multi-server callback fails closed.
7. Equal timestamps reversed reflection order and could hide new episodes behind the
   watermark. Ordering uses an ID tie-break and the watermark uses insertion ID.
8. Failed/malformed extraction now logs the failure without killing conversation.
9. Vision observations were absent from dispatch; they now reach routing as untrusted context.
10. Early DJ work and router dispatch could run DJ twice; duplicate dispatch removed.
    Explicit `skip` no longer depends on the model guessing. Recommendation-plus-play
    requests clarified in DJ instructions. Pending playback is no longer described as completed.
11. Usage updates could race across threads/processes and lose counts or fail replacement.
    Writes now use a cross-process lock and atomic replacement; corrupt ledgers fail closed.
12. Dashboard and terminal could lose late search results or fail abruptly during outages.
    Follow-ups are consumed, and provider failures produce visible explanations.
13. Graph data could escape its script block or inject HTML in tooltips. Both are escaped.
14. Added isolated acceptance runners, service/gateway probes, owner ingress check,
    setup script, sample configuration, dependency lock and venv-aware restart launcher.

## Boundaries of the verdict

- The offline suite now contains 24 scripts. `results.json` and per-script logs record the
  latest run. Model measures are nondeterministic; exit zero alone is not proof of quality.
  Read the printed scores, missed cases and replies in `live-*.log`.
- The original Calendar benchmark uses dated/contradictory requests. Its revised gate
  accepts explicit clarification as correct for ambiguity and separately requires clear
  tomorrow/1pm requests to queue. Neither score means an event was written to Google.
- The final DJ change has a deterministic regression for bare skip. Earlier live repeat
  scored 13/14 because skip was ignored; final rerun scored 14/14, and all eight
  non-music vetoes held.
- Persona still invents conversational anecdotes in some smoke replies. Storage guards
  prevented tested invented facts from becoming durable memory; they do not prove every
  spoken sentence true. One late sports-search reply was vague rather than useful.
- First deployment is single-server: the music deck remains global. Display-name-based
  identity and concurrent multi-server memory/action behavior need separate design work.
- Local/mixed model inference, live Discord voice/STT/TTS, real calendar add/cancel,
  owner reaction arriving from the Discord UI, restart-at-login and long soak remain open.
- Historical `dispatchbench`, `latbench`, and pairwise `personabench` were not rerun.
  They depend on private historical data or comparison artifacts. Automatic approval
  review rejected exporting copied private memories; live tests used synthetic data.
  A later quota rejection temporarily blocked a calendar rerun; retry after user continued succeeded.
- Windows initially blocked PyAV with Application Control. No protection was disabled.
  Later fresh-process import and real decoding passed; reason for the transient change
  was not established. Recheck the decoder on the actual deployment machine.

## Maintainer decision

Use [DEPLOYMENT.md](../../DEPLOYMENT.md) for the supervised launch and manual completion
checks. Keep voice disabled until its real-call acceptance pass. Complete one owner-approved
test event and cancellation before relying on calendar writes. Run a supervised session,
then test restart/reconnect and a long soak before calling this full production acceptance.
