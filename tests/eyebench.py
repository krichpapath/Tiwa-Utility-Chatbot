"""Her eyes: an image reaches her voice, and a blind turn admits it.

Offline by default — the vision model is faked, so this asserts the PLUMBING,
which is where every one of these bugs actually lives: does the description reach
her persona prompt, does a failure become "I can't see it" instead of a made-up
picture, and does an ordinary text turn still cost zero vision calls.

    py -X utf8 tests\\eyebench.py
    py -X utf8 tests\\eyebench.py --live <image url>   # a real look, needs a key
"""
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import eyes, llm, memory, pipeline  # noqa: E402

db = memory.connect(str(Path(tempfile.mkdtemp(prefix="tiwa-eyes-")) / "t.db"))

SEEN = ("Screenshot of Marvel Rivals, scoreboard open, the player is 2/11 and bottom "
        'of the team. Chat box reads "gg ez" from someone on the other side.')

calls = []  # (model, messages) for every llm.chat in the turn


def fake_chat(model=None, messages=None, tools=None, fmt=None, options=None,
              think=False, provider=None):
    calls.append((model, messages))
    if model == eyes.MODEL:
        if FAIL[0]:
            raise RuntimeError("vision provider exploded")
        return {"content": SEEN, "tool_calls": [], "raw": {}}
    if any("inner thoughts" in str(m.get("content", "")) for m in messages or []):
        # deliberately shares no words with SEEN, so "did the picture reach her"
        # can never be answered by the brief instead
        return {"content": "you remember Krich: plays shooters", "tool_calls": [],
                "raw": {}}
    return {"content": "REPLY", "tool_calls": [], "raw": {}}


FAIL = [False]
llm.chat = fake_chat
pipeline.llm.chat = fake_chat
eyes.llm.chat = fake_chat


def persona_prompt():
    """The system block her VOICE actually receives — the only one that matters."""
    # the literal opening bracket: prompts/tiwa.md ALSO talks about inner-state,
    # and matching loosely returns the persona file instead of the turn's state
    for model, msgs in calls:
        for m in msgs or []:
            if str(m.get("content", "")).startswith("[inner-state"):
                return m["content"]
    return ""


def turn(text, images=()):
    calls.clear()
    hist = [{"role": "user", "content": f"Krich: {text}"}]
    return asyncio.run(pipeline.respond(db, hist, "Krich", text, images))


def check(name, cond):
    print(f"| {name} | {'ok' if cond else 'FAIL'} |")
    assert cond, name


print("| check | result |\n|---|---|")

# --- the message shape sent to the vision model -------------------------------
m = eyes._msgs(["http://x/a.png", "http://x/b.png"], "ดูนี่ดิ")
check("what they said rides with the image", m[1]["content"][0]["text"].endswith("ดูนี่ดิ"))
check("caption-less image says so", "no caption" in eyes._msgs(["http://x/a.png"], "")[1]
      ["content"][0]["text"])
check("one image per turn", sum(c["type"] == "image_url" for c in m[1]["content"]) == 1)
check("log shows a filename, not 200 chars of CDN signature",
      eyes._name("https://cdn.discordapp.com/attachments/1/2/meme.png?ex=a&hm=b")
      == "meme.png")

# --- an ordinary turn must not cost a vision call -----------------------------
turn("ว่าไง")
check("no image = no vision call", not any(mo == eyes.MODEL for mo, _ in calls))
check("no image = no blind warning", "cannot see" not in persona_prompt())

# --- the use case: she sees it and reacts -------------------------------------
turn("ดูนี่ดิ", ["https://cdn.discordapp.com/attachments/1/2/shot.png"])
check("vision model was called", any(mo == eyes.MODEL for mo, _ in calls))
check("what she saw reaches her voice", "Marvel Rivals" in persona_prompt())
check("told to react, not narrate", "Do NOT describe it back" in persona_prompt())
check("never claims blindness while seeing",
      "cannot see it" not in persona_prompt())

# Dispatch gets the image too, so an unfamiliar game can be searched.
inner = [msgs for mo, msgs in calls
         if any("minis should handle" in str(x.get("content", "")) for x in msgs or [])]
