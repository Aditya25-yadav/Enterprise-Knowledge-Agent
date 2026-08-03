"""
Google Drive OAuth2 authentication.

One-time setup required before this works:
  1. Go to console.cloud.google.com -> create/select a project
  2. Enable the "Google Drive API" for that project
  3. Configure the OAuth consent screen (External is fine for personal testing)
  4. Create OAuth client credentials -> Application type: Desktop app
  5. Download the JSON and save it as credentials.json in this project's root

First run opens a browser window for you to grant access, then caches a
token.json so you don't have to log in again on every run.
"""

from __future__ import annotations
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Read-only is deliberate: this connector only ever reads content + permissions,
# it never modifies anything in the user's Drive.
SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]

CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
TOKEN_PATH = os.getenv("GOOGLE_TOKEN_PATH", "token.json")


def get_credentials() -> Credentials:
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"Missing {CREDENTIALS_PATH}. Download OAuth client credentials "
                    "from Google Cloud Console (see module docstring) and place them here."
                )
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())

    return creds
