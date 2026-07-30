"""G9 — music from YouTube, decoded with PyAV. No ffmpeg binary needed.

yt-dlp finds a direct audio URL, av decodes it to PCM, discord plays the PCM.
"""
import os
import queue
import threading
import time

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

_YDL = {
    "format": "bestaudio/best",
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "skip_download": True,
    "default_search": "ytsearch1",
}


def find(query: str) -> dict:
    """Search YouTube, return {title, url, duration} for the top hit."""
    import yt_dlp

    with yt_dlp.YoutubeDL(_YDL) as ydl:
        info = ydl.extract_info(query, download=False)
    if "entries" in info:
        info = info["entries"][0]
    return {
        "title": info.get("title", "?"),
        "url": info["url"],
        "duration": info.get("duration") or 0,
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

    def __init__(self, url: str, query: str = "", buffer_frames: int = 200):  # ~4s
        self.q = queue.Queue(maxsize=buffer_frames)
        self.stop = threading.Event()
        self.rest = b""
        self.seconds = 0.0  # how much audio we have handed to discord
        threading.Thread(target=self._decode, args=(url, query), daemon=True).start()

    def _decode(self, url, query=""):
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
                    if query:  # the CDN url may have expired; get a fresh one
                        try:
                            url = find(query)["url"]
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
    """AudioSource that can heal itself mid-song by re-resolving `query`."""
    return source(hit["url"], hit.get("query", ""))


def source(url: str, query: str = ""):
    """Wrap Stream so discord.py accepts it as an AudioSource.

    Stream MUST come first: discord.AudioSource.read() raises
    NotImplementedError, so if it wins the MRO every song dies instantly.
    """
    import discord

    cls = type("MusicSource", (Stream, discord.AudioSource), {})
    assert cls.read is Stream.read, "MRO wrong — AudioSource.read would raise"
    # query MUST be forwarded: it is how _decode re-resolves an expired CDN url
    # after a drop. Dropping it here made that whole recovery path dead code.
    return cls(url, query)


if __name__ == "__main__":  # self-check: search + decode, no discord, no playback
    import sys

    q = " ".join(sys.argv[1:]) or "lofi hip hop radio"
    hit = find(q)
    print(f"found: {hit['title']}  ({hit['duration']}s)")
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
