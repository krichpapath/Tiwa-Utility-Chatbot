# Before you begin

You don't need to know this project. You do need a few things that this guide will
**not** teach you, because other people teach them better.

## Assumed knowledge

| You should be comfortable with | Why it comes up | If you're rusty |
|---|---|---|
| **Python 3.12+**, incl. f-strings and `pathlib` | The entire codebase | [Python tutorial](https://docs.python.org/3/tutorial/) |
| **`async`/`await`** and the event loop | Every turn is async; blocking the loop makes her freeze mid-conversation | [asyncio docs](https://docs.python.org/3/library/asyncio.html) · [Real Python guide](https://realpython.com/async-io-python/) |
| **`asyncio.to_thread`** | How blocking work (network, model calls) stays off the loop | [to_thread](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread) |
| **SQL basics** — `SELECT`, `JOIN`, `INSERT OR REPLACE` | Her memory is hand-written SQL. No ORM | [SQLite tutorial](https://www.sqlitetutorial.net/) |
| **How LLM tool calling works** | The inner pass is a tool loop | [OpenAI function calling](https://platform.openai.com/docs/guides/function-calling) · [Ollama tool support](https://ollama.com/blog/tool-support) |
| **Discord bot basics** — tokens, intents, gateways | `bot.py` assumes it | [discord.py quickstart](https://discordpy.readthedocs.io/en/stable/quickstart.html) |

You do **not** need: machine learning background, model training experience, prompt
engineering theory, or Thai. Thai strings appear throughout — every one is explained
where it matters, and the [glossary](reference/glossary.md) covers the rest.

## Accounts and services

| Need | For what | Required? |
|---|---|---|
| [Discord bot application](https://discord.com/developers/applications) | A token, and the **Message Content** intent switched on | Yes, for the Discord surface |
| [Ollama](https://ollama.com/download) installed locally | `local` and `mixed` modes | Only if you run models locally |
| [OpenRouter](https://openrouter.ai/) account + key | `mixed` and `api` modes | Only if you use the API |
| [Google Cloud OAuth client](https://developers.google.com/workspace/guides/create-credentials#oauth-client-id) | Calendar reads and writes | No — everything else works without it |

You can do the whole [10-minute tour](tour.md) with **just Ollama and no accounts at
all**, using `chat.py` in the terminal. That's the recommended first run.

## Hardware reality

This project was built against one machine, and the constraints show up in the design:

- **8 GB VRAM.** One 8B model fits. Two don't. That's why speech-to-text runs on
  CPU and why [modes](concepts/modes.md) exist at all.
- **Windows with [Smart App Control](https://support.microsoft.com/en-us/topic/what-is-smart-app-control-285ea03d-fa88-4d56-882e-6698afdb7f73) enforced.** It blocks unsigned DLLs, and it
  has already vetoed two libraries here. See [audio and speech](toolchain/audio-speech.md).
- **10 CPU cores.** Thread counts in the speech config are tuned to it.

None of this is required to run the project. It explains why some choices look odd.

## Next

[Environment setup](setup.md) — about 10 minutes, mostly waiting on a model download.
