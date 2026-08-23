"""G9 — music from YouTube, decoded with PyAV. No ffmpeg binary needed.

yt-dlp finds a direct audio URL, av decodes it to PCM, discord plays the PCM.
"""
import os
import queue
import threading
import time
from urllib.parse import quote

import numpy as np

# Imported HERE, on the main thread, once. Importing av lazily inside the
# decoder thread blew up mid-init ("DLL load failed while importing
# dictionary") — a big native package being loaded from a worker thread while
# the CPU was busy. Import cost is paid at startup instead of at play time.
try:
    import av

    AV_ERROR = None
except Exception as e:  # pragma: no cover - depends on the machine
    av = None
    AV_ERROR = f"{type(e).__name__}: {e}"

SAMPLE_RATE = 48000
FRAME = SAMPLE_RATE // 50 * 4  # 20ms of 16-bit stereo, what discord asks for

# 1.0 = whatever loudness YouTube gave us. Turn it down so she can be heard
# talking over the track (bot.py applies it via discord's volume transformer).
VOLUME = float(os.environ.get("TIWA_MUSIC_VOLUME", "1.0"))


def ready() -> str:
    """Called at startup so decoder problems surface before you ask for a song."""
    return "music ready" if av is not None else f"music UNAVAILABLE — {AV_ERROR}"


# ponytail: one deck, not one per guild. She has one voice connection in
# practice; make it per-guild the day she is in two calls at once.
NOW = {"title": None, "query": None}
QUEUE = []  # [{"title","url","query"}] waiting to play


def deck() -> str:
    """What is playing and what is next — the text the model reads."""
    if not NOW["title"]:
        return "nothing playing"
    lines = [f"playing now: {NOW['title']}"]
    if QUEUE:
        lines.append("queued: " + " | ".join(t["title"] for t in QUEUE[:5]))
    return "\n".join(lines)

# YouTube stopped serving the default `web` client's stream urls to anything that
# is not a browser. They RESOLVE fine and then 403 the moment PyAV opens them, so
# the failure surfaces as "stream broke at 0s" long after the search looked like a
# success. Measured 2026-08-23 across three videos and eight clients: web,
# web_safari, web_music, web_embedded, mweb, ios, tv, tv_embedded, android_vr and
# android_music all fail; `android` resolves and plays all three.
#
# It costs bandwidth. The android client exposes no audio-only format at all — five
# formats, four of them storyboards — so `bestaudio` falls back to itag 18, a muxed
# 385 kbps mp4 whose picture we decode and throw away. Working beats tidy, and
# bestaudio still picks a real audio stream the day they expose one again.
#
# ponytail: env knob, because YouTube breaks a client every few months and the fix
# should not need a code edit. When it next 403s, `yt-dlp -F <url>` and try the
# clients above; whichever plays goes in TIWA_YT_CLIENT.
YT_CLIENT = os.environ.get("TIWA_YT_CLIENT", "android")

# "Sign in to confirm you're not a bot" is YouTube rate-limiting the whole IP, not
# a problem with one video. Cookies are the only thing that reliably answers it —
# it is literally what the error asks for. OFF by default, because the yt-dlp wiki
# warns that a logged-in account used for bot traffic can get flagged: use a
# throwaway Google account, never your real one.
#   TIWA_YT_COOKIES=C:\path\cookies.txt      exported cookies file
#   TIWA_YT_COOKIE_BROWSER=firefox           read them from an installed browser
YT_COOKIES = os.environ.get("TIWA_YT_COOKIES", "")
YT_COOKIE_BROWSER = os.environ.get("TIWA_YT_COOKIE_BROWSER", "")

_YDL = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "default_search": "ytsearch1",
    "extractor_args": {"youtube": {"player_client": [YT_CLIENT]}},
}
if YT_COOKIES:
    _YDL["cookiefile"] = YT_COOKIES
elif YT_COOKIE_BROWSER:
    _YDL["cookiesfrombrowser"] = (YT_COOKIE_BROWSER,)


