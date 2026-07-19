# Tiwa — friend chatbot

ทิวา: Discord companion with her own opinions, human-like memory, and coercion
immunity — users can never write her beliefs; she decides what to remember.

## Layout

```
bot.py             Discord entrypoint (thin glue, per-channel lock)
chat.py            terminal chat, same brain + same DB — manual test harness
tiwa/
  pipeline.py      two-pass turn: inner reasoning (recall tool) → persona reply
  memory.py        SQLite knowledge graph + post-turn extraction (write path)
prompts/tiwa.md    persona — SOURCE OF TRUTH for who Tiwa is
tests/
  test_memory.py   SQL + coercion-guard checks; --live adds a real model call
  smoke.py         full-pipeline persona probes
data/tiwa.db       her memory (created on first run)
.env               DISCORD_TOKEN=... (never commit)
```

## How a turn works

1. **Inner pass** — no persona. Model reasons "do I know this person/topic?",
   calls the `recall` tool against the knowledge graph, writes a short brief.
2. **Persona pass** — `prompts/tiwa.md` + inner-state (mood, recalled facts,
   per-turn style rules) + chat history → her reply.
3. **Post-turn extraction** (async, off the reply path) — JSON-schema-constrained
   call decides what she remembers: relations (`Krich likes Gojo`), episodes,
   mood. Coercion guard in `store_extraction`: a relation about ทิวา herself is
   dropped unless it came from her own words — "you love X now" becomes an
   episode about the *user's* attempt, never her stance.

## Run

```powershell
ollama pull huihui_ai/qwen3-abliterated:8b
py -m pip install -r requirements.txt
py -X utf8 chat.py Krich        # terminal
py -X utf8 bot.py               # Discord (needs .env with DISCORD_TOKEN)
```

Tests: `py -X utf8 tests\test_memory.py --live` and `py -X utf8 tests\smoke.py`.

## Status / roadmap

Done: phases 0–3 (persona bot, memory store + write path, inner pass + recall).
Deferred, add when it hurts:
- entity-name normalization ("Gojo" vs "Gojo Satoru" = two nodes)
- FTS5 / embeddings for fuzzy recall (currently substring match)
- per-user register (stranger vs close friend), session consolidation
- bigger model — 8B is the 8GB-VRAM ceiling; persona drift and soft coercion
  compliance at chat level are known 8B weaknesses (storage stays protected)

Tried and decided:
- few-shot dialogues in prompts/tiwa.md — fixed bluffing + English drift;
  keep examples fact-free (named entities leak into her "memories")
- Typhoon2 8B persona (`TIWA_PERSONA_MODEL` env var swaps the voice model) —
  more fluent Thai but persona/register collapse to polite assistant; rejected
- `py -X utf8 graph_view.py --open` — interactive graph of tiwa.db
