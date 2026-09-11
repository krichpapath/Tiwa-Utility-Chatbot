"""S7 — the whole thesis in one number. Offline.

The claim this design was built on: adding a capability must not cost Main Tiwa
anything. `docs/concepts/tools.md` records the opposite happening — `now_playing`
was DELETED because every tool added competes with `play_music` for attention,
and Home Assistant is next on the roadmap with five or six more.

So: add a mini, measure what grew. If Main's prompt moved, the containment
leaked and this design did not do the thing it exists for.

    py -X utf8 tests\\growthbench.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, minis, pipeline, tools  # noqa: E402

db = memory.connect(":memory:")
TEXT = "Steven is coming over tonight"


def main_prompt() -> str:
    """Every word Main Tiwa reads on an ordinary turn: her persona plus the state
    block. This is the thing that must not grow."""
    tools.new_turn()
    return pipeline.PERSONA + pipeline._state(db, "Krich", TEXT)


def dispatch_prompt() -> str:
    return minis._SYSTEM.format(
        minis="\n".join(f"- {n}: {m['description']}" for n, m in sorted(minis.MINIS.items()))
    )


def before_and_after():
    main_before, disp_before = main_prompt(), dispatch_prompt()
    n_before = len(minis.MINIS)

    @minis.mini(
        "sets a timer and tells her when it goes off. give it a duration.", ("seconds", "label")
    )
    def timer(db, task):
        return {"seconds": 60, "label": task}

    main_after, disp_after = main_prompt(), dispatch_prompt()

    print(f"{'':22} | {'before':>8} | {'after':>8} | delta")
    print(f"{'-' * 22}-+-{'-' * 8}-+-{'-' * 8}-+------")
    print(f"{'minis registered':22} | {n_before:8} | {len(minis.MINIS):8} | +1")
    print(
        f"{'MAIN TIWA prompt':22} | {len(main_before):8} | {len(main_after):8} | "
        f"{len(main_after) - len(main_before):+}"
    )
    print(
        f"{'dispatch prompt':22} | {len(disp_before):8} | {len(disp_after):8} | "
        f"{len(disp_after) - len(disp_before):+}"
    )

    assert main_after == main_before, (
        "MAIN TIWA'S PROMPT GREW when a mini was added — the containment leaked "
        "and this design did not do the thing it was built for"
    )
    grew = len(disp_after) - len(disp_before)
    assert disp_after.count("\n- ") == disp_before.count("\n- ") + 1
    assert grew < 200, f"a mini cost {grew} chars of dispatch prompt, not one line"
    print(f"\ngrowth ok   — a new capability cost Main Tiwa 0 chars and dispatch {grew}")


def main_has_no_tool_list():
    """She is handed nothing to choose between. The tool list she used to read
    every turn is what the whole tool-count literature is about.

    Structural, not stylistic: on this branch there is no registry to show her,
    so the check is that nothing grew one back. `tools.TOOLS` existing again
    would mean somebody re-added the pass this design exists to remove.
    """
    assert not hasattr(tools, "TOOLS"), "a tool registry came back"
    src = (Path(__file__).parents[1] / "tiwa" / "pipeline.py").read_text(encoding="utf-8")
    for dead in ("_tool_chat", "_inner_brief", "def _arg_name"):
        assert f"\n{dead}" not in src and f"def {dead}" not in src, (
            f"the tool pass came back: {dead}"
        )
    print("main ok     — she is handed no tools to choose between, and no pass to run")


# Measured on `main`'s tiwa/tools.py, where the four @tool descriptions for
# play/stop/queue/skip were the biggest thing Main Tiwa read every turn. Frozen
# here as a number because the code it was measured from is gone from this
# branch: `git show main:tiwa/tools.py` is the receipt.
OLD_MUSIC_DESC_CHARS = 1643


def one_line_buys_a_whole_subsystem():
    """DJ owns the four music tools plus the terms call. Main sees one line."""
    dj_line = len(f"- dj: {minis.MINIS['dj']['description']}")
    print(f"\n4 music tool descriptions Main used to read : {OLD_MUSIC_DESC_CHARS:>5} chars")
    print(f"the one dj line she reads instead           : {dj_line:>5} chars")
    assert dj_line < OLD_MUSIC_DESC_CHARS / 4, (
        "the mini line is not cheaper than the tools it hides"
    )
    print(
        f"contain ok  — {OLD_MUSIC_DESC_CHARS // dj_line}x less, and it stays flat"
        " as DJ gains tools"
    )


def nothing_is_orphaned():
    """Every actuator in tools.py must have a named caller.

    This check exists because two did not. There is no tool pass to pick them up
    any more, so a function nobody calls is dead — and dead SILENTLY: every bench
    passed while "come join the vc" quietly did nothing. It caught exactly that,
    and it still has to, now that the registry it used to read is gone.

    A new actuator fails here until somebody says who calls it.
    """
    OWNER = {
        "join_voice": "code: pipeline._asked_voice (off under TIWA_VOICE=dj)",
        "leave_voice": "code: pipeline._asked_voice (off under TIWA_VOICE=dj)",
        "play_music": "mini: dj",
        "stop_music": "mini: dj",
        "queue_music": "mini: dj",
        "skip_music": "mini: dj",
        "web_search": "mini: search",
        "calendar_write": "mini: calendar",
    }
    # the Turn machinery is not an actuator — it is what they all write to
    PLUMBING = {"current", "new_turn"}
    actual = {
        n
        for n, f in vars(tools).items()
        if inspect.isfunction(f) and not n.startswith("_") and f.__module__ == "tiwa.tools"
    } - PLUMBING
    missing = sorted(actual - set(OWNER))
    stale = sorted(set(OWNER) - actual)
    assert not missing, (
        f"{missing} has no named caller. There is no tool pass to find it, so a "
        "function nobody calls is dead — and dead silently. Name who calls it, "
        "or delete it."
    )
    assert not stale, f"{stale} is claimed by an owner but no longer exists"
    for name, who in OWNER.items():
        if who.startswith("mini: "):
            assert who[6:] in minis.MINIS, f"{name} claims {who}, which is not registered"
    print(f"owners ok   — all {len(actual)} actuators have a named caller")


def voice_is_dj_only():
    """TIWA_VOICE=dj: the channel is a speaker for music, not a conversation.

    Both halves matter. Conversational join/leave must be inert AND honest — a
    tool that silently sets nothing is how she claims she joined and did not. And
    the classifier must still be correct underneath, so `full` is one env var away.
    """
    from tiwa import tools as t, voice

    assert voice.DJ_ONLY and not voice.LISTEN, "ears are on"
    for text in ("come join the vc", "เข้ามาหน่อย", "get out", "ออกไป"):
        assert pipeline._asked_voice(text) == "dj-only", f"{text!r} not flagged dj-only"

    t.new_turn()
    for fn in (t.join_voice, t.leave_voice):
        fn(db, "")
    assert t.PENDING_JOIN is False and t.PENDING_LEAVE is False, "a flag was set anyway"

    # ...and she is TOLD, which is the half that is easy to lose. Nothing reads a
    # tool's return string any more, so the honest sentence has to arrive in the
    # state block or it does not arrive at all.
    for text in ("come join the vc", "เข้ามาหน่อย", "get out", "ออกไป"):
        assert pipeline._asked_voice(text) == "dj-only", text
    state = pipeline._state(db, "Krich", "come join the vc", voice_asked=True)
    assert "only" in state and "speaker for music" in state, state
    assert "Do NOT claim you joined" in state, "she can still bluff about joining"

    # ...and the classifier itself is still right, so `full` is one env var away
    was, t.VOICE_DJ_ONLY = t.VOICE_DJ_ONLY, False
    try:
        live = [
            ("come join the vc", "join"),
            ("เข้ามาหน่อย", "join"),
            ("get out", "leave"),
            ("ออกไป", "leave"),
            ("join us later tonight", ""),
            ("ออกไปตอนดึกนะ", ""),
            ("หยุดเพลง", ""),
            ("ปิดเพลง", ""),
            ("พอแล้ว", ""),
            ("how are you", ""),
        ]
        for text, want in live:
            got = pipeline._asked_voice(text)
            assert got == want, f"under full: {text!r} -> {got!r}, wanted {want!r}"
    finally:
        t.VOICE_DJ_ONLY = was
    print("voice ok    — dj-only: inert and honest; the classifier still correct")


def music_still_gets_a_channel():
    """The one thing voice is still FOR. Asking for a song brings her in."""
    from tiwa.discord_player import Player
    import inspect

    body = inspect.getsource(Player._flush_locked)
    assert "await voice.join(author)" in body, (
        "music no longer brings her into the channel — voice is now FOR nothing"
    )
    from tiwa import voice
    import inspect

    assert "DJ_ONLY" not in inspect.getsource(voice.join), (
        "voice.join() got gated — music cannot reach a channel"
    )
    print("dj ok       — a song still pulls her into the channel; join() ungated")


def acts_are_declared_not_guessed():
    """Every registered mini is either an action or speech. A new one that is
    neither would silently become speech and never reach bot.py's flush."""
    for name in minis.MINIS:
        assert name in pipeline.ACTS or name not in ("dj", "calendar"), name
    assert pipeline.ACTS == {"dj", "calendar"}, pipeline.ACTS
    print("acts ok     — the two minis bot.py has to flush for are named explicitly")


before_and_after()
main_has_no_tool_list()
one_line_buys_a_whole_subsystem()
nothing_is_orphaned()
voice_is_dj_only()
music_still_gets_a_channel()
acts_are_declared_not_guessed()
print("\ngrowth ok — a mini costs Main Tiwa nothing. That was the whole bet.")
