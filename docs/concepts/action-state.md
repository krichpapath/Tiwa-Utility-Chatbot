# Action state

## What this is

`pipeline._doing()` reads live state and tells her, every turn, what she is actually
doing right now. Today that means music. It's the extension point for anything she gains
later.

## Why it's here

Two bugs, one cause: **her own actions weren't in her context.**

- She had to spend a tool call (`now_playing`) to learn what she was playing.
- She'd say *"เปิดละ"* (put it on) with nothing queued — three turns running — because
  pass 1 wrote *"putting it on now"* in its brief and pass 2 believed it.

Writing an intention is not doing a thing. So what's true is now decided from live
state, in code, and handed to her whether she asks or not.

## Diagram

```mermaid
stateDiagram-v2
    [*] --> Silent
    Silent --> Playing : play_music queued<br/>+ bot.py flush
    Playing --> Playing : queue_music (appends)
    Playing --> Playing : skip_music (next track)
    Playing --> Silent : stop_music
    Playing --> Silent : track ends, queue empty
    Playing --> Silent : leave_voice (deck cleared)
    note right of Silent
        _doing() says:
        "no music is playing —
        do NOT say you are
        putting a song on"
    end note
    note right of Playing
        _doing() says:
        "you are playing X.
        Queued next: Y"
    end note
```

<figcaption>The deck has two states, and she is told which one she's in on every single
turn.</figcaption>

## How it works here

```python
def _doing() -> str:
    out = []
    if music.NOW["title"]:
        # "You are playing X right now. Queued next: Y. You know this
        #  without looking it up — if they ask what is on, just tell them."
    if queued:      # what she set in motion THIS turn
        # "You have just done this: play bad apple. It IS happening."
    elif not music.NOW["title"]:
        # "No music is playing and none was queued this turn —
        #  do NOT say you are putting a song on."
    if tools.PENDING_LEAVE:
        # "You are leaving the voice channel as you say this."
```

The output goes into pass 2's `[inner-state]` block. Three jobs in one function:

1. **What's playing** — so she never needs a tool call to know her own deck.
2. **What she just queued** — so she confirms it instead of denying it. This also
   carries "you don't need to recognise a song to put it on", which fixed her saying
   *"never heard of it"* while the track was already starting.
3. **What is *not* happening** — so she can't claim an action she didn't take.

### `now_playing` was deleted

Once the deck is in her context every turn, a tool that reports the deck is a wasted
round-trip. Removing it also removed one competitor for the model's attention — and
missing `play_music` was an active bug at the time. Nine tools instead of ten.

### Adding a future feature

One `if` per subsystem. When she gains lights, timers, or a download:

```python
if lights.ON:
    out.append(f"The {lights.WHERE} lights are on because you turned them on.")
```

No registry, no state machine, no event bus. The rule that matters is the one already
holding: **read live state, never what the model believes it did.**

## Gotchas

- **It's still a prompt.** `_doing()` puts *true* information in front of her, but
  nothing forces her to respect it. She has been observed role-playing
  `*puts on some upbeat music*` with nothing queued, roughly 1 turn in 6. A hard fix
  needs a retry in code — see [where to go next](../reference/next.md).
- **Lazy import of `tiwa.music`** inside the function, so `chat.py` and the benches
  don't drag in PyAV and numpy.
- **`music.NOW` is one global deck**, not per-guild. Two simultaneous voice calls would
  read each other's state.
- **Clearing the deck is `bot.py`'s job.** `_hang_up()` does it, because disconnecting
  kills the audio but leaves `NOW` claiming a song is on.

## Go deeper

- [Music and the DJ](../surfaces/music.md) — what sets this state.
- [Her persona](persona.md) — where the output lands.
- [The tool registry](tools.md) — why fewer tools is better.
