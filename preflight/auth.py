"""Credentials for the teacher's Google account and Schoology.

The files belong to the teacher's school account, so the job acts as the teacher
through an OAuth refresh token (read-only Drive and Sheets scopes, plus drive.file
so it can write its own report into the output folder and nothing else). On Cloud
Run the token and the Schoology key come from Secret Manager as environment variables.
"""

from __future__ import annotations

import json
import os

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.file",
]


def google_credentials():
    from google.oauth2.credentials import Credentials

    raw = os.environ.get("GOOGLE_OAUTH_TOKEN_JSON")
    if raw:
        info = json.loads(raw)
    else:
        path = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE", "secrets/token.json")
        with open(path) as fh:
            info = json.load(fh)
    return Credentials.from_authorized_user_info(info, SCOPES)


def schoology_keys() -> tuple[str, str] | None:
    key, secret = os.environ.get("SCHOOLOGY_KEY"), os.environ.get("SCHOOLOGY_SECRET")
    return (key, secret) if key and secret else None


def authorize_interactively(client_secrets: str, out_path: str = "secrets/token.json") -> None:
    """One-time, on the teacher's computer: sign in with the school account and save the refresh token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as fh:
        fh.write(creds.to_json())
    print(f"Saved {out_path}. Store it in Secret Manager as preflight-google-token.")
