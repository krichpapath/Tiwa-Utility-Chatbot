# Tiwa memory redesign: research and proposed direction

Research date: 10 September 2026. Status: proposal with an initial scoped-recall implementation.

Implementation update: the first scoped-recall version is now implemented in
`tiwa/recall.py`; see [current behavior and limits](memory.md#september-2026-scoped-recall-is-now-the-conversation-path).
The broader identity/access-control and semantic retrieval design below remains a proposal.

**Recommendation: evidence-backed memories, explicit ownership, and retrieval scoped to the current conversation.** Keep a small interaction profile available; load preferences, opinions, and shared experiences only when they help with the current turn. SQLite can remain the storage layer.

This is an initial research synthesis and inspection of the current source, not a benchmark of competing implementations. Examples below are illustrative, not claims about actual users. No live memories were audited or changed.

## What the current code explains

| Finding | Source | Consequence |
|---|---|---|
| `turn_context(db, user)` has no query argument; injects the first 12 fact lines plus three recent episodes | `tiwa/memory.py`, `turn_context` | A Gojo question still receives the user's unrelated music facts. The fact query has no explicit ordering. |
| `lookup()` matches substrings and returns relations in both directions | `tiwa/memory.py`, `lookup` | A topic lookup mixes Tiwa's opinion with other people's opinions. |
| `mentioned()` caps entities but not the facts returned for each | `tiwa/memory.py`, `mentioned` | Three popular entities can still produce a large context block. |
| Fuzzy spelling similarity automatically merges names | `tiwa/memory.py`, `canonical` | Similar names can identify different people; matching text is insufficient evidence of identity. |
| Discord text passes `display_name` as the memory author | `bot.py`, `on_message` | Renames and shared display names can split or mix personal histories. |
| New opposing preferences delete older relations | `tiwa/memory.py`, `_supersede` | Current state is simpler, but the original belief and change history disappear. |
| Repeated music requests can write ordinary `likes` relations | `tiwa/pipeline.py`, `_tastes` | An inference receives the same representation as an explicit preference. |
| Automatic episode generation emphasizes new subjects and preference flips; extractor episodes can also be accepted | `tiwa/memory.py`, `store_extraction` | The automatic criteria miss many meaningful shared experiences; the alternative episode path needs its own evidence validation. |
| Reflections are stored as episodes under Tiwa's name | `tiwa/memory.py`, `reflect`; `tiwa/pipeline.py`, `_settle` | An interpretation and an observed event lack an explicit type distinction. |

The existing guards against invented events and user-imposed Tiwa opinions are valuable. Preserve those guarantees, while replacing their storage and retrieval assumptions.

## Research that informs the design

| Work | Relevant finding or mechanism | Application to Tiwa |
|---|---|---|
| [LongMemEval, ICLR 2025](https://arxiv.org/abs/2410.10813) | Separates indexing, retrieval, and reading; evaluates extraction, cross-session reasoning, updates, temporal reasoning, and abstention. | Test whether a memory was stored correctly, selected correctly, and used correctly as separate stages. |
| [Mem0, 2025](https://arxiv.org/abs/2504.19413) | Extracts, consolidates, and retrieves salient memories, with an optional graph variant. | Separate conversational evidence from compact reusable memories; a graph database is not a prerequisite. |
| [Zep, 2025](https://arxiv.org/abs/2501.13956) | Maintains temporal relationships in a knowledge graph. | Preserve belief history and distinguish when something happened from when Tiwa learned it. |
| [Generative Agents, 2023](https://arxiv.org/abs/2304.03442) | Combines observed experiences, higher-level reflections, and dynamic retrieval for believable behavior. | Shared experiences and interpretations matter for personality, but retain their distinction. |
| [A-MEM, NeurIPS 2025](https://arxiv.org/abs/2502.12110) | Builds connections between memories and updates their contextual representations. | Link a running joke or impression to supporting experiences; postpone autonomous rewriting of the whole memory network. |
| [Lost in the Middle, TACL 2024](https://arxiv.org/abs/2307.03172) | Demonstrates positional sensitivity when models use information in long contexts. | Measure the value of supplied context rather than assuming a larger prompt solves recall. This is evidence from the studied models, not a measurement of Tiwa's present model. |

These papers support useful components, not a universal winner. Their benchmark results do not establish which system best handles Tiwa's Thai/English conversations, group identity, teasing, or opinion consistency. The architecture below is my engineering recommendation based on those requirements.

## What Tiwa should remember

Use typed records in a common store, rather than a separate subsystem for every category.

| Type | Example | When it belongs in context |
|---|---|---|
| Interaction preference | Prefers brief replies; enjoys playful disagreement | A compact active-person profile |
| Preference | Likes Mili, especially its storytelling; dislikes loud music while working | Music discussion or choosing something for that person |
| Personal fact or relationship | Steven is Krich's cousin | Relevant discussion involving those people |
| Episode | Krich lost a match immediately after boasting about an easy win | A related callback or conversation about that match |
| Relationship impression | Tiwa finds Krich competitive but a good sport | Relevant interpersonal response; explicitly an interpretation |
| Tiwa stance | Finds Gojo charismatic but dislikes his arrogance | A question or discussion about Gojo |
| Open commitment | Promised to hear a demo when it is ready | Relevant follow-up until resolved |

Avoid turning personality into a permanent global label such as "Krich is insecure." Prefer contextual observations: "He asked for direct feedback on his music twice." Broad impressions should remain revisable, with supporting evidence.

Teasing needs both a remembered moment and a sense of what this person enjoys. Store the event, relevant participants, accepted joke topics, explicit boundaries, and last use. Retrieve a callback when the subject and tone fit. Reduce repetition after use. A laugh once is weak evidence of a general preference; an explicit "stop joking about that" overrides prior positive reactions. Details disclosed privately retain their audience restrictions.

Tiwa's stance is separate from a user's preference and from objective information about the subject. It should contain her evaluation, reason, and supporting interaction. She can form a new opinion without pretending to remember an old one. Jokes, quotations, roleplay, and statements induced by "say you love X" should not automatically become enduring stances. Repeating her own stored stance does not add independent evidence.

## A memory record

Store a short natural-language statement plus fields that make it filterable:

- Identity: record ID, type, subject ID, opinion holder when applicable, topic/entity IDs.
- Meaning: statement, aspect, contextual qualifier, reason where available.
- Evidence: source message IDs, speaker ID, evidence kind (`explicit`, `observed`, `inferred`, `tiwa_stance`), and uncertainty status.
- Time: observed time, effective time when known, validity end, status, and superseded record ID.
- Scope: origin conversation and allowed audience; private memories must not become group context through a related-person lookup.
- Maintenance: last used time and expiry/review date where appropriate.

For example, "likes Gojo's design" and "dislikes Gojo's arrogance" are compatible. A single positive/negative axis should not erase either. A correction supersedes the same aspect in the same context; contradictory hearsay remains attributed rather than replacing a person's self-report.

Use Discord account IDs for speakers. Display names and confirmed aliases resolve to those IDs. For fictional characters, songs, and third parties, maintain entity IDs and aliases. Fuzzy matching may suggest candidates but should not silently merge people. Ambiguous references stay unresolved until context or clarification identifies them.

## Writing: evidence first, selective promotion

1. Capture a bounded source event with real speaker, time, conversation, and message identity.
2. Extract candidate durable facts, explicit preferences, meaningful episodes, commitments, or Tiwa stances. Ordinary commands and greetings usually produce no durable memory.
3. Validate each candidate against its own evidence span: subject, negation, attribution, quotation, and context. A preference word elsewhere in the message does not validate every candidate.
4. Compare with existing records for that subject, topic, aspect, and scope. Add, reinforce, supersede, or keep unresolved. Preserve source references.
5. Consolidate only when new evidence warrants it. Derived impressions retain links to their supporting events and remain marked as inferred.

"Play Mili" is an action. Repeated Mili requests support "often requests Mili," a behavioral observation. "Mili is my favorite band" supports an explicit preference. The first two do not justify inventing the reason behind the preference.

Do not make surprise the only admission rule: a quietly shared personal detail can be useful, while a surprising throwaway joke can be worthless. Evaluate likely future conversational usefulness, specificity, and evidence. Explicit remember/forget/correct requests need dedicated handling.

Use time-based expiry for temporary state and resolved commitments; reduce retrieval priority for stale inferences. Stable explicit preferences should not vanish merely because they were not recently mentioned. Forget operations must remove or invalidate derived summaries and search entries too, and prevent retained source logs from immediately regenerating the memory.

## Reading: select before adding to the prompt

```mermaid
flowchart LR
    A[Message and recent conversation] --> B[Resolve people, topic and intent]
    B --> C[Filter ownership, audience and time]
    C --> D[Retrieve candidate memories]
    D --> E[Check relevance and evidence]
    E --> F[Pack a bounded memory block]
    F --> G[Tiwa responds]
    G --> H[Extract and consolidate with evidence]
```

Start with automatic scoped retrieval every turn. The current code documents why relying entirely on Tiwa choosing a recall tool failed. Keep an explicit recall tool for follow-up searches and multi-step questions.

The query should express **who, what topic, whose perspective, and why it is needed**. Use recent context to resolve "him," "that song," or "what about now?" Explicit entity questions can take a direct route; ambiguous semantic questions can use a small structured query-planning call. Measure its latency before putting it on every turn.

Apply identity and audience restrictions before search. Then retrieve by exact entity/attribute matches, lexical search, and semantic similarity where useful. An explicit ownership restriction such as "your opinion" must survive reranking. For open questions such as "what would I enjoy?", use the intent to broaden across relevant preferences rather than insisting on one named entity.

For a first prototype, compare structured SQLite retrieval against the same retrieval augmented with multilingual embeddings. SQLite full-text search alone needs careful evaluation on Thai segmentation. Embeddings help paraphrases and cross-language matches but do not establish identity, truth, negation, or whose opinion is represented.

Use relevance as an admission requirement. Among eligible memories, rank direct support and useful context before recency or general importance. Expand linked evidence only when needed. Return zero memories when none are useful; do not fill a quota with unrelated rows.

Starting budget hypotheses: 80–150 tokens for the active person's interaction preferences and boundaries; up to roughly 400–800 tokens of topical memory, often much less. Count actual tokens, including evidence annotations. These are tuning values, not research-established optimums. A larger research-style recall request may explicitly receive a larger budget.

## The Gojo example

Question: "What do you think of Gojo Satoru?"

Resolved request: holder = Tiwa; topic = canonical Gojo entity; purpose = current stance and reasons. A hypothetical memory block would be:

```text
Tiwa's established view of Gojo:
- Likes his confidence and character design.
- Finds his arrogance irritating.
Evidence: prior sincere stance, source m142. Current; no later revision found.
```

The user's favorite song is excluded. Other people's Gojo opinions are excluded unless the question asks for a comparison or the current exchange makes one relevant. If no stance exists, the block says there is no established stance; Tiwa may answer freshly without inventing prior history.

| Question | Memory selected |
|---|---|
| "What do you think of Gojo?" | Tiwa's Gojo stance |
| "Do I like Gojo?" | Speaker's Gojo preference and evidence |
| "Why do we disagree about Gojo?" | Both perspectives and relevant shared discussion |
| "Pick a song I might like" | Speaker's relevant music tastes and contextual dislikes |
| "Remember when I said I'd carry the team?" | Matching shared episode, with appropriate callback context |

## Implementation direction and evaluation

Keep SQLite initially: entities/aliases, source events, typed memories, and a memory-to-evidence link table are sufficient for the first prototype. Relationships can remain queryable links. A dedicated graph service adds operational complexity before we have evidence it improves Tiwa.

Build a small labeled replay set before changing the live behavior. Include Thai, English, transliteration, topic switches, ambiguous names, two users with opposite tastes, explicit corrections, old preferences, sarcasm, induced Tiwa stances, private/group boundaries, and unanswered questions. For each turn label required memories, forbidden memories, expected write operations, and acceptable response behavior.

Measure retrieval precision and recall separately, wrong-person/holder errors, stale-belief errors, unsupported writes, correct abstention, memory tokens, and end-to-end latency. Evaluate whether adaptation feels natural and whether callbacks become repetitive. Public QA benchmarks do not replace those conversational judgments.

Compare the current system, structured scoped retrieval, and structured retrieval plus multilingual semantic search under the same reply model and context budget. Hard release cases should include zero unrelated music memories in the Gojo example, zero cross-person/private leakage in the labeled cases, and reliable preference correction. Broader quality thresholds should follow a measured baseline.

Migrate through a copy: preserve a snapshot, import old rows as legacy/unverified, and promote records whose evidence can be recovered. Do not treat old notes as proof or silently merge ambiguous identities. Run the new retrieval in shadow mode, inspect selected/excluded memories, then switch behind a reversible flag. Existing live records should not be bulk-deleted as a first step.

The first implementation should replace the broad context assembly contract and introduce ownership, identity, and provenance. Richer reflection and associative recall come after we can demonstrate that Tiwa remembers the right person's right detail at the right moment.