# The difference between "this one video is gone" and "YouTube is refusing ME".
# The first is worth skipping past; the second means every remaining candidate
# will fail the same way, and asking four times in a row is what deepens the
# throttle. Measured 2026-08-24: a burst of test resolutions took this IP from
# intermittent to total in about an hour.
_BLOCKED = ("sign in to confirm", "not a bot", "429", "too many requests",
            "confirm you’re not a bot", "confirm you're not a bot")


def _rate_limited(err: Exception) -> bool:
    return any(s in str(err).lower() for s in _BLOCKED)
# flat = titles and durations only, no format resolution. Cheap enough to ask for
# five and then throw four away.
_FLAT = {**_YDL, "extract_flat": True}
SEARCH_N = 5

# YouTube's top hit for a mood is a 2-3 hour mix, measured: "hype gaming EDM" ->
# 131m, and all five of the top five were 131-183m. A mix is not a song — it
# outlives the whole conversation, so queue_music and auto-advance never fire and
# "playing now" stops meaning anything. Raise it when you want long mixes back.
MAX_TRACK_S = int(os.environ.get("TIWA_MAX_TRACK_MIN", "12")) * 60
_RECENT = []  # ponytail: last few video ids, so the same query stops replaying
              # the same video. A list because it never exceeds _RECENT_KEEP.
_RECENT_KEEP = 20


def _ok(e: dict, trust_short: bool = False) -> bool:
    """Could this result be a song?

    `trust_short` is for the filtered search below, whose entries report
    `duration: None` — measured, all 5 of them for "lofi study". On a plain search
    a missing duration means livestream; on that page YouTube has promised every
    result is under four minutes, so it means "not reported". It still lists live
    streams, which is why `find()` re-checks after resolving.
    """
    if "list=" in (e.get("url") or ""):
        return False  # a "Mix - ..." radio playlist, not a video
    seconds = e.get("duration")
    if seconds is None:
        return trust_short
    return 0 < seconds <= MAX_TRACK_S and e.get("id") not in _RECENT


# ponytail: YouTube's own "under 4 minutes" search filter. A mood query returns
# NOTHING but mixes — measured 0 songs in the top 20 for "hype gaming EDM", and
# 261/264 usable with this filter on. The sp= value is a magic constant that only
# YouTube defines, so it is a FALLBACK: the plain search decides first, and if
# YouTube ever changes it the code degrades to the old behaviour instead of
# breaking.
_SHORT = "https://www.youtube.com/results?search_query={q}&sp=EgIYAQ%3D%3D"


def _search(target: str, n: int) -> list:
    import yt_dlp

    with yt_dlp.YoutubeDL({**_FLAT, "playlistend": n}) as ydl:
        return (ydl.extract_info(target, download=False) or {}).get("entries") or []


