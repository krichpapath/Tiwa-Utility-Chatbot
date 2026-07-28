"""Chat with Tiwa in the terminal — full pipeline (inner pass + memory + extraction).

Uses the same brain and database as the Discord bot.
Usage: py -X utf8 chat.py [yourname]
"""
import asyncio
import sys

from tiwa import gcal, memory, pipeline, tools

name = sys.argv[1] if len(sys.argv) > 1 else "user"
db = memory.connect()
hist = []


async def main():
    while True:
        try:
            text = input(f"{name}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        hist.append({"role": "user", "content": f"{name}: {text}"})
        reply = await pipeline.respond(db, hist, name, text)
        hist.append({"role": "assistant", "content": reply})
        print(f"tiwa> {reply}\n")
        # sync here — terminal, latency fine; prior lines let "he/she" resolve
        memory.extract(db, name, text, reply, memory.history_context(hist))
        while tools.PENDING_CALENDAR:  # terminal version of the Discord ✅ gate
            req = tools.PENDING_CALENDAR.pop(0)
            if input(f"calendar change: {req} — confirm? [y/N] ").lower() == "y":
                print(gcal.apply_change(req))


asyncio.run(main())
