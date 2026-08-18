"""One-time local OAuth flow to mint a Gmail/Drive refresh token (T08/T09).

Run once per environment:

    python scripts/gmail_oauth_setup.py

Reads ./credentials.json (Desktop app client, downloaded from the Google Auth
Platform "Clients" tab — gitignored, never commit it). Opens a browser for
consent, then writes ./token.json locally (gitignored) and prints the refresh
token so it can be pushed into Secret Manager:

    gcloud secrets versions add gmail-refresh-token --data-file=- --project=pharmacy-bill-agent

(paste the printed refresh token, then Ctrl-Z Enter on Windows / Ctrl-D on
Unix to end stdin).
"""

from __future__ import annotations

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive",
]

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"


def main() -> None:
    if not CREDENTIALS_PATH.exists():
        raise SystemExit(
            f"Missing {CREDENTIALS_PATH}. Download the Desktop app client JSON "
            "from Google Auth Platform > Clients and save it there."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_PATH), SCOPES)
    creds = flow.run_local_server(port=0)

    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print(f"\nWrote local token cache to {TOKEN_PATH}\n")

    if not creds.refresh_token:
        raise SystemExit(
            "No refresh_token returned. This usually means the account already "
            "granted consent before — revoke access at "
            "https://myaccount.google.com/permissions and re-run this script."
        )

    print("Refresh token (store this in Secret Manager, do not commit it):\n")
    print(creds.refresh_token)
    print(
        "\nTo store it:\n"
        "  gcloud secrets versions add gmail-refresh-token --data-file=- "
        "--project=pharmacy-bill-agent\n"
        "  (paste the refresh token above, then Ctrl-Z Enter to finish on Windows)"
    )


if __name__ == "__main__":
    main()
