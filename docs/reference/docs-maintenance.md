# Docs maintenance

How to run this site, extend it, and keep it from rotting.

## Run it

```bash
py -m pip install -r requirements-docs.txt
```

```bash
py -m mkdocs serve
```

<http://127.0.0.1:8000> with live reload. Note `py -m mkdocs`, not `mkdocs` — the scripts
directory isn't on PATH on this machine.

```bash
py -m mkdocs build
```

Static HTML into `site/`. Open `site/index.html` directly, or serve it anywhere. No
server needed.

## Add a page

1. Create the markdown file under `docs/`.
2. Add it to `nav:` in `mkdocs.yml` — **nav is the curriculum**, so put it where a reader
   would meet it, not alphabetically.
3. Follow the page template (below).
4. `py -m mkdocs build --strict` — fails on a broken internal link.

## The page template

Every concept page uses this shape, in this order:

| Section | Contents |
|---|---|
| **What this is** | 2–3 sentences, plain language |
| **Why it's here** | The problem it solves *in this project*; what we'd lose without it |
| **Diagram** | The mental model, **before** any code |
| **How it works here** | Minimal specifics, pointing at real files |
| **Gotchas** | Things that bit us |
| **Go deeper** | External links |

Hard limit: **7 minutes to read.** Split rather than exceed it.

## Rules for writing here

- **Never invent a rationale.** If the "why" isn't known, write *"Unknown — original author
  to confirm"* and add it to [open questions](../open-questions.md).
- **Link out, don't re-explain.** asyncio, SQLite and OAuth have better docs than we'd
  write.
- **Diagram over paragraph** for anything structural, sequential, or stateful. Mermaid only
  — it lives in version control and edits alongside code. Raster images only for real
  screenshots.
- **Under ~12 nodes per diagram.** Split into zoomed-out and zoomed-in instead of one dense
  graph. Every diagram gets a caption stating the one thing it teaches.
- **Point at files and line numbers, not pasted code.** Code in docs drifts; a path
  doesn't. Short illustrative snippets are fine.
- **Placeholders for anything secret.** `REPLACE_ME`, `sk-or-v1-REPLACE_ME`,
  `client_secret_XXXX.apps.googleusercontent.com.json`. Never a real token, hostname, or
  the real client-secret filename.

## What goes stale when

Use this table when you change code. If your change touches the left column, the right
column is now suspect.

| If you change… | Re-read / update |
|---|---|
| `prompts/tiwa.md` | [Her persona](../concepts/persona.md) |
| `pipeline.py` passes, prompts or temperatures | [The three passes](../concepts/three-passes.md), [Decision log](decisions.md) |
| `pipeline._doing()` | [Action state](../concepts/action-state.md) |
| `llm.py` `_MODES`, models, or the ceiling | [Modes](../concepts/modes.md), [Model providers](../toolchain/providers.md), [Env vars](env.md) |
| `memory.py` schema or caps | [Memory](../concepts/memory.md), [Env vars](env.md) |
| **`memory.store_extraction()`** | **[The guards](../concepts/guards.md) — and run `tests/test_memory.py`** |
| `minis.py` — adding or removing a mini | [The swarm](../concepts/the-swarm.md), the list in [the tour](../tour.md), [Workflows](../work/workflows.md) |
| `tools.py` — adding or removing an actuator | [Actuators](../concepts/tools.md), and name its caller in `tests/growthbench.py` or the bench fails |
| Any mini *description* | [The swarm](../concepts/the-swarm.md) — descriptions are behaviour |
| `bot.py` flush order | [The Discord bot](../surfaces/discord.md) |
| `music.py` | [Music and the DJ](../surfaces/music.md) |
| `voice.py` gates, wake word, patches | [Voice in](../surfaces/voice-in.md), [Discord transport](../toolchain/discord-transport.md) |
| `gcal.py` | [Calendar](../surfaces/calendar.md) |
| `dashboard.py` tabs or `SETTINGS` | [The control panel](../surfaces/panel.md), [Env vars](env.md) |
| Any `os.environ.get` | [Env vars](env.md) — the count in the heading too |
| `requirements.txt` | [Dependency table](../toolchain/dependencies.md), [Setup](../setup.md) |
| Adding or deleting a bench | [The bench suite](../work/testing.md) |
| Fixing anything in [findings](findings.md) | Delete the finding, and any page that references it |

## Known gaps in this guide

Honest list. Each is a real shortfall, not a placeholder.

- **No screenshots.** The [control panel](../surfaces/panel.md) is described in prose
  because the browser pane wasn't available to capture it. Five images (one per tab) would
  make that page much better. Put them in `docs/img/` and reference with
  `![alt](../img/name.png)`.
- **No permalinks.** Most of the project is uncommitted, so GitHub links to the last
  commit (`21e42a3`) would 404 for `dashboard.py`, `voice.py`, `music.py` and every bench.
  Once it's pushed, convert the inline file references to
  `https://github.com/krichpapath/Tiwa-Utility-Chatbot/blob/<sha>/path#L12`.
- **Line numbers will drift.** Where this guide cites one (`tiwa/llm.py:39`) treat it as a
  hint, not an address.
- **`PLAN.md` is not reproduced here.** It's the historical record of five development
  tracks and remains worth reading; the [decision log](decisions.md) distils only the
  decided parts.

## Deleting pages

A page that doesn't help a newcomer get running, understand the architecture, justify a
dependency, ship a change, or find where code goes should be deleted. That test was
applied when this guide was structured; apply it again when you add.
