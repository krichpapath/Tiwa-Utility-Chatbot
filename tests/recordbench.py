"""The fine-tune export: only her voice gets in. Offline.

The guarantee is that multi-agent traces cannot pollute the persona training set.
That has to hold by construction, not by remembering to filter — so this drives a
log containing one row of every pass that exists and checks exactly one survives.

    py -X utf8 tests\\recordbench.py
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import memory, minis, pipeline, record  # noqa: E402

db = memory.connect(":memory:")


def flatten(messages) -> str:
    """Exactly what llm.log_llm writes into the request column."""
    return "\n\n".join(f"[{m['role']}]\n{m['content']}" for m in messages)


def log(messages, response):
    db.execute(
        "INSERT INTO llm_log(ts, provider, model, ms, tokens, request, response) "
        "VALUES(?,?,?,?,?,?,?)",
        (time.time(), "openrouter", "x", 1, 1, flatten(messages), response),
    )
    db.commit()


HER = [
    {"role": "system", "content": pipeline.PERSONA},
    {"role": "system", "content": "[inner-state — background, do not recite]\nrules"},
    {"role": "user", "content": "Krich: hey"},
]


def every_pass_goes_in_one_comes_out():
    log(HER, "bored out of my skull")  # keep
    # The tool pass is gone from this branch, but its rows are not: the live
    # database holds hundreds of them from before the swap, and every one is a
    # brief written TO her, not BY her. Frozen as a literal so the filter is
    # still tested against what is actually on disk.
    log(
        [
            {
                "role": "system",
                "content": "You are Tiwa's inner thoughts, run before she replies. "
                "You are NOT the reply.",
            },
            {"role": "user", "content": "Krich: hey"},
        ],
        "- you remember Krich",
    )
    log(
        [
            {"role": "system", "content": minis._SYSTEM.format(minis="- dj: x")},
            {"role": "user", "content": "Krich: play something"},
        ],
        '{"dispatch":[]}',
    )
    log(
        [{"role": "system", "content": minis._DJ_SYSTEM}, {"role": "user", "content": "lofi"}],
        '{"action":"play"}',
    )
    log(
        [{"role": "system", "content": minis._CAL_SYSTEM}, {"role": "user", "content": "tuesday"}],
        '{"action":"none"}',
    )
    log(
        [
            {"role": "system", "content": minis._SEARCH_SYSTEM},
            {"role": "user", "content": "who won"},
        ],
        "ผลบอลเมื่อคืน",
    )
    log(
        [
            {"role": "system", "content": pipeline._IDLE_SYSTEM},
            {"role": "user", "content": "time: 3am"},
        ],
        "NOTHING",
    )

    out = record.rows(db)
    print(f"{'logged rows':28} | {db.execute('select count(*) from llm_log').fetchone()[0]}")
    print(f"{'exported':28} | {len(out)}")
    assert len(out) == 1, f"a non-persona pass got into the training set: {len(out)}"
    assert out[0]["messages"][-1] == {"role": "assistant", "content": "bored out of my skull"}
    print("filter ok   — 7 passes in, 1 out, and it is hers")


def a_follow_up_is_not_a_turn():
    """_voice() speaks through say(), so it IS a persona row by the rule above.
    But its user turn is the placeholder '(earlier message)' — training on it
    teaches her to answer a message that is not there."""
    log(
        [
            {"role": "system", "content": pipeline.PERSONA},
            {
                "role": "system",
                "content": "[inner-state]\nYou just this second found "
                "out what you went to look up.\nsearch: x",
            },
            {"role": "user", "content": "Krich: (earlier message)"},
        ],
        "oh — it's 3-1",
    )
    assert len(record.rows(db)) == 1, "the late follow-up got in"
    print("followup ok — her late line is real speech but not a real turn")


def empty_and_unparsable_are_dropped():
    log(HER, "")  # she said nothing
    log(HER, "   \n ")  # ...and whitespace is nothing
    db.execute(
        "INSERT INTO llm_log(ts, provider, model, ms, tokens, request, "
        "response) VALUES(?,?,?,?,?,?,?)",
        (time.time(), "x", "x", 1, 1, "not flattened at all", "hi"),
    )
    db.commit()
    assert len(record.rows(db)) == 1, "an empty or unparsable row got in"
    print("drop ok     — empty replies and unparsable rows never reach the set")


def tool_calls_are_stripped():
    """log_llm appends '[tool_calls] ...' to the response. That is not her voice."""
    log(HER, "putting it on\n[tool_calls] [{'name': 'play_music'}]")
    row = record.rows(db)[-1]
    assert row["messages"][-1]["content"] == "putting it on", row["messages"][-1]
    print("clean ok    — the tool_calls tail never becomes something she said")


def shape_is_chat_completions():
    out = record.rows(db)
    for r in out:
        roles = [m["role"] for m in r["messages"]]
        assert roles[0] == "system", roles
        assert roles[-1] == "assistant", roles
        assert set(roles) <= {"system", "user", "assistant", "tool"}, roles
        json.dumps(r, ensure_ascii=False)  # must serialise, Thai included
    print(f"shape ok    — {len(out)} rows, system first, assistant last, JSON-safe")


def drop_state_is_available():
    kept = record.rows(db, keep_state=True)[0]["messages"]
    dropped = record.rows(db, keep_state=False)[0]["messages"]
    assert sum(m["role"] == "system" for m in kept) == 2
    assert sum(m["role"] == "system" for m in dropped) == 1
    # parsed rows are newline-stripped by _messages(); the filter compares .strip()
    assert dropped[0]["content"].strip() == pipeline.PERSONA.strip()
    assert not any("[inner-state" in m["content"] for m in dropped)
    print("state ok    — both exports available; next.md wants the crutch-free one")


every_pass_goes_in_one_comes_out()
a_follow_up_is_not_a_turn()
empty_and_unparsable_are_dropped()
tool_calls_are_stripped()
shape_is_chat_completions()
drop_state_is_available()
print("\nrecord ok — no mini trace can reach the persona set; the filter is exact")
