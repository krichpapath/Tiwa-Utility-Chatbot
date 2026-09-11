"""Acceptance regressions for deployment boundaries; all services faked."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import tempfile
import subprocess
import os
import runpy
import time
from types import SimpleNamespace as Obj
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import gcal, llm, memory, minis, tools, voice
import bot

bot.db = memory.connect(":memory:")

bot.player.db = bot.calendar.db = bot.db
PLAN = dict(
    action="add", title="QA dentist", start="2026-09-10T15:00:00", end="2026-09-10T16:00:00"
)


class Channel:
    id = 42
    guild = None

    def __init__(self):
        self.sent = []

    async def send(self, text):
        self.sent.append(text)
        return Obj(id=len(self.sent), add_reaction=self.react)

    async def react(self, *args):
        pass


async def boundaries():
    ch = Channel()
    bot.calendar.owner_id = "7"
    bot.client = Obj(user=Obj(id=999), get_channel=lambda _: ch)
    bot.player.client = bot.client
    bot.calendar.pending.clear()
    tools.new_turn()
    tools.calendar_write(bot.db, "dentist tomorrow at three")
    with patch.object(gcal, "prepare_change", return_value=PLAN) as prepare:
        await bot.calendar.propose(ch)
    assert prepare.call_count == 1 and "10/09/2026 15:00" in ch.sent[0]
    mid = next(iter(bot.calendar.pending))
    reaction = Obj(message_id=mid, user_id=8, channel_id=42, emoji="✅")
    with patch.object(gcal, "apply_change", return_value="added") as apply:
        await bot.on_raw_reaction_add(reaction)
        assert not apply.called and mid in bot.calendar.pending
        reaction.user_id, reaction.channel_id = 7, 43
        await bot.on_raw_reaction_add(reaction)
        assert not apply.called
        reaction.channel_id = 42
        await bot.on_raw_reaction_add(reaction)
        await bot.on_raw_reaction_add(reaction)
        apply.assert_called_once_with(PLAN)
        bot.calendar.pending[mid] = (PLAN, 42, time.monotonic() - 1)
        await bot.on_raw_reaction_add(reaction)
        assert apply.call_count == 1 and "expired" in ch.sent[-1]
    tools.play_music(bot.db, "lofi")
    await bot.player.flush(ch, Obj())
    assert "server voice channel" in ch.sent[-1]
    assert "server voice channel" in await bot.player.leave(None)
    assert "not in a voice channel" in await voice.join(Obj())
    assert "not in a voice channel" in await voice.leave(None)
    other = Channel()
    other.id = 43
    bot.voice_channel.update({1: ch, 2: other})
    with patch.object(voice, "wake", return_value=None):
        await bot._heard("QA", "only this server hears this", channel=ch)
    assert not other.sent and "only this server" in ch.sent[-1]
    import os

    with (
        patch.dict(os.environ, {"TIWA_VOICE_REPLY": "0"}),
        patch.object(bot.pipeline, "respond") as respond,
    ):
        await bot._heard("QA", "play a song", channel=ch, activated=True)
        assert "play a song" in ch.sent[-1] and not respond.called
        await bot._heard("QA", "", channel=ch, activated=True, error="transcription unavailable")
        assert "transcription unavailable" in ch.sent[-1] and not respond.called
    with (
        patch.dict(os.environ, {"TIWA_VOICE_REPLY": "1"}),
        patch.object(bot.pipeline, "respond", return_value="Playing your song") as respond,
        patch.object(memory, "extract"),
        patch.object(voice, "say"),
        patch.object(bot.player, "flush") as music_flush,
        patch.object(bot.calendar, "propose") as calendar_flush,
        patch.object(bot.player, "flush_leave") as leave_flush,
    ):
        ch.sent.clear()
        await bot._heard("QA", "play a song", channel=ch, activated=True)
        assert "play a song" in ch.sent[0]
        assert ch.sent[1] == "Playing your song"
        assert respond.await_args.args[2:4] == ("QA", "play a song")
        music_flush.assert_awaited_once_with(ch)
        calendar_flush.assert_awaited_once_with(ch, None)
        leave_flush.assert_awaited_once_with(ch, "play a song")
        await asyncio.sleep(0.05)  # let the mocked background extraction finish
    print("PASS activated voice posts transcript, answers, and flushes requested actions")
    bot.voice_channel.clear()
    msg = Obj(
        author=Obj(id=7, bot=False, display_name="QA"),
        content="<@999> status",
        attachments=[],
        guild=Obj(voice_client=None),
        mentions=[bot.client.user],
        channel=ch,
    )
    with patch.object(bot.pipeline, "respond") as respond:
        await bot.on_message(msg)
        assert "Online. Voice: off" in ch.sent[-1]
        respond.assert_not_called()
    print("PASS owner gate, exact preview, one shot, wrong channel, expiry, DM voice/music")


def calendar_failures():
    with patch.object(llm, "chat", side_effect=ConnectionError("offline")):
        assert "calendar change failed" in gcal.apply_change("dentist tomorrow")
    for bad in (
        {},
        [],
        dict(PLAN, action="move"),
        dict(PLAN, title=""),
        dict(PLAN, start="2026-09-10"),
        dict(PLAN, end="2026-09-10T14:00:00"),
    ):
        with patch.object(gcal, "_service") as service:
            assert "failed" in gcal.apply_change(bad)
            assert not service.called
    event = dict(
        id="event1", summary=PLAN["title"], start={"dateTime": "2026-09-10T15:00:00+07:00"}
    )
    with patch.object(gcal, "_service") as service:
        service.return_value.events.return_value.list.return_value.execute.return_value = {
            "items": [event, event]
        }
        assert "multiple" in gcal.apply_change(dict(PLAN, action="cancel"))
        assert not service.return_value.events.return_value.delete.called
    print("PASS calendar outage, malformed plans, reversed times, ambiguous cancellation")


def parsing_and_memory():
    tools.new_turn()
    with patch.object(llm, "chat", side_effect=AssertionError("exact skip needs no model")):
        assert minis.dj(bot.db, "skip")["action"] == "skip"
    assert tools.DJ == [("skip", "")]
    for bad in ("null", "[]", '{"dispatch": 3}', '{"dispatch": [{"mini": []}]}'):
        assert minis.parse(bad)["dispatch"] == []
    db = memory.connect(":memory:")
    with patch.object(memory.time, "time", return_value=12345):
        memory.reflect(db, "old reflection")
        for i in range(3):
            memory.store_extraction(db, "QA", {"memories": [], "episode": f"event {i}"})
    assert [s for _, s in memory.unreflected(db)] == ["event 2", "event 1", "event 0"]
    print("PASS malformed router data, equal-timestamp memory watermark and ordering")
    for payload in ("null", "[]", "{broken", '{"memories": 42}'):
        with patch.object(llm, "chat", return_value={"content": payload}):
            memory.extract(db, "QA", "hello", "hello")
    with patch.object(llm, "chat", side_effect=ConnectionError("offline")):
        memory.extract(db, "QA", "hello", "hello")
    assert db.execute("SELECT count(*) FROM log WHERE kind='error'").fetchone()[0] == 5
    print("PASS failed/malformed extraction is logged and never crashes the conversation")


def usage_guard():
    with tempfile.TemporaryDirectory(prefix="tiwa-usage-") as scratch:
        ledger = Path(scratch) / "spend.json"
        with patch.object(llm, "SPEND_FILE", ledger):
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(llm.spend, [1] * 80))
            assert llm.spend() == 80
            script = "from tiwa import llm; [llm.spend(1) for _ in range(20)]"
            env = dict(os.environ, TIWA_SPEND_FILE=str(ledger))
            workers = [
                subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.DEVNULL)
                for _ in range(3)
            ]
            assert all(p.wait(timeout=30) == 0 for p in workers)
            assert llm.spend() == 140
            with (
                patch.object(llm, "DAILY_TOKENS", 100),
                patch.object(llm, "LOG_PROMPTS", False),
                patch.object(llm, "_openrouter_chat") as remote,
                patch.object(
                    llm, "_ollama_chat", return_value={"content": "local", "tool_calls": []}
                ) as local,
            ):
                assert llm.chat(provider="openrouter")["content"] == "local"
                assert local.called and not remote.called
            ledger.write_text("{broken", encoding="utf-8")
            try:
                llm.spend()
            except RuntimeError:
                pass
            else:
                raise AssertionError("corrupt ledger silently reset paid usage")
    print(
        "PASS usage thread/process contention, token ceiling routing, corrupt ledger fails closed"
    )


def graph_boundary():
    with tempfile.TemporaryDirectory(prefix="tiwa-graph-") as scratch:
        db = memory.connect(":memory:")
        memory.remember(
            db, "</script><script>alert(1)</script>", "likes", "QA", "<img src=x onerror=alert(1)>"
        )
        with (
            patch.object(memory, "connect", return_value=db),
            patch.object(memory, "DATA_DIR", Path(scratch)),
        ):
            runpy.run_path(str(Path(__file__).resolve().parents[1] / "graph_view.py"))
        rendered = (Path(scratch) / "graph.html").read_text(encoding="utf-8")
        assert "</script><script>alert" not in rendered
        assert "<img src=x" not in rendered
    print("PASS graph treats stored names and tooltip notes as data, not executable HTML")


asyncio.run(boundaries())
calendar_failures()
parsing_and_memory()
usage_guard()
graph_boundary()
