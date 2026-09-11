"""G7 — she answers ONLY when a line opens with her name.

Every Thai spelling here is what Whisper actually produces, not what a human
would type. Whisper never writes "ทิวา". A greeting in front is optional
because Whisper drops it constantly.

    py -X utf8 tests\\wakebench.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from tiwa import voice  # noqa: E402

# transcript, should wake, what she should end up hearing (None = don't care)
CASES = [
    # --- "hey tiwa ..." in English, spelled every way it comes out ---
    ("hey tiwa what time is it", True, "what time is it"),
    ("Hey Tiwa, play something", True, "play something"),
    ("hey teewa play some lofi", True, "play some lofi"),
    ("hey tewa stop the music", True, "stop the music"),
    ("hay tiva what's up", True, "what's up"),
    ("hey tiwah play something", True, None),
    ("hi tiwa are you there", True, "are you there"),
    ("yo tiwa come here", True, "come here"),
    ("ok tiwa stop", True, "stop"),
    # --- no greeting, name still first ---
    ("tiwa what time is it", True, "what time is it"),
    ("teewah play music", True, None),
    ("ทิวา อยู่ไหม", True, "อยู่ไหม"),
    ("ที่ว่าเปิดเพลงหน้อย", True, "เปิดเพลงหน้อย"),
    ("ที่ว่า เปิดเพลงหน่อย", True, "เปิดเพลงหน่อย"),
    ("ทีวา เปิดเพลง", True, None),
    ("ที่วา อยู่ไหม", True, "อยู่ไหม"),
    # --- Thai greeting in front ---
    ("เฮ้ ทิวา เปิดเพลงหน่อย", True, "เปิดเพลงหน่อย"),
    ("เฮ้ย ที่ว่า อยู่ไหม", True, "อยู่ไหม"),
    ("เฮ้ทิวาเปิดเพลง", True, None),
    # --- name NOT at the start: talking about her, not to her ---
    ("I told tiwa about it yesterday", False, None),
    ("ask tiwa when she gets back", False, None),
    ("เมื่อวานคุยกับทิวาแล้ว", False, None),
    ("do you think tiwa would like this", False, None),
    # --- no name at all ---
    ("hey what time is it", False, None),
    ("what time is it", False, None),
    ("ที่นี่กินอร่อยดีคิดไม่ออกเลย", False, None),
    ("ที่ไหนก็ได้", False, None),
    ("ที่บ้านมีคนเยอะ", False, None),
    ("ที่จริงแล้วไม่ใช่", False, None),
    ("the water is cold", False, None),
    ("she said the tea was good", False, None),
    ("I was watching a movie yesterday", False, None),
]

print("| transcript | wakes? | want | she hears |")
print("|---|---|---|---|")
wrong = []
for text, want, expect in CASES:
    got = voice.wake(text)
    woke = got is not None
    ok = woke == want and (not expect or not woke or got == expect)
    if not ok:
        wrong.append((text, got, want, expect))
    print(
        f"| `{text}` | {'yes' if woke else 'no'} | {'yes' if want else 'no'} "
        f"| {got if woke else '—'} |{'' if ok else '  **WRONG**'}"
    )

if wrong:
    print("\nfailures:")
    for text, got, want, expect in wrong:
        print(f"  {text!r} -> {got!r} (wanted {'wake: ' + repr(expect) if want else 'no wake'})")
assert not wrong, f"{len(wrong)} of {len(CASES)} wrong"
print(f"\nall {len(CASES)} cases correct")