def find(query: str) -> dict:
    """Search YouTube, return {title, url, duration, id} for the best song hit.

    A bare video id or url skips the search entirely — that is how `_decode`
    re-resolves the track it is ALREADY playing without risking a different song.
    """
    import yt_dlp

    if query.startswith("http"):  # a real url, not something to search for
        with yt_dlp.YoutubeDL(_YDL) as ydl:
            return _hit(ydl.extract_info(query, download=False))

    entries = _search(f"ytsearch{SEARCH_N}:{query}", SEARCH_N)
    cands = [e for e in entries if _ok(e)]
    if not cands:
        # the filtered page is a fallback because its sp= value is YouTube's, not ours
        # capped at 2: these are unverified (duration None), and the finite tail
        # below has to stay reachable inside the extraction budget
        cands = [e for e in _search(_SHORT.format(q=quote(query)), SEARCH_N)
                 if _ok(e, trust_short=True)][:2]
    # tail: anything with a length at all. Some queries have no song on YouTube —
    # "lofi study" is 24/7 streams most of the way down — and a long mix that ENDS
    # still lets the queue advance, which a livestream never does.
    seen = {e.get("id") for e in cands}
    cands += [e for e in entries if e.get("id") not in seen and e.get("duration")]
    if not cands:
        raise LookupError(f"no results for {query!r}")

    hit = best = None
    for cand in cands[:4]:  # flat metadata lies; the resolved duration does not
        try:
            with yt_dlp.YoutubeDL(_YDL) as ydl:
                hit = _hit(ydl.extract_info(cand["url"], download=False))
        except Exception as e:
            if _rate_limited(e):
                # Not this video's fault and not fixable by trying the next one —
                # every remaining candidate will fail identically, and asking is
                # what makes the throttle worse. Stop, and say something a person
                # can act on instead of relaying yt-dlp's four-line error.
                raise LookupError(
                    "YouTube is rate-limiting this machine (\"sign in to confirm "
                    "you're not a bot\"). Set TIWA_YT_COOKIES or "
                    "TIWA_YT_COOKIE_BROWSER, or wait it out."
                ) from e
            # A DEAD candidate must not lose the live ones behind it. This loop was
            # written to survive bad METADATA — livestreams, three-hour mixes — and
            # an unavailable video is a different failure that walked straight out
            # of find(). Live log 2026-08-23: "[youtube] 5Z8N9TTvKeQ: This video is
            # not available" ended the whole request while three good results sat
            # untried. Deleted, private, region-locked and age-gated all land here.
            # Same rule as the voice router: one bad packet must not deafen her.
            print(f"[music] skipping {cand.get('id') or cand.get('url')}: "
                  f"{type(e).__name__}: {str(e).splitlines()[-1][:90]}")
            continue
        if 0 < hit["duration"] <= MAX_TRACK_S:
            break  # a real song, not a livestream and not a three-hour mix
        if hit["duration"] and best is None:
            best = hit  # long, but it ends
    else:
        hit = best or hit
    if hit is None:
        # every candidate was dead. Same shape as no results at all, so bot._find
        # reports it in her voice instead of raising through the turn.
        raise LookupError(f"every result for {query!r} was unavailable")
    if hit["id"]:
        _RECENT.append(hit["id"])
        del _RECENT[:-_RECENT_KEEP]
    return hit


def watch_url(vid: str) -> str:
    return f"https://www.youtube.com/watch?v={vid}"


def _hit(info: dict) -> dict:
    if "entries" in info:
        info = info["entries"][0]
    return {
        "title": info.get("title", "?"),
        "url": info["url"],
        "duration": info.get("duration") or 0,
        "id": info.get("id", ""),
    }


