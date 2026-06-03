import os
import json
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.compose",
]


def get_credentials(client_secret_file: str, token_file: str) -> Credentials:
    """Return valid OAuth2 credentials, running browser consent flow on first use."""
    creds = None

    if os.path.exists(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, SCOPES)
            hint = os.environ.get("GOOGLE_LOGIN_HINT")
            creds = flow.run_local_server(
                port=0,
                prompt="select_account",
                login_hint=hint,
            )

        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return creds
