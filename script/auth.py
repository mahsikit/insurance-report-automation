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

# Fixed port so the redirect URI is predictable for web-type OAuth clients.
# Register http://localhost:8080/ in the Cloud Console Authorized redirect URIs
# for the satria_yudha.json client before running for the first time.
_LOCAL_SERVER_PORT = 8080


def get_credentials(client_secret_file: str, token_file: str) -> Credentials:
    """Return valid OAuth2 credentials, running browser consent flow on first use.

    Works with both 'installed' (Desktop) and 'web' type OAuth 2.0 client JSONs.
    For web clients the redirect URI must be http://localhost:8080/ — register
    that URI in Google Cloud Console → APIs & Services → Credentials.
    """
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
                port=_LOCAL_SERVER_PORT,
                prompt="select_account",
                login_hint=hint,
            )

        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return creds