check("dispatch can see it too", "Marvel Rivals" in str(inner))

# --- caption-less image: the common case --------------------------------------
turn("", ["https://cdn.discordapp.com/attachments/1/2/meme.png"])
check("bare image still gets looked at", any(mo == eyes.MODEL for mo, _ in calls))
check("bare image still reaches her voice", "Marvel Rivals" in persona_prompt())

# --- blind: the vision call fails ---------------------------------------------
FAIL[0] = True
turn("ดูนี่ดิ", ["https://cdn.discordapp.com/attachments/1/2/shot.png"])
check("failure is admitted, not invented", "cannot see it" in persona_prompt())
check("nothing invented about the picture", "Marvel Rivals" not in persona_prompt())
FAIL[0] = False

# --- the cost ceiling must not fall back to the text-only local model ---------
real_spend, llm.DAILY_TOKENS = llm.spend, 10
eyes.llm.spend = lambda add=0: 999999
check("over budget = blind, never a content array at the 8B",
      eyes.look(db, ["http://x/a.png"], "hi") == "")
eyes.llm.spend = real_spend
llm.DAILY_TOKENS = 0

# --- the switch ---------------------------------------------------------------
eyes.ON = False
check("TIWA_VISION=0 stops the call dead", eyes.look(db, ["http://x/a.png"], "hi") == "")
eyes.ON = True

# --- the panel renders these rows for free ------------------------------------
rows = [r[0] for r in db.execute(
    "SELECT text FROM log WHERE kind='tool' ORDER BY rowid").fetchall()]
check("looks are logged as tool rows", any(r.startswith("look(") for r in rows))
check("a look row splits like every other tool row",
      all(") -> " in r for r in rows if r.startswith("look(")))
check("the failure is on the record too", any("failed:" in r for r in rows))

print("\neyes ok — sees, reacts, admits blindness, costs nothing when idle")

# Stable Wikimedia Commons files, one per kind she will actually be sent. Real
# urls, not fixtures in the repo: Discord hands her a link too, so this exercises
# "the provider fetches it" as well as the model.
LIVE = [
    ("meme", "https://upload.wikimedia.org/wikipedia/commons/a/ab/Lolcat_in_folder.jpg",
     "", ("folder", "cat")),
    ("screenshot",
     "https://upload.wikimedia.org/wikipedia/commons/c/cf/GNOME_Anwendungen.png",
     "นี่มันโปรแกรมอะไรวะ", ("menu", "linux", "gnome", "anwendungen")),
    ("thai text",
     "https://upload.wikimedia.org/wikipedia/commons/thumb/b/b7/"
     "A_5_language_sign_in_Singapore%2C_with_Thai_%282025%29.jpg/"
     "960px-A_5_language_sign_in_Singapore%2C_with_Thai_%282025%29.jpg",
     "ป้ายนี้เขียนว่าไง", ("sign", "safety", "คนงาน")),
    ("photo", "https://upload.wikimedia.org/wikipedia/commons/4/4d/Wikicat-keyboard.jpeg",
     "ดูนี่ดิ", ("keyboard", "cat", "kitten")),
]

if "--live" in sys.argv:  # real model, real images, ~$0.0006 for the set
    import importlib

    importlib.reload(llm)  # undo the fake chat
    importlib.reload(eyes)
    live_db = memory.connect(":memory:")
    urls = sys.argv[sys.argv.index("--live") + 1:]
    cases = [("custom", u, "ดูรูปนี้ดิ", ()) for u in urls] or LIVE
    for kind, url, said, want in cases:
        out = eyes.look(live_db, [url], said)
        print(f"\n--- {kind}: {eyes._name(url)[:44]}  said={said!r}\n{out or '(BLIND)'}")
        assert out, f"{kind}: live look returned nothing"
        assert len(out) <= eyes.MAX_CHARS, f"{kind}: description was not capped"
        assert not want or any(w in out.lower() for w in want), \
            f"{kind}: saw none of {want} — it looked at the wrong thing"
    print(f"\nlive ok — {len(cases)} images through {eyes.MODEL}")