class Stream:
    """discord.AudioSource over a PyAV-decoded stream.

    ponytail: a background thread decodes into a small queue. Discord calls
    read() every 20ms and will not wait for the network, so the buffer exists
    to absorb jitter, nothing more.
    """

    # libavformat's own HTTP reconnect. Without these a CDN hiccup ends the
    # song with "OSError: [Errno 5] I/O error" halfway through.
    _HTTP = {
        "reconnect": "1",
        "reconnect_streamed": "1",
        "reconnect_on_network_error": "1",
        "reconnect_on_http_error": "4xx,5xx",
        "reconnect_delay_max": "10",
        "rw_timeout": "20000000",  # 20s in microseconds
        "user_agent": "Mozilla/5.0",
    }

    def __init__(self, url: str, query: str = "", vid: str = "",
                 buffer_frames: int = 200):  # ~4s
        self.q = queue.Queue(maxsize=buffer_frames)
        self.stop = threading.Event()
        self.rest = b""
        self.seconds = 0.0  # how much audio we have handed to discord
        threading.Thread(target=self._decode, args=(url, query, vid),
                         daemon=True).start()

    def _decode(self, url, query="", vid=""):
        if av is None:
            print(f"[music] cannot decode audio: {AV_ERROR}")
            self.q.put(None)
            return
        tries = 4
        try:
            for attempt in range(tries):
                if self.stop.is_set():
                    return
                try:
                    self._decode_once(url)
                    return  # finished the track
                except Exception as e:
                    if self.stop.is_set():
                        return
                    last = attempt + 1 == tries
                    print(f"[music] stream broke at {self.seconds:.0f}s "
                          f"({type(e).__name__}: {e}) — "
                          + ("giving up" if last
                             else f"resuming, attempt {attempt + 2}/{tries}"))
                    if last:
                        break
                    time.sleep(1)
                    # the CDN url may have expired; get a fresh one. Re-resolve by
                    # video id when we have it — re-running the SEARCH can hand
                    # back a different song halfway through this one, which it did.
                    if vid or query:
                        try:
                            url = find(watch_url(vid) if vid else query)["url"]
                        except Exception:
                            pass
        finally:
            self.q.put(None)  # sentinel: end of stream

    def _decode_once(self, url):
        """Decode from `self.seconds` onward, so a resume does not restart it."""
        with av.open(url, options=self._HTTP, timeout=20) as container:
            stream = container.streams.audio[0]
            if self.seconds > 0.5:  # picking up after a drop
                container.seek(int(self.seconds * 1_000_000), any_frame=False)
            resampler = av.AudioResampler(format="s16", layout="stereo",
                                          rate=SAMPLE_RATE)
            for frame in container.decode(stream):
                if self.stop.is_set():
                    return
                for out in resampler.resample(frame):
                    data = out.to_ndarray().tobytes()
                    self.seconds += len(data) / (SAMPLE_RATE * 4)
                    while not self.stop.is_set():
                        try:
                            self.q.put(data, timeout=0.5)
                            break
                        except queue.Full:
                            continue

    def read(self) -> bytes:
        """Exactly 20ms of PCM, or b'' to end playback."""
        while len(self.rest) < FRAME:
            try:
                # generous: a mid-song reconnect can take several seconds, and
                # returning b"" here would end the track for good
                chunk = self.q.get(timeout=30)
            except queue.Empty:
                return b""
            if chunk is None:
                out, self.rest = self.rest, b""
                return out.ljust(FRAME, b"\x00") if out else b""
            self.rest += chunk
        out, self.rest = self.rest[:FRAME], self.rest[FRAME:]
        return out

    def is_opus(self) -> bool:
        return False

    def cleanup(self):
        self.stop.set()


def source_for(hit: dict):
    """AudioSource that can heal itself mid-song by re-resolving the same video."""
    return source(hit["url"], hit.get("query", ""), hit.get("id", ""))


def source(url: str, query: str = "", vid: str = ""):
    """Wrap Stream so discord.py accepts it as an AudioSource.

    Stream MUST come first: discord.AudioSource.read() raises
    NotImplementedError, so if it wins the MRO every song dies instantly.
    """
    import discord

    cls = type("MusicSource", (Stream, discord.AudioSource), {})
    assert cls.read is Stream.read, "MRO wrong — AudioSource.read would raise"
    # query and vid MUST be forwarded: they are how _decode re-resolves an expired
    # CDN url after a drop. Dropping query here made that path dead code once.
    return cls(url, query, vid)


if __name__ == "__main__":  # self-check: search + decode, no discord, no playback
    import sys

    # a mood query on purpose: the top YouTube hit for one is a multi-hour mix,
    # and picking a real song out of the results is the thing being checked
    q = " ".join(sys.argv[1:]) or "hype gaming EDM"
    hit = find(q)
    print(f"found: {hit['title']}  ({hit['duration']}s)")
    assert 0 < hit["duration"] <= MAX_TRACK_S, "picked a livestream or a long mix"
    assert find(q)["id"] != hit["id"], "same query twice returned the same video"
    # source(), not Stream() — this is the exact class discord.py drives, and
    # testing the raw class is how a NotImplementedError reached a live call.
    s = source(hit["url"])
    print(f"source class: {type(s).__mro__[:3]}")
    t0 = time.perf_counter()
    frames = [s.read() for _ in range(500)]  # 10s — tracks often fade in
    took = time.perf_counter() - t0
    s.cleanup()
    good = [f for f in frames if len(f) == FRAME]
    audio = np.frombuffer(b"".join(good), dtype=np.int16)
    print(f"decoded {len(good)}/500 frames in {took:.1f}s (10s of audio), "
          f"peak amplitude {int(np.abs(audio).max())}")
    assert len(good) >= 490, "stream did not keep up"
    assert np.abs(audio).max() > 100, "decoded silence"
    print("music ok — searched, decoded, real audio, no ffmpeg")
