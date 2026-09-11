# Calendar

`gcal.py` reads/writes the configured `TIWA_CALENDAR_ID`, falling back to `primary`.
Authorize with `gcal_auth.py`; the token stays in `data/gcal_token.json`. The target
calendar must be accessible to the authorized Google account.

## Request → proposal → answer → save

1. Calendar Tiwa queues an action. Google is not changed yet.
2. `discord_calendar.Calendar` parses and shows a concrete proposal.
3. The requester answers naturally by text or their next voice response.
4. Clear agreement saves the exact proposal; refusal drops it; a correction creates
   revised details for another confirmation. Unclear replies do not write.
5. The actual Google API result is sent to Discord, including its link when available.

Approval uses the requesting Discord account ID. `TIWA_OWNER_ID` is a fallback only
for proposals not associated with a requester. A display name is never permission.
An affirmative reply to an unrelated conversation is not approval. The classifier
receives the actual pending proposal, and a delayed verdict cannot approve a replaced
proposal. Writes are one-shot; a repeat confirmation does not repeat the event.

For add requests with missing details, the parser uses visible defaults: generic
Appointment title, next occurrence of a stated day (otherwise today), 09:00 when time
is absent (tomorrow if already past), and a one-hour duration. Review these defaults.
Edits/deletions require identifying details and reject ambiguous matching events.
Moving an event preserves duration unless explicitly changed; edits use the event's
revision when available.

Normal reads use a bounded upcoming window. Fresh Google results take precedence over
chat history; remembered plans are not proof an event exists. Pending proposals expire
after ten minutes and are lost on restart. After an uncertain write error, check Google
before retrying. Tests: `calendarconfirmbench`, `calendarconversationbench`,
`calendarproofbench`, `calendareditbench`, `calminibench`.
