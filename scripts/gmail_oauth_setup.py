"""One-time local OAuth flow to mint a Gmail/Drive refresh token (T08/T09).

Run once per environment (or whenever the token expires -- PRD S9's 7-day
trap while the OAuth app was in Testing status; less frequent now it's in
Production, but revocation/re-consent can still happen):

    python scripts/gmail_oauth_setup.py

Reads ./credentials.json (Desktop app client, downloaded from the Google Auth
Platform "Clients" tab — gitignored, never commit it). Opens a browser for
consent, writes ./token.json locally (gitignored), then pushes the refresh
token straight into Secret Manager via the API -- it is never printed to
stdout or otherwise surfaced, since a value that hits a terminal/log is a
value that can leak (same principle as CLAUDE.md's Secrets section).
"""

from __future__ import annotations

from pathlib import Path

from google.cloud import secretmanager
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/drive",
]

PROJECT = "pharmacy-bill-agent"
SECRET_ID = "gmail-refresh-token"

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
    print(f"Wrote local token cache to {TOKEN_PATH}")

    if not creds.refresh_token:
        raise SystemExit(
            "No refresh_token returned. This usually means the account already "
            "granted consent before — revoke access at "
            "https://myaccount.google.com/permissions and re-run this script."
        )

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT}/secrets/{SECRET_ID}"
    version = client.add_secret_version(
        request={"parent": parent, "payload": {"data": creds.refresh_token.encode("utf-8")}}
    )
    print(f"Stored new refresh token as {version.name} (value not printed)")


if __name__ == "__main__":
    main()
