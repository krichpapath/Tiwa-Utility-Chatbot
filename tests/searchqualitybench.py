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
    assert music._rank('The Tender Box Spectacular Spider-Man (Main Title)',
                       song('spider', 'The Spectacular Spider-Man', 'The Tender Box - Topic')) >= 0
    db = memory.connect(":memory:")
    for request in ("ที่ว่าไม่ใช่เพลงนี้ มีลิจากลิมบัสคอมพานีต่างหาก",
                    "เอ้ย พี่ว่าหยุดเพลงนี้แล้วเปิดเพลงมิลิมบัสคอมพานีแทน"):
        tools.new_turn()
        with patch.object(llm, "chat", side_effect=AssertionError("source correction needs no invented title")):
            result = minis.dj(db, request)
        assert result["action"] == "play" and result["terms"] == "Mili Limbus Company"
    tools.new_turn()
    assert music._rank("Hound Dog R★O★C★K★S", song("rocks", "Hound Dog - Rocks (Naruto Opening)")) >= 0
    assert music._rank("Hound Dog Rocks", song("rocks", "Hound Dog R★O★C★K★S")) >= 0
    assert music._rank("Hound Dog R★O★C★K★S", song("wrong", "Rocks", "Other artist")) < 0
    assert music._rank("Hound Dog Rocks", song("tuned", "Hound Dog - Rocks (432Hz)")) < 0
    assert music._rank("Steven Universe songs", song("su", "Steven Universe - Love Like You (Official Audio)")) >= 0
    assert music._rank("Steven Universe songs", song("wrong", "Other Cartoon Theme")) < 0
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
        assert result["terms"] == "study music"
        assert tools.PENDING_MUSIC == {"keywords": "study music", "request": "play something chill"}
        assert chat.call_count == 1
    tools.new_turn()
    with patch.object(llm, "chat", return_value={"content": json.dumps(dict(action="none", terms="", selection="mood"))}) as chat:
        minis.dj(memory.connect(":memory:"), "I enjoy chill music")
        assert chat.call_count == 1 and tools.PENDING_MUSIC is None
    for selection in ("named", "mood"):
        request = "เหตุที่ว่า เปิดเพลง Say Fun Fair ที่ราคามิฟูบุกิ"
        tools.new_turn()
        with patch.object(llm, "chat", return_value={"content": json.dumps(dict(action="play", terms="bad spelling", selection=selection))}):
            minis.dj(memory.connect(":memory:"), request)
        assert tools.PENDING_MUSIC == {"keywords": "bad spelling", "request": request}
    tools.new_turn()
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    with patch.object(llm, "chat", return_value={"content": json.dumps(dict(action="play", terms=url, selection="named"))}):
        minis.dj(memory.connect(":memory:"), "play " + url)
    assert tools.PENDING_MUSIC == url
    print("named/mood: original request reaches evidence search; explicit URLs preserved")


@patch.object(music, "_music_queries", return_value=[])
def discovery_checks(_plan):
    correct = song("abcdefghijk", "ビビデバ / 星街すいせい(official)", "Suisei Channel")
    wrong = song("12345678901", "Suisei", "Yoh Kamiyama")
    YDL.infos = {correct["url"]: correct}
    request = {"request": "เล่นเพลงของสุยเซโฮโลไลฟ์ให้หน่อย",
               "keywords": "Yoh Kamiyama Suisei Hololive"}
    reviews = [{"content": json.dumps(dict(index=-1, query="星街すいせい official music"))},
               {"content": json.dumps(dict(index=1, query=""))}]
    with patch("yt_dlp.YoutubeDL", YDL), patch.object(music, "_search", side_effect=[[wrong], [correct]]) as search, patch.object(llm, "chat", side_effect=reviews) as chat:
        assert music.find(request)["id"] == correct["id"]
        assert search.call_count == 2
        assert request["request"] in chat.call_args.kwargs["messages"][1]["content"]
        assert "json" in chat.call_args.kwargs["messages"][0]["content"].lower()
    # A reviewer that gives up or repeats itself still gets the next phonetic hypothesis.
    _plan.return_value = ["wrong hypothesis", "Suisei official"]
    with patch("yt_dlp.YoutubeDL", YDL), patch.object(music, "_search", side_effect=[[wrong], [correct]]) as search, patch.object(llm, "chat", side_effect=[
        {"content": '{"index":-1,"query":""}'},
        {"content": '{"index":1,"query":""}'}]):
        assert music.find(request)["id"] == correct["id"]
        assert search.call_count == 2
    _plan.return_value = []
    for response in ("null", '{"index":99,"query":""}', '{"index":-1,"query":"Yoh Kamiyama Suisei Hololive"}'):
        with patch.object(music, "_search", return_value=[wrong]) as search, patch.object(llm, "chat", return_value={"content": response}):
            try:
                music.find(request)
                raise AssertionError("invalid/unverified selection accepted")
            except LookupError:
                pass
            assert search.call_count == 1
    with patch.object(music, "_search", return_value=[wrong]) as search, patch.object(llm, "chat", side_effect=[{"content": json.dumps(dict(index=-1, query=q))} for q in ("second", "third", "fourth")]):
        try:
            music.find(request)
            raise AssertionError("budget ignored")
        except LookupError:
            pass
        assert search.call_count == 3
    YDL.infos[correct["url"]] = {**correct, "is_live": True}
    with patch("yt_dlp.YoutubeDL", YDL):
        try:
            music.find(correct["url"])
            raise AssertionError("live resolved video accepted")
        except LookupError:
            pass
    print("discovery: wrong keywords refined, real candidate selected, invalid index/JSON, budget and resolved-live guards pass")

for malformed in ('null', '{"queries":"not a list"}', '{"queries":[null,42]}'):
    with patch.object(llm, "chat", return_value={"content": malformed}):
        assert music._music_queries("fixture", "fixture") == []

discovery_checks()

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
