"""Shared OAuth credential loading for Gmail and Drive (PRD S9 / T38, T48).

Both `gmail_client.py` and `drive_client.py` authenticate as the
pharmacist's own OAuth identity (personal Gmail/Drive can't use the Cloud
Run service account -- no domain-wide delegation, T11) using the same
refresh token and Desktop OAuth client id/secret, held in Secret Manager
(`gmail-refresh-token` from T10, `gmail-oauth-client` from T38). The
refresh token was minted in T08/T09 against the combined
gmail.readonly/gmail.send/drive consent, so the same token is valid for
either scope -- callers just pass the scope list the resulting service
actually needs.
"""
from __future__ import annotations

import json
import os

from google.auth.transport.requests import Request
from google.cloud import secretmanager
from google.oauth2.credentials import Credentials

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "pharmacy-bill-agent")


def _access_secret(secret_id: str, project: str = _PROJECT) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_id}/versions/latest"
    return client.access_secret_version(name=name).payload.data.decode("utf-8")


def load_credentials(scopes: list[str]) -> Credentials:
    refresh_token = _access_secret("gmail-refresh-token")
    client_config = json.loads(_access_secret("gmail-oauth-client"))
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_config["client_id"],
        client_secret=client_config["client_secret"],
        scopes=scopes,
    )
    creds.refresh(Request())
    return creds
