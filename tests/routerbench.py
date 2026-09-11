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
    from unittest.mock import patch, Mock
    from discord.ext.voice_recv.opus import PacketDecoder

    buffered = [types.SimpleNamespace(sequence=18013), types.SimpleNamespace(sequence=18014)]
    decoder = object.__new__(PacketDecoder)
    decoder._buffer = Mock()
    decoder._buffer.pop.return_value = None
    decoder._buffer.flush.return_value = buffered
    decoder.router = types.SimpleNamespace(waiter=Mock())
    assert decoder._get_next_packet(0).sequence == 18013
    decoder._flag_ready_state()
    decoder.router.waiter.register.assert_called_once_with(decoder)
    assert decoder._get_next_packet(0).sequence == 18014
    decoder._buffer.flush.assert_called_once()
    assert not decoder._tiwa_flushed
    decoder._buffer.peek.return_value = None
    decoder._flag_ready_state()
    decoder.router.waiter.unregister.assert_called_once_with(decoder)
    print("PASS jitter flush preserves both packets and schedules the second for decoding")
    from discord.ext.voice_recv import reader as vr

    session = types.SimpleNamespace(
        ready=True, decrypt=Mock(side_effect=ValueError("UnencryptedWhenPassthroughDisabled"))
    )
    vc = types.SimpleNamespace(
        _connection=types.SimpleNamespace(dave_session=session),
        _get_id_from_ssrc=lambda _: 7,
        mode="fixture",
        secret_key=b"",
    )
    with (
        patch.object(vr, "PacketRouter"),
        patch.object(vr, "SinkEventRouter"),
        patch.object(vr, "SpeakingTimer"),
        patch.object(vr, "UDPKeepAlive"),
        patch.object(vr, "PacketDecryptor") as decryptor,
    ):
        transport = decryptor.return_value.decrypt_rtp
        transport.return_value = b"ciphertext"
        reader = vr.AudioReader(types.SimpleNamespace(), vc)
        packet = types.SimpleNamespace(ssrc=7)
        # RTP padding is transport metadata and must not reach DAVE.
        packet.padding = True
        transport.return_value = b"ciphertext" + b"\x04" * 4
        session.decrypt.side_effect = None
        session.decrypt.return_value = b"valid opus"
        assert reader.decryptor.decrypt_rtp(packet) == b"valid opus"
        assert session.decrypt.call_args.args[-1] == b"ciphertext"
        session.decrypt.reset_mock()
        transport.return_value = b"ciphertext\x00"
        assert reader.decryptor.decrypt_rtp(packet) is None
        session.decrypt.assert_not_called()
        packet.padding = False
        session.decrypt.side_effect = ValueError("UnencryptedWhenPassthroughDisabled")
        transport.return_value = b"\xf8\xff\xfe"
        assert reader.decryptor.decrypt_rtp(packet) == b"\xf8\xff\xfe"
        session.decrypt.assert_not_called()
        # A near-match must still go through DAVE and be rejected.
        transport.return_value = b"\xf8\xff\xfe\x00"
        assert reader.decryptor.decrypt_rtp(packet) is None
        transport.return_value = b"ciphertext"
        assert reader.decryptor.decrypt_rtp(packet) is None
        session.decrypt.side_effect = None
        session.decrypt.return_value = b"valid opus"
        assert reader.decryptor.decrypt_rtp(packet) == b"valid opus"
    pushed = []
    routing = types.SimpleNamespace(
        _dropped_ssrcs=set(),
        _lock=threading.RLock(),
        get_decoder=lambda _: types.SimpleNamespace(push_packet=pushed.append),
    )
    rt.PacketRouter.feed_rtp(routing, types.SimpleNamespace(ssrc=7, decrypted_data=None))
    assert not pushed, "failed decryption must never reach Opus"
    rt.PacketRouter.feed_rtp(routing, types.SimpleNamespace(ssrc=7, decrypted_data=b"valid opus"))
    assert len(pushed) == 1, "good audio after failure must still reach Opus"
    print("PASS DAVE failure dropped; next successfully decrypted packet accepted")
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
    watchdog()
    print("\nrouter ok — bad packet skipped, listening continued, watchdog quiet")


def watchdog():
    """The 30s restart loop must stay silent when her ears are off.

    With TIWA_LISTEN=0 it used to log "listening had stopped — restarting" every
    tick forever and restart nothing: 36 rows out of 36 ticks on a real session.
    """
    import asyncio
    from unittest.mock import patch
    from tiwa import listening

    import bot

    async def ticks(n):
        bot.client.loop = asyncio.get_running_loop()  # listen() needs a live loop
        for _ in range(n):
            await bot.keep_listening.coro()

    logged = []
    live = {"on": False}
    vc = types.SimpleNamespace(
        is_connected=lambda: True,
        is_listening=lambda: live["on"],
        listen=lambda ears: live.update(on=True),
    )
    bot.memory.log = lambda db, kind, text, ms=0: logged.append(text)
    bot.voice_channel[1] = types.SimpleNamespace(guild=types.SimpleNamespace(voice_client=vc))

    was, voice.LISTEN = voice.LISTEN, False
    try:
        asyncio.run(ticks(3))  # three ticks of a real session, ears off
        assert not logged, f"watchdog logged with listening off: {logged}"

        voice.LISTEN = True  # ears on: restart once, then stay quiet
        with patch.object(
            listening,
            "Ears",
            lambda *args: types.SimpleNamespace(start=lambda loop: None, cleanup=lambda: None),
        ):
            asyncio.run(ticks(3))
        assert logged == ["listening had stopped — restarted"], logged
    finally:
        voice.LISTEN = was
        bot.voice_channel.pop(1, None)
    print("watchdog: silent with ears off, one line per real restart with them on")


main()
