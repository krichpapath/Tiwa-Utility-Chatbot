# Search improvements — 2026-09-08

Implemented on `swarm`, preserving the earlier acceptance fixes and existing user edits.

## What changed

- DJ intent and mood selection have separate responsibilities. Named songs retain
  title/artist/version. Mood requests use one additional focused call to choose an
  actual title and artist, preventing generic study-music queries from reaching the deck.
- YouTube search ranks five candidates by title and channel match, prefers official
  or Topic metadata, and rejects unintended recording versions. Compound names such
  as Red Line / Redline match. Resolved duration and livestream status are rechecked.
- Weak or unavailable song results trigger an official-audio query and short-video
  fallback. Three searches and four stream resolutions are the caps. Rate limits stop
  immediately; no unrelated song or hours-long mix is silently substituted.
- Web search plans keywords or requests essential clarification. It preserves dates,
  entities and version qualifiers, targets appropriate languages and official sources,
  and reviews multiple excerpts before answering. Missing evidence can trigger two
  refined searches; duplicate queries stop early.
- Excerpts retain full source URLs and up to 600 characters each. Only delivered
  results are marked seen. A reviewer selects up to three valid source IDs. The final
  reply preserves useful detail and source links, including when the persona omits them.

No new dependencies. No Discord restart or persistent deployment performed.

## Verification

- **24/24 offline suites pass**, including new search-quality regressions.
- **Live DJ intent: 14/14; recommendation routing: 8/8.** All eight non-music vetoes held.
- **Named songs: 3/3 matched**: Mili Hero; Warframe Red Line (matched Gauss: Redline);
  Bodyslam แสงสุดท้าย. Search/resolution took approximately 2.6–3.1 seconds in this sample.
- **Mood requests: 2/2 resolved**: English study request selected Nujabes Aruarian Dance;
  Thai recommendation selected Three Man Down ฝนตกไหม. No audio playback was started.
- English technical answer reached Python's official documentation and explained the
  distinction with a concrete example. Thai game answer included the developer and
  game type, with URLs retained in the final persona reply. An unnamed match request
  clarified its identity without a search.
- Offline failure checks cover unrelated artists, unwanted covers/remixes, requested
  versions, dead videos, rate limiting, stale duration, livestreams, long mixes,
  weak evidence requiring another query, invalid source IDs/JSON, provider failure,
  repeated queries, finite search budget and source-link preservation.
- Syntax checks, diff whitespace checks and strict documentation build passed.

Evidence: [offline results](acceptance/results.json),
[live public searches and replies](search-quality-live.json),
[live mood-to-song checks](search-dj-mood-live.json),
[DJ intent log](acceptance/live-djminibench.log),
[recommendation log](acceptance/live-pickbench.log).

## Remaining limits

Web evidence is search excerpts, not full pages. Model judgments and persona wording
can still be wrong, embellished or incomplete; no claim of universal answer accuracy.
Extra evidence checks and the mood picker add latency and paid calls. Follow-up work
has a 45-second deadline, but already-running synchronous network calls may outlive it.

Song matching is conservative text matching, not audio fingerprinting. Misspellings,
alternate scripts, aliases and misleading upload metadata can still require an artist
or direct video URL. Keyword searches reject tracks beyond the configured length limit.

The earlier deployment limits still apply: live voice, real Calendar mutations,
human Discord ingress and restart/soak acceptance have not been newly certified.
