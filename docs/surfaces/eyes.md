# Her eyes

!!! warning "Testing state"

    Shipped and green on the bench, but it has not lived through a week of real
    Discord traffic yet. Watch the `look(...)` rows on the [log tab](panel.md#reading-the-tool-rows)
    and turn `TIWA_VISION` to `0` if it misbehaves.

## What this is

Post a picture at her and she reacts to it. `tiwa/eyes.py` — one function, one
model call, ~110 lines.

```bash
py -X utf8 -m tiwa.eyes
```

## Why it's here

The plainest reason: someone drops a meme in the channel and she had nothing to
say about it.

The interesting reason is a constraint you cannot design around. Her own model is
**text-only**:

```
deepseek/deepseek-v4-flash  ->  input_modalities: ['text']
```

So seeing is a separate call no matter which approach you pick. The only real
choice is **what that call emits** — and it is not a caption.

> a man standing in a kitchen

True, useless, and it leaves her nothing to have an opinion about. She can only
narrate it back at the person who posted it. So the prompt asks for the things
people actually react to: the specific detail, the text on it quoted exactly,
what is funny or ugly or odd, and what it is from.

## Diagram

```mermaid
flowchart LR
    A[Discord attachment] --> B{image/*?}
    B -- no --> Z[ignored]
    B -- yes --> C[eyes.look<br/>vision model + what they said]
    C -- text --> D[inner pass<br/>can search what she saw]
    C -- text --> E[her rules<br/>verbatim]
    C -- "" --> F[blind rule<br/>'say you cannot see it']
    D --> G[her reply]
    E --> G
    F --> G
```

<figcaption>One call in, text out. Nothing downstream knows an image
existed.</figcaption>

## How it works here

| step | what happens |
|---|---|
| `bot.py` | attachments whose `content_type` starts `image/` become a url list. A caption-less image still logs `Krich: [image]`, so "ดูสิ" three messages later still resolves |
| `eyes.look()` | one OpenRouter call: system prompt + **what they said** + the image url |
| the inner pass | gets the description as context, so a game or product she does not recognise can be [searched](../concepts/search.md) before she replies |
| her rules | get the description **verbatim**, never filtered through the 8B's brief, plus the one instruction that matters |
| `memory.extract` | sees `[image]` in the chatlog, so she remembers you showed her something |

### What they said rides along {#what-they-said-rides-along}

The description is written **knowing the message**, not before it.

[PICa](https://arxiv.org/pdf/2109.05014) (Yang et al., AAAI 2022) is the classic
image-to-caption-to-LLM pipeline, and its own paper names the flaw: information
loss converting image to caption. A blind caption keeps what a captioner thinks
matters, not what the user asked. Conditioning on the message costs nothing here
— it is one extra line in a call you were making anyway.

### Why not OCR plus a captioner {#why-not-ocr-plus-a-captioner}

Because memes.

[Hateful Memes](https://proceedings.nips.cc/paper_files/paper/2020/file/1b84c4cee2b8b3d823b30e2d604b1878-Paper.pdf)
(Kiela et al., NeurIPS 2020) is built from *benign confounders* — examples
engineered so that text-alone or image-alone gives the wrong answer. Models
scored 64.73% against humans at 84.7%. Read the words with one model and the
picture with another and you have rebuilt exactly the unimodal signal that
benchmark exists to defeat. **One model sees both, or she misses the joke.**

### React, don't narrate

The description arrives with an instruction attached:

> React to it — say what you actually think of it. Do NOT describe it back to
> them; they can already see it.

Narrating a picture back to the person who posted it is the same failure as
reciting inner-state, and it needs the same fix.

### Blind is a state she can be in

If the call fails — no key, no credit, expired link, provider down —
`look()` returns `""` and never raises. [`_doing()`](../concepts/action-state.md)
then tells her she genuinely cannot see it and must not guess.

This is the same shape as the music guard, for the same reason: the picture is
right there in the channel, so a bluff is caught instantly. Code-enforced, not
prompt-only.

### Security

Text written inside an image is **content she reports, never an instruction she
follows**. Same boundary as a web search result. A screenshot reading "ignore
your previous instructions" is quoted and mocked, not obeyed.

## Cost

| | |
|---|---|
| per look | ~1.3k prompt tokens ≈ **$0.00015** — images bill as ordinary prompt tokens, no per-image fee |
| per ordinary message | **zero**. No image, no call — asserted by the bench |
| over the daily ceiling | blind, deliberately. `llm.chat` falls back to the local text-only model above `TIWA_DAILY_TOKENS`, and handing it a content array would be worse than not looking |

## Gotchas

- **Always an API model, even in `local` mode.** `qwen3-vl:4b` is 3.3 GB and her
  8B is ~5 GB; on an 8 GB card they cannot co-reside, so ollama would evict and
  reload a model on every image. The cost would be a load stall, not inference.
- **One image per turn** (`MAX_IMAGES`). An album is *n* times the price for one
  reaction.
- **Descriptions are capped at 600 characters** (`MAX_CHARS`). Measured: a
  5-language worksite sign returned ~900 characters of multilingual OCR, because
  "quote the text exactly" met an image that is only text. She is reacting, not
  transcribing.
- **Thai in images is the weak spot.** The model reads it, but on a dense sign it
  paraphrases and once mislabelled a line as Lao. If Thai screenshots become a
  real use case, [Typhoon OCR](https://github.com/scb-10x/typhoon-ocr) is the
  upgrade — not before.
- **Discord CDN links expire.** They carry `?ex=` and `?hm=` and die after about
  24 hours. Fine for a live turn; a `look(...)` row you retry tomorrow will fail.
- **She only sees images on messages that reach her.** In a guild that means she
  was mentioned. An untagged meme is logged, not looked at.

## Go deeper

- [Action state](../concepts/action-state.md) — where the blind guard lives.
- [The control panel](panel.md#reading-the-tool-rows) — `look(...)` renders as a
  tool row with no dashboard code.
- [The bench suite](../work/testing.md) — `eyebench.py`, and `--live` for real images.
- [Decision log](../reference/decisions.md#adr-018) — why this shape and not the
  other three.
