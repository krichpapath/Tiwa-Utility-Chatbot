"""Search quality guards with misleading rankings and incomplete evidence. Offline."""
import json
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tiwa import llm, memory, minis, music, pipeline, tools


def song(id, title, channel="", duration=180, **extra):
    return dict(id=id, title=title, channel=channel, duration=duration,
                url=f"https://www.youtube.com/watch?v={id}", **extra)


class YDL:
    resolved = []
    infos = {}

    def __init__(self, options):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def extract_info(self, url, download=False):
        self.resolved.append(url)
        result = self.infos[url]
        if isinstance(result, Exception):
            raise result
        return result


def music_checks():
    assert music._rank("Warframe Red Line", song("red", "Warframe Gauss: Redline Official Music Video")) >= 0
    cover = song("cover", "Mili Hero cover")
    wrong = song("wrong", "Hero", "Other artist")
    fan = song("fan", "Mili Hero lyrics")
    official = song("official", "Hero (Official Audio)", "Mili")
    remix = song("remix", "Mili Hero remix")
    YDL.infos = {s["url"]: s for s in (cover, wrong, fan, official, remix)}
    with patch("yt_dlp.YoutubeDL", YDL):
        with patch.object(music, "_search", return_value=[cover, wrong, fan, official]) as search:
            assert music.find("Mili Hero")["id"] == "official"
            assert search.call_count == 1
        with patch.object(music, "_search", return_value=[official, remix]):
            assert music.find("Mili Hero remix")["id"] == "remix"
        with patch.object(music, "_search", side_effect=[[wrong], [official]]) as search:
            assert music.find("Mili Hero")["id"] == "official"
            assert search.call_count == 2
        YDL.infos[official["url"]] = LookupError("unavailable")
        with patch.object(music, "_search", return_value=[official, fan]):
            assert music.find("Mili Hero")["id"] == "fan"
        YDL.infos[official["url"]] = LookupError("HTTP Error 429")
        with patch.object(music, "_search", return_value=[official, fan]) as search:
            try:
                music.find("Mili Hero")
                raise AssertionError("rate limit ignored")
            except LookupError as error:
                assert "rate-limiting" in str(error)
            assert search.call_count == 1
        for bad in (song("long", "Mili Hero", duration=7200),
                    song("stream", "Mili Hero", is_live=True), wrong):
            with patch.object(music, "_search", return_value=[bad]) as search:
                try:
                    music.find("Mili Hero")
                    raise AssertionError("unrelated/mix/live accepted")
                except LookupError as error:
                    assert "no confident" in str(error)
                assert search.call_count == 3
        # Flat duration can lie: resolving must not accept a livestream.
        YDL.infos[official["url"]] = {**official, "is_live": True, "duration": 0}
        with patch.object(music, "_search", return_value=[official]):
            try:
                music.find("Mili Hero")
                raise AssertionError("resolved live accepted")
            except LookupError:
                pass
    print("music: ranking, requested version, retry, dead result, rate limit, live/mix guards pass")


def reply(enough=False, answer="", sources=None, query=""):
    return {"content": json.dumps(dict(enough=enough, answer=answer,
                                       sources=sources or [], query=query))}


def web_checks():
    db = memory.connect(":memory:")
    with patch.object(llm, "chat", return_value={"content": json.dumps({"query": "", "clarification": "Which teams or league?"})}), patch.object(tools, "web_search") as search:
        out = minis.search(db, "Who won last night?")
        assert "Which teams" in out["found"]
        search.assert_not_called()
    with patch.object(llm, "chat", side_effect=ConnectionError("private provider error")):
        out = minis.search(db, "fixture fact?")
        assert "unavailable" in out["found"] and "private provider error" not in out["found"]
    irrelevant = "Old release [example.com] https://example.com/old: old preview, no final specs"
    relevant = "Official specs [maker.example] https://maker.example/spec: Fixture X supports 96 GB"
    calls = [{"content": "Fixture X memory limit"},
             reply(query="Fixture X maximum RAM site:maker.example"),
             reply(True, "Fixture X supports 96 GB.", [2])]
    with patch.object(llm, "chat", side_effect=calls), patch.object(tools, "web_search", side_effect=[irrelevant, relevant]) as search:
        out = minis.search(db, "What memory limit does Fixture X have?")
        assert search.call_count == 2
        assert "96 GB" in out["found"] and "https://maker.example/spec" in out["found"]
        assert "example.com/old" not in out["found"]
    for reviews in (
        [reply(True, "made up", [99])],
        [{"content": "null"}],
        [reply(query="fixture")],  # duplicate: don't burn the entire budget
        [reply(query="second"), reply(query="third"), reply(query="fourth")],
        [ConnectionError("provider gone")],
    ):
        with patch.object(llm, "chat", side_effect=[{"content": "fixture"}, *reviews]), patch.object(tools, "web_search", return_value=irrelevant) as search:
            out = minis.search(db, "fixture fact?")
            assert "Could not establish" in out["found"], out
            assert search.call_count <= 3
    with patch.object(llm, "chat", side_effect=[{"content": "fixture"}, reply(True, "fake", [1])]), patch.object(tools, "web_search", return_value="search failed: private error"):
        out = minis.search(db, "fixture fact?")
        assert "fake" not in out["found"] and "private error" not in out["found"]
    print("web: evidence refinement, provenance, bad IDs/JSON, duplicate/budget and outage guards pass")


def mood_choice():
    tools.new_turn()
    responses = [{"content": json.dumps(dict(action="play", terms="study music", selection="mood"))},
                 {"content": json.dumps(dict(title="Aruarian Dance", artist="Nujabes"))}]
    with patch.object(llm, "chat", side_effect=responses) as chat:
        result = minis.dj(memory.connect(":memory:"), "play something chill")
        assert result["terms"] == tools.PENDING_MUSIC == "Nujabes Aruarian Dance"
        assert chat.call_count == 2
    tools.new_turn()
    with patch.object(llm, "chat", return_value={"content": json.dumps(dict(action="none", terms="", selection="mood"))}) as chat:
        minis.dj(memory.connect(":memory:"), "I enjoy chill music")
        assert chat.call_count == 1 and tools.PENDING_MUSIC is None
    print("mood: concrete artist/title reaches deck; a stated taste never invokes picker")


music_checks()
web_checks()
mood_choice()


async def reply_links():
    db = memory.connect(":memory:")
    send = AsyncMock()
    facts = ["search: {'found': 'Source https://example.com/page: quoted evidence'}"]
    with patch.object(pipeline, "say", AsyncMock(return_value="Answer with no link")):
        line = await pipeline._voice(db, "QA", facts, send)
        assert "https://example.com/page" in line
        send.assert_awaited_once_with(line)
    with patch.object(pipeline, "say", AsyncMock(return_value="Answer https://example.com/page")):
        line = await pipeline._voice(db, "QA", facts, send)
        assert line.count("https://example.com/page") == 1
    print("reply: cited source survives persona rewriting, without duplicate links")


asyncio.run(reply_links())
