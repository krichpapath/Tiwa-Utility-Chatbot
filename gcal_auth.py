"""One-time Google Calendar OAuth. Opens a browser; token -> data/gcal_token.json.

Usage: py -X utf8 gcal_auth.py
"""

from google_auth_oauthlib.flow import InstalledAppFlow

from tiwa import gcal

flow = InstalledAppFlow.from_client_secrets_file(gcal.client_secret_path(), gcal.SCOPES)
creds = flow.run_local_server(port=0)
gcal.TOKEN.parent.mkdir(exist_ok=True)
gcal.TOKEN.write_text(creds.to_json())
print("token saved:", gcal.TOKEN)
