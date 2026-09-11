# Memory

`memory.py` owns the SQLite schema and persistence. `recall.py` selects relevant
facts and episodes for the current reply. `record.py` and extraction logic decide
what is worth storing; `llm.py` records model calls and usage separately.

The database contains entities/relations, episodes, activity/model logs, and memory
history. Names, stated preferences and relationships need evidence. A one-off song
request is not a durable preference. Persona improvisation is not user evidence.
Name links are stored as claims, not verified Discord identities. Old plans do not
prove a Google Calendar write succeeded.

The Discord application keeps up to 40 in-memory messages per text channel, including
accepted voice requests. `pipeline.respond` passes recent context to routing/recall
and combines selected evidence with the current request. Background voice conversation
without activation is not continuously transcribed into history.

The dashboard offers search, person/category/evidence filters, detail views, recall
preview, history, exports and explicit deletion controls. Use test databases for
experiments; the real database, prompts and recordings belong in private `data/`.

When changing memory behavior, begin with `memoryrecallbench`, `namebench`,
`relationshipbench`, `tastebench`, and `test_memory`. Check evidence precision and
identity separation, not just whether a row was inserted.
