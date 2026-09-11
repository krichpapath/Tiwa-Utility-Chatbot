# Music and search

`minis.dj` turns a request into ordered play/queue/skip/remove/stop steps. An explicit
present-tense play request has a fallback if the mini returns an empty plan. The
search stage decides what recording matches; the persona cannot cancel that action.

`music._music_request` removes recognized conversational wake prefixes and chatter.
`_music_queries` generates spelling hypotheses. `_discover` performs up to three
search rounds and asks the tool model to select from actual candidate metadata.
The original intent and current search hypothesis both reach review. Artist-only
requests can choose a song by that artist, including cross-script channel evidence.
Invalid selections, unrelated recordings, live streams and overlong tracks are rejected.

`discord_player.Player` executes accepted steps serially. Ordinary requests append
to the queue. Streams resolve at playback time for deferred tracks. A generation
counter stops callbacks from replaced tracks advancing the queue twice. Failed
searches do not discard later steps; rate limits stop retry storms.

| Request | Behavior |
|---|---|
| Play TheFatRat songs | Default batch of five; explicit counts capped at 50 |
| Play Unity then Monody | Ordered song requests |
| Skip | Advance one song |
| Skip three | Current plus next two, without starting intermediate audio |
| Skip Monody | Skip current only if it matches; otherwise remove the matching queued song |
| Remove Monody and Unity | Remove matching waiting songs; current continues |
| Remove next three | Remove three waiting songs |

Missing/ambiguous named queue edits leave the queue unchanged. An explicit playlist
URL preserves playlist order within the bounded batch. The deck is still shared
within the process. Tests: `musicqueuebench`, `musicintentbench`, `musicartistbench`,
`searchqualitybench`. Live search/decoding is distinct from Discord playback.
