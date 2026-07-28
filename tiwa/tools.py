"""Tool registry for the inner pass. Every tool: fn(db, arg: str) -> str.

Every tool takes ONE string argument named "name" — deliberate: the 8B mangles
nested/renamed structured args (see pipeline._arg_name), so structure is minted
later by schema-constrained calls, never by the tool-calling model.
"""
from . import memory
from .memory import TIWA

TOOLS = {}  # name -> {"schema": ollama tool spec, "fn": callable(db, arg) -> str}


def tool(description: str, arg_desc: str):
    def reg(fn):
        TOOLS[fn.__name__] = {
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": arg_desc}
                        },
                        "required": ["name"],
                    },
                },
            },
            "fn": fn,
        }
        return fn

    return reg


@tool(
    f"Check {TIWA}'s memory for a person, character, or topic. "
    "Returns known facts or 'no memory'. Use for every name/topic that matters.",
    "who or what to recall",
)
def recall(db, arg: str) -> str:
    return memory.lookup(db, arg)


@tool(
    "Web search. Use ONLY for current events or facts outside memory that the "
    "message directly asks about. Never for people you should just recall.",
    "search query",
)
def web_search(db, arg: str) -> str:
    from ddgs import DDGS  # lazy: keeps dep optional for tests

    try:
        hits = DDGS().text(arg, max_results=3)
    except Exception as e:  # network flake -> brief says so instead of crashing the turn
        return f"search failed: {e}"
    return "\n".join(f"{h['title']}: {h['body']}" for h in hits) or "no results"


@tool(
    "Read Krich's Google Calendar (next 7 days). Use only when the message "
    "involves his schedule or plans.",
    "ignored",
)
def calendar_read(db, arg: str) -> str:
    from . import gcal

    return gcal.upcoming()


PENDING_CALENDAR = []  # plain-language change requests awaiting Krich's ✅ in Discord


@tool(
    "Request a change to Krich's calendar (add or cancel an event). Pass ONE "
    "plain sentence, e.g. 'add dentist tomorrow 15:00'. Queued until Krich confirms.",
    "the change in one sentence",
)
def calendar_write(db, arg: str) -> str:
    PENDING_CALENDAR.append(arg)
    return "queued — Krich must confirm with ✅ before it happens"


if __name__ == "__main__":  # runnable check: registry shape + dispatch
    db = memory.connect(":memory:")
    memory.remember(db, "Krich", "cousin", "Steven")
    for t in TOOLS.values():
        assert "name" in t["schema"]["function"]["parameters"]["properties"]  # _arg_name contract
    assert "Steven" in TOOLS["recall"]["fn"](db, "Steven")
    assert "confirm" in TOOLS["calendar_write"]["fn"](db, "add x tomorrow")
    assert PENDING_CALENDAR == ["add x tomorrow"]
    print("tools ok:", ", ".join(TOOLS))
