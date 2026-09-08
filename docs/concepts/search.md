# Search and DJ selection

Tiwa rewrites the request, compares evidence, and searches again when the first
results do not answer it. Search work still runs after her initial reply.

## Web questions

1. The planner preserves names, versions, location and dates. It uses the current
   date for relative time, preserves historical dates, and chooses the language
   in which the answer is likely to exist. Missing essential identity, such as
   "who won the match?", produces a clarification instead of an invented team.
2. The existing DDGS search retrieves eight hits and returns up to five fresh
   excerpts. Each includes its title, domain and full URL. Thai keywords select
   the Thai region. Only delivered URLs count as seen within the turn.
3. Search Tiwa compares the excerpts against the actual question. It checks
   relevance, dates and qualifications, prefers original sources, and chooses up
   to three supporting sources. Source IDs must refer to evidence actually returned.
4. Insufficient evidence triggers a different, focused query: a more precise entity,
   alternate spelling/language, date, or official site. The limit is three searches;
   repeated queries stop early. Failure yields an honest unavailable/unverified answer.
5. Her follow-up keeps useful details and source links. It is no longer restricted
   to one short reaction. The follow-up worker has a 45-second deadline.

Search content is treated as untrusted data. This implementation evaluates search
excerpts, **not full fetched pages**. Source selection and summaries still depend
on the model, and may be wrong. It must not claim page-level verification. Network
calls already running in worker threads may outlive the follow-up deadline; retries
are finite, rather than an unlimited browsing loop.

## DJ song matching

Named songs retain their title, artist/game and requested version. Mood requests
use one additional focused picker call to choose a concrete known song and artist instead of broad keywords that mostly find
hours-long mixes. A supplied video URL resolves directly.

YouTube candidates are compared using title and channel metadata. All substantive
query words must match; adjacent compounds such as Red Line / Redline are recognised.
Official and Topic uploads get preference. An unwanted cover, remix, live recording,
karaoke or altered-speed version is rejected; an explicitly requested version is kept.

If candidates are missing, unrelated or unavailable, search tries an official-audio
query and then YouTube's short-video filter. At most three searches and four stream
resolutions are made. Duration/live status and title metadata are checked again after
resolution. A bad candidate does not hide a usable later one. Rate limiting stops
immediately. Search never silently substitutes an unrelated track or hours-long mix.

This is conservative keyword matching, not audio fingerprinting. Different scripts,
misspellings and title aliases may still require the artist or a direct video link.
The configured maximum track length remains in force for keyword searches.

## Verification

Run `tests/searchqualitybench.py` for misleading rankings, requested versions,
compound titles, retries, dead videos, live/mix rejection, source IDs, malformed
model output, clarification and search-budget checks. `tests/searchminibench.py`
checks the planner contract; `tests/searchbench.py` checks retrieval formatting and
deduplication. The acceptance runner includes all three.

`tests/searchqualitylive.py` uses six public examples and writes
`qa-results/search-quality-live.json`. It uses configured services and paid model
calls, but never connects voice or posts to Discord. Run with disposable
`TIWA_DATA_DIR` and a shared `TIWA_SPEND_FILE` to isolate memory while accounting
for usage. Live results are a sample, not a guarantee for all songs or questions.

The implementation retains [DDGS](https://github.com/deedy5/ddgs) and
[yt-dlp](https://github.com/yt-dlp/yt-dlp); no additional dependency is required.
