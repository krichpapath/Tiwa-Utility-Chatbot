# Dashboard

Run `dashboard.py --open` using the project Python. The panel binds to localhost on
port 8787. It includes Overview, Chat, Settings, Wake recordings, Memory, Model calls,
and Activity log.

- `dashboard.py`: page construction, callbacks, data tables and exports.
- `tiwa/dashboard_settings.py`: setting definitions, defaults and explanatory labels.
- `assets/dashboard.css`: pastel-red visual styling and responsive rules.
- `tiwa/recordings.py`: recording library and file validation.
- `tiwa/memory.py`, `tiwa/recall.py`: memory data and recall preview.

Settings writes preserve unrelated `.env` entries and do not render credential values.
Restart the bot after changes. Dashboard chat uses the real pipeline and may incur
model charges, but reports Discord-only actions rather than executing them. Recordings
can be saved, played, labeled, trimmed into copies and deleted. Training is a separate
explicit local script, not automatic after recording.

Run `panelbench`, `dashboardbench`, and `recordingsbench` after dashboard changes.
For visual changes, inspect the actual local page as well as passing data tests.
