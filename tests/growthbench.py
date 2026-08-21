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
acts_are_declared_not_guessed()
print("\ngrowth ok — a mini costs Main Tiwa nothing. That was the whole bet.")
