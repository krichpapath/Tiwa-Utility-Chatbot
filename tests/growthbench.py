"""S7 — the whole thesis in one number. Offline.

The claim this design was built on: adding a capability must not cost Main Tiwa
anything. `docs/concepts/tools.md` records the opposite happening — `now_playing`
was DELETED because every tool added competes with `play_music` for attention,
and Home Assistant is next on the roadmap with five or six more.

So: add a mini, measure what grew. If Main's prompt moved, the containment
leaked and this design did not do the thing it exists for.

    py -X utf8 tests\\growthbench.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, minis, pipeline, tools, turn  # noqa: E402

db = memory.connect(":memory:")
TEXT = "Steven is coming over tonight"


def main_prompt() -> str:
    """Every word Main Tiwa reads on an ordinary turn: her persona plus the state
    block. This is the thing that must not grow."""
    tools.new_turn()
    return pipeline.PERSONA + pipeline._state(db, "Krich", TEXT)


def dispatch_prompt() -> str:
    return minis._SYSTEM.format(
        minis="\n".join(f"- {n}: {m['description']}"
                        for n, m in sorted(minis.MINIS.items())))


def before_and_after():
    main_before, disp_before = main_prompt(), dispatch_prompt()
    n_before = len(minis.MINIS)

    @minis.mini("sets a timer and tells her when it goes off. give it a duration.",
                ("seconds", "label"))
    def timer(db, task):
        return {"seconds": 60, "label": task}

    main_after, disp_after = main_prompt(), dispatch_prompt()

    print(f"{'':22} | {'before':>8} | {'after':>8} | delta")
    print(f"{'-'*22}-+-{'-'*8}-+-{'-'*8}-+------")
    print(f"{'minis registered':22} | {n_before:8} | {len(minis.MINIS):8} | +1")
    print(f"{'MAIN TIWA prompt':22} | {len(main_before):8} | {len(main_after):8} | "
          f"{len(main_after) - len(main_before):+}")
    print(f"{'dispatch prompt':22} | {len(disp_before):8} | {len(disp_after):8} | "
          f"{len(disp_after) - len(disp_before):+}")

    assert main_after == main_before, (
        "MAIN TIWA'S PROMPT GREW when a mini was added — the containment leaked "
        "and this design did not do the thing it was built for")
    grew = len(disp_after) - len(disp_before)
    assert disp_after.count("\n- ") == disp_before.count("\n- ") + 1
    assert grew < 200, f"a mini cost {grew} chars of dispatch prompt, not one line"
    print(f"\ngrowth ok   — a new capability cost Main Tiwa 0 chars and dispatch {grew}")


def main_has_no_tool_list():
    """The concurrent path hands her nothing to choose between. The tool list she
    used to read every turn is what the whole tool-count literature is about."""
    src = (Path(__file__).parents[1] / "tiwa" / "turn.py").read_text(encoding="utf-8")
    assert "tools.TOOLS" not in src, "the concurrent turn still shows her a tool list"
    assert "_tool_chat" not in src and "_inner_brief" not in src, \
        "the concurrent turn still runs the tool pass"
    print("main ok     — on this path she is handed no tools to choose between")


def one_line_buys_a_whole_subsystem():
    """DJ owns the four music tools plus recall and search. Main sees one line."""
    dj_line = len(f"- dj: {minis.MINIS['dj']['description']}")
    music_tools = [n for n in tools.TOOLS if n.endswith("_music")]
    old = sum(len(tools.TOOLS[n]["schema"]["function"]["description"])
              for n in music_tools)
    print(f"\n{len(music_tools)} music tool descriptions Main used to read : {old:>5} chars")
    print(f"the one dj line she reads instead            : {dj_line:>5} chars")
    assert dj_line < old / 4, f"the mini line is not cheaper than the tools it hides"
    print(f"contain ok  — {old // dj_line}x less, and it stays flat as DJ gains tools")


def nothing_is_orphaned():
    """Every tool must have an owner on the CONCURRENT path too.

    This check exists because two did not. The concurrent turn has no tool pass,
    and no mini owns voice, so nothing could set PENDING_JOIN or PENDING_LEAVE —
    "come join the vc" silently did nothing. Silently is the word: the serial
    path still worked, every bench passed, and only reading the registry found it.

    A new tool now fails here until somebody says who calls it.
    """
    OWNER = {
        # superseded by code — the tool still exists for the serial path
        "recall": "code: memory.mentioned() + memory.turn_context()",
        "calendar_read": "code: gcal.upcoming(), read by the calendar mini",
        "join_voice": "off: TIWA_VOICE=dj (classifier ready for full)",
        "leave_voice": "off: TIWA_VOICE=dj (classifier ready for full)",
        # owned by a mini
        "play_music": "mini: dj", "stop_music": "mini: dj",
        "queue_music": "mini: dj", "skip_music": "mini: dj",
        "web_search": "mini: search",
        "calendar_write": "mini: calendar",
    }
    missing = sorted(set(tools.TOOLS) - set(OWNER))
    stale = sorted(set(OWNER) - set(tools.TOOLS))
    assert not missing, (
        f"{missing} has no owner on the concurrent path. There is no tool pass "
        "there, so a tool nobody calls is dead — and dead silently. Name who "
        "calls it, or say why it is superseded.")
    assert not stale, f"{stale} is claimed by an owner but no longer exists"
    for name, who in OWNER.items():
        if who.startswith("mini: "):
            assert who[6:] in minis.MINIS, f"{name} claims {who}, which is not registered"
    print(f"owners ok   — all {len(tools.TOOLS)} tools have a named owner without"
          " the tool pass")


def voice_is_dj_only():
    """TIWA_VOICE=dj: the channel is a speaker for music, not a conversation.

    Both halves matter. Conversational join/leave must be inert AND honest — a
    tool that silently sets nothing is how she claims she joined and did not. And
    the classifier must still be correct underneath, so `full` is one env var away.
    """
    from tiwa import tools as t, voice

    assert voice.DJ_ONLY and not voice.LISTEN, "ears are on"
    for text in ("come join the vc", "เข้ามาหน่อย", "get out", "ออกไป"):
        assert pipeline._asked_voice(text) == "", f"{text!r} still routed to voice"

    t.new_turn()
    for fn in (t.join_voice, t.leave_voice):
        note = fn(db, "")
        assert "only for playing music" in note, note
        assert "do not claim" in note, "she can still bluff about joining"
    assert t.PENDING_JOIN is False and t.PENDING_LEAVE is False, "a flag was set anyway"

    # ...and the classifier itself is still right, so `full` is one env var away
    was, t.VOICE_DJ_ONLY = t.VOICE_DJ_ONLY, False
    try:
        live = [("come join the vc", "join"), ("เข้ามาหน่อย", "join"),
                ("get out", "leave"), ("ออกไป", "leave"),
                ("join us later tonight", ""), ("ออกไปตอนดึกนะ", ""),
                ("หยุดเพลง", ""), ("ปิดเพลง", ""), ("พอแล้ว", ""),
                ("how are you", "")]
        for text, want in live:
            got = pipeline._asked_voice(text)
            assert got == want, f"under full: {text!r} -> {got!r}, wanted {want!r}"
    finally:
        t.VOICE_DJ_ONLY = was
    print("voice ok    — dj-only: inert and honest; the classifier still correct")


def music_still_gets_a_channel():
    """The one thing voice is still FOR. Asking for a song brings her in."""
    src = (Path(__file__).parents[1] / "bot.py").read_text(encoding="utf-8")
    i = src.index("async def _flush_music")
    body = src[i:src.index("async def _hang_up")]
    assert "await voice.join(author)" in body, \
        "music no longer brings her into the channel — voice is now FOR nothing"
    from tiwa import voice
    import inspect
    assert "DJ_ONLY" not in inspect.getsource(voice.join), \
        "voice.join() got gated — music cannot reach a channel"
    print("dj ok       — a song still pulls her into the channel; join() ungated")


def acts_are_declared_not_guessed():
    """Every registered mini is either an action or speech. A new one that is
    neither would silently become speech and never reach bot.py's flush."""
    for name in minis.MINIS:
        assert name in turn.ACTS or name not in ("dj", "calendar"), name
    assert turn.ACTS == {"dj", "calendar"}, turn.ACTS
    print("acts ok     — the two minis bot.py has to flush for are named explicitly")


before_and_after()
main_has_no_tool_list()
one_line_buys_a_whole_subsystem()
nothing_is_orphaned()
voice_is_dj_only()
music_still_gets_a_channel()
acts_are_declared_not_guessed()
print("\ngrowth ok — a mini costs Main Tiwa nothing. That was the whole bet.")
