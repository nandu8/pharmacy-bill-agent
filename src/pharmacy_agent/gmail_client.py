"""Gmail send client (PRD S9 / T38).

Builds an authenticated Gmail API service from the OAuth refresh token in
Secret Manager (`gmail-refresh-token`, T10) plus the Desktop OAuth client's
id/secret (`gmail-oauth-client`, provisioned for T38 from the same
credentials.json used in T08 -- Cloud Run has no local file to read it
from). Personal Gmail can't use the Cloud Run service account (no
domain-wide delegation -- T11), so every environment, including
production, authenticates as the pharmacist's own OAuth identity.
"""
from __future__ import annotations

import base64
import json
import os
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.cloud import secretmanager
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "pharmacy-bill-agent")


def _access_secret(secret_id: str, project: str = _PROJECT) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_id}/versions/latest"
    return client.access_secret_version(name=name).payload.data.decode("utf-8")


def load_credentials() -> Credentials:
    refresh_token = _access_secret("gmail-refresh-token")
    client_config = json.loads(_access_secret("gmail-oauth-client"))
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_config["client_id"],
        client_secret=client_config["client_secret"],
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def get_service() -> Resource:
    return build("gmail", "v1", credentials=load_credentials())


def send_email(to: str, cc: str, subject: str, body: str, service: Resource | None = None) -> dict:
    service = service or get_service()
    message = MIMEText(body)
    message["to"] = to
    message["cc"] = cc
    message["subject"] = subject
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()
