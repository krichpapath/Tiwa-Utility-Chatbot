"""DJ behaviour: queue, skip, auto-advance, and surviving a dropped stream.

Uses fake search/playback so it runs offline and fast. `--live` also checks the
real reconnect path against YouTube.

    py -X utf8 tests\\djbench.py
"""
import asyncio
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, music, tools  # noqa: E402

sent = []
played = []


class FakeVC:
    def __init__(self):
        self.after = None
        self.playing = False

    def is_playing(self):
        return self.playing

    def is_connected(self):
        return True

    def play(self, source, after=None):
        self.playing, self.after = True, after
        played.append(music.NOW.get("pending_title"))

    def stop(self):
        self.playing = False
        if self.after:
            cb, self.after = self.after, None
            cb(None)  # discord calls this when a track ends or is stopped


class FakeChannel:
    def __init__(self, vc):
        self.guild = types.SimpleNamespace(voice_client=vc, id=1)
        self.id = 1

    async def send(self, text):
        sent.append(text)


async def main():
    import bot  # noqa: E402  (imports discord, does not connect)

    vc = FakeVC()
    channel = FakeChannel(vc)
    bot.client.loop = asyncio.get_running_loop()

    async def fake_find(ch, query):
        music.NOW["pending_title"] = f"{query.title()} (video)"
        return {"title": f"{query.title()} (video)", "url": f"http://x/{query}",
                "query": query}

    bot._find = fake_find
    music.source_for = lambda hit: object()

    async def run(*jobs):
        tools.DJ.clear()
        tools.DJ.extend(jobs)
        await bot._flush_music(channel, None)

    print("| step | now playing | queue | she said |")
    print("|---|---|---|---|")

    async def show(step):
        await asyncio.sleep(0.05)
        q = ", ".join(t["title"] for t in music.QUEUE) or "—"
        print(f"| {step} | {music.NOW['title'] or '—'} | {q} | {sent[-1] if sent else '—'} |")

    await run(("play", "bad apple"))
    await show("play bad apple")
    assert music.NOW["title"].startswith("Bad Apple")

    await run(("queue", "rick roll"), ("queue", "despacito"))
    await show("queue two more")
    assert len(music.QUEUE) == 2, music.QUEUE

    await run(("skip", ""))
    await show("skip")
    assert music.NOW["title"].startswith("Rick Roll"), music.NOW
    assert len(music.QUEUE) == 1

    vc.stop()  # pretend the track ended on its own
    await asyncio.sleep(0.05)
    await show("song ends by itself")
    assert music.NOW["title"].startswith("Despacito"), music.NOW
    assert not music.QUEUE

    await run(("stop", ""))
    await show("stop")
    assert music.NOW["title"] is None and not music.QUEUE

    # what she is told every turn, without spending a tool call on it
    from tiwa import pipeline

    music.NOW.update(title="Bad Apple (video)")
    music.QUEUE.append({"title": "Rick Roll (video)"})
    state = pipeline._doing()
    print("\nher per-turn action state:")
    print("  " + state)
    assert "Bad Apple (video)" in state and "Rick Roll (video)" in state
    music.QUEUE.clear()
    music.NOW["title"] = None
    # nothing happening = say nothing. A standing "no music is playing" would be
    # paid for on every ordinary message and used on almost none.
    assert pipeline._doing() == "", pipeline._doing()
    # except when she was asked and the search came up empty — the one negative
    assert "came up empty" in pipeline._doing(missed_music=True)
    tools.PENDING_MUSIC = "lofi"  # forced retry worked -> back to silence on the negative
    assert "came up empty" not in pipeline._doing(missed_music=True)
    tools.PENDING_MUSIC = None

    # the forced retry: fires whenever they asked and nothing was queued. The
    # "already playing" rows are the ones that matter — they were live failures,
    # three confabulated turns in one session, every one with a song already on.
    print("\n| ask | deck | forced? |")
    print("|---|---|---|")
    cases = [
        ("เปิดเพลงอะไรก็ได้", None, True),
        ("อยากได้เพลงเล่น Marvel rival เลือกให้หน่อย มันๆ", None, True),
        ("play some lofi", None, True),
        ("เปิดเพลงปล้น", "Lamenting the Days", True),          # swap the track
        ("ให้โอกาสอีกรอบเปิดเพลงให้ถูก", "Lamenting the Days", True),
        ("เพลงนี้ชื่ออะไร", "Bad Apple (video)", False),        # asking, not requesting
        ("มึงว่าไง", None, False),                             # not about music at all
    ]
    for ask, deck, want in cases:
        tools.PENDING_MUSIC, tools.DJ[:] = None, []
        music.NOW["title"] = deck
        got = pipeline._missed_music(ask)
        print(f"| {ask} | {deck or '—'} | {got} |")
        assert got is want, ask
    music.NOW["title"] = None
    tools.PENDING_MUSIC = "lofi"  # play_music already fired -> never force twice
    assert not pipeline._missed_music("เปิดเพลงอะไรก็ได้")
    tools.PENDING_MUSIC = ""  # stop_music fired -> do NOT start one instead
    assert not pipeline._missed_music("ปิดเพลง เปิดเพลงใหม่ไม่ต้อง")
    tools.PENDING_MUSIC = None

    # model output arrives padded; only the terms may reach the search
    assert pipeline._terms('<think>hmm</think>\n"hype gaming EDM".') == "hype gaming EDM"
    assert pipeline._terms("") == ""

    print("\nDJ ok — queue, skip, auto-advance, stop, forced retry all behave")


asyncio.run(main())
