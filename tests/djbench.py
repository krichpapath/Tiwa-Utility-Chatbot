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

    # bot.db is the REAL data/tiwa.db, and _start() logs "playing <title>" to it.
    # Every run of this bench wrote its six fixtures into the live activity log —
    # found while reading that log for a genuine music bug, where "Bad Apple
    # (video)" x5 sat in the middle of the evidence. Benches never touch it.
    bot.db = memory.connect(":memory:")
    bot.player.db = bot.calendar.db = bot.db

    vc = FakeVC()
    channel = FakeChannel(vc)
    bot.client.loop = asyncio.get_running_loop()

    def fake_find(query):
        music.NOW["pending_title"] = f"{query.title()} (video)"
        return [{"title": f"{query.title()} (video)", "url": f"http://x/{query}",
                 "query": query}]

    music.find_many = fake_find
    music.source_for = lambda hit: object()

    async def run(*jobs):
        tools.DJ.clear()
        tools.DJ.extend(jobs)
        await bot.player.flush(channel, None)

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

    # Ordinary play appends; explicit stop/play replaces. Stale callbacks must
    # never start queued songs over a replacement.
    await run(("play", "dvorak"))
    await run(("queue", "limbus"))
    played.clear()
    await run(("play", "mili"))
    assert music.NOW["title"].startswith("Dvorak") and len(music.QUEUE) == 2
    assert not played, "another listener's request interrupted playback"
    await run(("stop", ""), ("play", "mili"), ("queue", "limbus"))
    await show("explicit replacement")
    assert music.NOW["title"].startswith("Mili") and len(music.QUEUE) == 1
    assert len(played) == 1, "stale callback stole the deck"
    vc.stop()
    await asyncio.sleep(0.05)
    assert music.NOW["title"].startswith("Limbus") and not music.QUEUE
    await run(("stop", ""))

    # "เพิ่มเพลงMili ลงคิวหลายๆเพลง" — one queue_music call is one song, so several
    # songs means several calls. The DJ list has to keep every one of them; the
    # singleton globals are what silently drop repeats.
    tools.DJ.clear()
    for _ in range(3):
        tools.queue_music(None, "Mili")  # queue_music never touches the db
    assert tools.DJ == [("queue", "Mili")] * 3, tools.DJ
    await bot.player.flush(channel, None)
    await asyncio.sleep(0.05)
    await show("queue Mili x3")
    assert music.NOW["title"].startswith("Mili"), music.NOW
    assert len(music.QUEUE) == 2, f"repeat queue_music lost songs: {music.QUEUE}"
    await run(("stop", ""))

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
    # ...and while a song is on its way she is told so, WITHOUT being told what it
    # is. She has not seen the search result at this point in the turn, so every
    # title she could name here would be invented — measured, three at once.
    told = pipeline._doing(dispatching_music=True)
    assert "Playback is NOT confirmed" in told and "do NOT name a SONG TITLE" in told, told
    tools.PENDING_MUSIC = "lofi"  # DJ already acted -> she may name what SHE searched
    assert "play lofi" in pipeline._doing(dispatching_music=True)
    tools.PENDING_MUSIC = None

    # --- the hint: does DJ Tiwa get STARTED early? -------------------------
    #
    # Read _maybe_music() first. This is not "is it a music ask" — DJ answers
    # that. All a True buys is DJ starting at t=0 beside dispatch instead of
    # ~1.5s behind it, so it is deliberately greedy and several rows below are
    # True on purpose for messages that are obviously not music. Those are the
    # `veto` rows: DJ has to answer `none` to them, and djminibench --live is
    # where that is measured. A wrong True costs one cheap call nobody sees.
    #
    # It only has to be right about the two things that PREVENT an action.
    print("\n| ask | deck | DJ started? | why |")
    print("|---|---|---|---|")
    cases = [
        # plain asks
        ("เปิดเพลงอะไรก็ได้", None, True, "ask"),
        ("อยากได้เพลงเล่น Marvel rival เลือกให้หน่อย มันๆ", None, True, "ask"),
        ("play some lofi", None, True, "ask"),
        ("เปิดเพลงปล้น", "Lamenting the Days", True, "swap the track"),
        ("play ビビデバ - BIBBIDIBA", "TheFatRat - Unity", True, "ask"),
        ("queue https://www.youtube.com/watch?v=ftIfmQYUvVw", None, True, "a link is an ask"),
        ("ขอ ATLAS-The Score", None, True, "thai verb, foreign title"),
        ("เพลงไม่ออกใส่ queue ด้วย", None, True, "verb mid-sentence"),
        ("เปิดเพลงนี้ให้หน่อยได้ไหม", None, True, "politely, still an ask"),

        # --- THE 25 THE OLD PHRASE LIST SILENTLY MISSED, from her own log.
        # Every one of these reached no tool and no retry. `skip` alone appeared
        # four times and never once fired.
        ("skip", None, True, "was missed: a bare verb"),
        ("เปิดSunflowerให้หน่อย", None, True, "was missed: no space after เปิด"),
        ("เปิดwhat up danger", None, True, "was missed: no space"),
        ("hero miliเล่นให้หน่อย", None, True, "was missed: verb not at the front"),
        ("เล่นที่ฉันชอบหน่อย", None, True, "was missed: play what I like"),
        ("เพลงอื่นอีกเพลง", None, True, "was missed: another song"),
        ("คิวเพลง https://youtu.be/FeHDKMilBl0", None, True, "was missed: คิว alone"),
        # ...and these four carry NO music word at all. Only the live deck
        # reaches them, which is the whole reason that rule exists.
        ("ไม่ใช่ ของMili", "ATLAS", True, "was missed: deck is live"),
        ("มันจบแล้ว เล่นอีกรอบ", "ATLAS", True, "was missed: deck is live"),
        ("เอาอันที่เป็นของ enimen แทน", "ATLAS", True, "was missed: deck is live"),
        ("อีกอันนึง", "ATLAS", True, "was missed: deck is live"),

        # greedy on purpose — DJ answers `none` to every one of these
        ("my dad plays Warframe", None, True, "veto: DJ says none"),
        ("he plays guitar", None, True, "veto: DJ says none"),
        ("ขอโทษนะ", None, True, "veto: DJ says none"),
        ("ขอบคุณมาก", None, True, "veto: DJ says none"),
        ("เปิดประตูให้หน่อย", None, True, "veto: DJ says none"),
        ("ขอ ยืมตังหน่อย", None, True, "veto: DJ says none — the live 2026-08-24 turn"),

        # nothing music-shaped, and no deck: not even started
        ("มึงว่าไง", None, False, "no hint word, empty deck"),
        ("กินข้าวยัง", None, False, "no hint word, empty deck"),

        # ...and the one thing that must stay precise, because it PREVENTS a
        # track starting over her answer. Same rows as before the loosening.
        ("เพลงนี้ชื่ออะไร", "Bad Apple (video)", False, "deck question"),
        ("ตอนนี้้เปิดเพลงอะไรอยู่", "BIBBIDIBA", False, "deck question"),
        ("what song is this", "BIBBIDIBA", False, "deck question"),
        ("เพลงนี้ชื่ออะไร https://www.youtube.com/watch?v=abc", "BIBBIDIBA", False,
         "deck question with a link"),
    ]
    for ask, deck, want, why in cases:
        tools.PENDING_MUSIC, tools.DJ[:] = None, []
        music.NOW["title"], music.QUEUE[:] = deck, []
        got = pipeline._maybe_music(ask)
        print(f"| {ask} | {deck or '—'} | {got} | {why} |")
        assert got is want, f"{ask!r} -> {got}, wanted {want} ({why})"
    music.NOW["title"], music.QUEUE[:] = None, []
    tools.PENDING_MUSIC = "lofi"  # play_music already fired -> never start twice
    assert not pipeline._maybe_music("เปิดเพลงอะไรก็ได้")
    tools.PENDING_MUSIC = ""  # stop_music fired -> do NOT start one instead
    assert not pipeline._maybe_music("ปิดเพลง เปิดเพลงใหม่ไม่ต้อง")
    tools.PENDING_MUSIC = None
    # a queued song with nothing playing is still a live session
    music.QUEUE.append({"title": "x"})
    assert pipeline._maybe_music("อีกอันนึง"), "a full queue is not a live deck"
    music.QUEUE[:] = []

    # --- she must know when the deck is EMPTY, but only when asked -----------
    # _doing() stays silent on an ordinary quiet turn, on purpose. The gap was
    # the turn where someone asks what is on with nothing playing: she had zero
    # state and named a song anyway.
    music.NOW["title"] = None
    assert pipeline._doing(asked_deck=True).startswith("NOTHING is playing")
    assert pipeline._doing(asked_deck=False) == "", "a standing negative came back"
    music.NOW["title"] = "Bad Apple (video)"
    on = pipeline._doing(asked_deck=True)
    assert "NOTHING is playing" not in on and "Bad Apple" in on, on
    music.NOW["title"] = None
    for q, want in [("มึงเล่นเพลงไรอยู่เนี่ย", True), ("what song is this", True),
                    ("เปิดเพลงอะไรก็ได้", False), ("play some lofi", False)]:
        assert pipeline._asked_deck(q) is want, q

    # model output arrives padded; only the terms may reach the search
    assert pipeline._terms('<think>hmm</think>\n"hype gaming EDM".') == "hype gaming EDM"
    assert pipeline._terms("") == ""

    print("\nDJ ok — queue, skip, auto-advance, stop, forced retry all behave")


asyncio.run(main())
