"""One undecodable packet must not deafen her.

voice_recv's router does `finally: stop_listening()` around the whole loop, so
a single OpusError ended the session and dropped the voice connection (Krich
hit this live). This drives the patched loop directly.

    py -X utf8 tests\\routerbench.py
"""
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import voice  # noqa: E402  (importing installs the patches)

from discord.ext.voice_recv import router as rt  # noqa: E402

assert getattr(rt.PacketRouter, "_tiwa_resilient", False), "patch not installed"


TOTAL = 6


class Decoder:
    """Third packet is corrupt, like a DAVE-encrypted frame reaching opus."""

    def __init__(self, end):
        self.n = 0
        self.end = end

    def pop_data(self):
        self.n += 1
        if self.n >= TOTAL:
            self.end.set()  # stop the loop from inside: no racing timer thread
        if self.n == 3:
            raise Exception("corrupted stream")  # what opus raises
        return types.SimpleNamespace(source=f"packet{self.n}")


def main():
    written = []
    end = threading.Event()
    decoder = Decoder(end)

    fake = types.SimpleNamespace(
        _end_thread=end,
        _lock=threading.RLock(),
        waiter=types.SimpleNamespace(wait=lambda: None, items=[decoder]),
        sink=types.SimpleNamespace(write=lambda src, data: written.append(src)),
    )

    rt.PacketRouter._do_run(fake)  # returns only if it survived the bad packet

    print(f"packets offered : {decoder.n}")
    print(f"packets written : {len(written)} -> {written}")
    assert len(written) == TOTAL - 1, "router stopped writing after the bad packet"
    assert "packet3" not in written, "corrupt packet should have been skipped"
    assert "packet4" in written, "router did not recover after the bad packet"
    print("\nrouter ok — bad packet skipped, listening continued")


main()
