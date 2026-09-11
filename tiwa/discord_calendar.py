"""Calendar proposals and conversational approval, scoped to Discord requester IDs.

Google parsing/API calls live in gcal.py. This controller owns pending approvals.
"""

import asyncio
import re
import time
from . import gcal, memory, tools


class Calendar:
    def __init__(self, db, history, owner_id=None):
        self.db = db
        self.history = history
        self.owner_id = owner_id
        self.pending = {}
        self.requesters = {}

    async def propose(self, channel, user_id=None):
        """Parse queued requests into concrete, requester-owned proposals."""
        while tools.PENDING_CALENDAR:
            text = tools.PENDING_CALENDAR.pop(0)
            if not self.owner_id:
                await channel.send(
                    "calendar writes unavailable: configure TIWA_OWNER_ID or reconnect the bot"
                )
                continue
            try:
                plan = await asyncio.to_thread(gcal.prepare_change, text)
            except Exception as e:
                await channel.send(
                    f"calendar proposal failed: {type(e).__name__}; provide title, date and time"
                )
                continue
            now = time.monotonic()
            for mid, (_, _, expires) in list(self.pending.items()):
                if expires <= now:
                    self.pending.pop(mid, None)
            duplicate = next(
                (
                    mid
                    for mid, (old, cid, _) in self.pending.items()
                    if cid == channel.id
                    and old == plan
                    and self.requesters.get(mid, self.owner_id) == (user_id or self.owner_id)
                ),
                None,
            )
            if duplicate is not None:
                await channel.send("นัดนี้รอยืนยันอยู่แล้ว — ตอบ ยืนยัน ได้เลย")
                if user_id is not None:
                    from tiwa.listening import FOLLOWUPS

                    FOLLOWUPS[user_id] = time.monotonic() + 60
                continue
            m = await channel.send(
                f"📅 **{gcal.describe_change(plan)}**\nให้บันทึกตามนี้เลยไหม? ตอบได้ตามปกติ (รอฟังคำตอบ 1 นาที)"
            )
            # One latest proposal per requester in this channel.
            for mid, (_, cid, _) in list(self.pending.items()):
                if cid == channel.id and self.requesters.get(mid, self.owner_id) == (
                    user_id or self.owner_id
                ):
                    self.pending.pop(mid, None)
                    self.requesters.pop(mid, None)
            self.pending[m.id] = (plan, channel.id, now + 600)
            self.requesters[m.id] = user_id or self.owner_id
            if user_id is not None:
                from tiwa.listening import FOLLOWUPS

                FOLLOWUPS[user_id] = time.monotonic() + 60

    async def confirm(self, message_id, user_id, channel, approve):
        pending = self.pending.get(message_id)
        if pending is None:
            await channel.send(
                "No pending calendar proposal; it may already be handled or the bot restarted. Ask again."
            )
            return
        plan, channel_id, expires = pending
        if channel.id != channel_id:
            return
        if str(user_id) != str(self.requesters.get(message_id, self.owner_id)):
            await channel.send("Only the person who requested this appointment can confirm it.")
            return
        del self.pending[message_id]  # claim before awaiting: no duplicate writes
        self.requesters.pop(message_id, None)
        from tiwa.listening import FOLLOWUPS

        FOLLOWUPS.pop(user_id, None)
        if time.monotonic() >= expires:
            await channel.send("calendar proposal expired; ask again")
            return
        if not approve:
            await channel.send("Calendar proposal dropped.")
            return
        try:
            result = await asyncio.to_thread(gcal.apply_change, plan)
        except Exception as e:
            result = f"calendar change failed: {type(e).__name__}; check calendar before retrying"
        memory.log(self.db, "calendar", result)
        self.history[channel.id].append({"role": "assistant", "content": result})
        await channel.send(result)

    def decision(self, text):
        # Whole utterance only. STT may retain a mangled wake prefix even after local activation.
        text = re.sub(
            r"^\s*(?:(?:hey|เฮ้ย?)\s*[,!]?\s*(?:tiwa|ทิวา|ที่ว่า|ที่วา)|เหตุที่ว่า|ให้ที่ว่า|ให้ที่วา|ที่ว่า|ทิวา)[\s,!.:—-]*",
            "",
            text,
            flags=re.IGNORECASE,
        )
        match = re.fullmatch(
            r"\s*(?:please\s+)?(confirm|cancel|ยืนยัน|คอนเฟิร์ม|ยกเลิก)"
            r"(?:\s|สิ|เลย|นะ|ครับ|ค่ะ|คะ|จ้า|จ้ะ|ได้เลย|please|it|that|[.!])*",
            text.casefold(),
        )
        if not match:
            return None
        return match[1] in ("confirm", "ยืนยัน", "คอนเฟิร์ม")

    async def followup(self, channel, user_id, decision, mid=None):
        if mid is None:
            matches = [
                key
                for key, (_, cid, expires) in self.pending.items()
                if cid == channel.id
                and expires > time.monotonic()
                and str(self.requesters.get(key, self.owner_id)) == str(user_id)
            ]
            if len(matches) != 1:
                await channel.send(
                    "No single pending calendar proposal. Ask again if it expired or Tiwa restarted; if several are pending, reply to the one you want with confirm or cancel."
                )
                return
            mid = matches[0]
        await self.confirm(mid, user_id, channel, decision)

    async def answer(self, channel, user_id, text, mid=None):
        explicit = self.decision(text)
        candidates = [
            key
            for key, (_, cid, expires) in self.pending.items()
            if cid == channel.id
            and expires > time.monotonic()
            and str(self.requesters.get(key, self.owner_id)) == str(user_id)
        ]
        if mid is None and len(candidates) == 1:
            mid = candidates[0]
        if mid not in candidates:
            if explicit is not None:
                await self.followup(channel, user_id, explicit, mid)
                return True
            return False
        pending = self.pending[mid]
        if explicit is not None:
            await self.confirm(mid, user_id, channel, explicit)
            return True
        try:
            decision = await asyncio.wait_for(
                asyncio.to_thread(gcal.confirmation_reply, pending[0], text), 15
            )
        except Exception:
            await channel.send("ยังไม่ได้บันทึกนะ ฟังคำตอบไม่ชัด ต้องการให้บันทึกนัดนี้ไหม?")
            from tiwa.listening import FOLLOWUPS

            FOLLOWUPS[user_id] = time.monotonic() + 60
            return True
        # A delayed answer must never approve a replacement proposal.
        if self.pending.get(mid) is not pending:
            return True
        if decision in ("approve", "decline"):
            await self.confirm(mid, user_id, channel, decision == "approve")
            return True
        if decision == "unrelated":
            return False
        if decision == "revise":
            try:
                plan = await asyncio.to_thread(
                    gcal.prepare_change,
                    f"Revise this UNSAVED proposal, preserving its action and all details except the requested correction. "
                    f"Proposal: {pending[0]}\nRequester correction: {text}",
                )
                if self.pending.get(mid) is not pending:
                    return True
                self.pending[mid] = (plan, channel.id, time.monotonic() + 600)
                await channel.send(f"📅 {gcal.describe_change(plan)}\nตามนี้ ให้บันทึกเลยไหม?")
            except Exception:
                await channel.send("ยังไม่ได้บันทึกนะ ต้องการเปลี่ยนรายละเอียดไหน?")
        else:
            await channel.send(f"ยังไม่ได้บันทึกนะ — {gcal.describe_change(pending[0])}\nให้บันทึกตามนี้ไหม?")
        from tiwa.listening import FOLLOWUPS

        FOLLOWUPS[user_id] = time.monotonic() + 60
        return True

    async def text(self, message, text):
        ref = getattr(message, "reference", None)
        return await self.answer(
            message.channel, message.author.id, text, getattr(ref, "message_id", None)
        )
