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
        async def late(line):
            hist.append({"role": "assistant", "content": line})
            print(f"tiwa> {line}\n")
        try:
            reply = await pipeline.respond(db, hist, name, text, on_late=late)
        except Exception as error:
            print(f"tiwa> brain call failed: {type(error).__name__}; retry when provider is available\n")
            continue
        hist.append({"role": "assistant", "content": reply})
        print(f"tiwa> {reply}\n")
        # sync here — terminal, latency fine; prior lines let "he/she" resolve
        memory.extract(db, name, text, reply, memory.history_context(hist))
        while tools.PENDING_CALENDAR:  # terminal version of the Discord ✅ gate
            req = tools.PENDING_CALENDAR.pop(0)
            try:
                plan = await asyncio.to_thread(gcal.prepare_change, req)
                if input(f"calendar change: {gcal.describe_change(plan)} — confirm? [y/N] ").lower() == "y":
                    print(await asyncio.to_thread(gcal.apply_change, plan))
            except (EOFError, KeyboardInterrupt):
                break
            except Exception as error:
                print(f"calendar proposal failed: {type(error).__name__}")
        pending = pipeline._pending.get(name)
        if pending:
            await asyncio.gather(pending, return_exceptions=True)


asyncio.run(main())
