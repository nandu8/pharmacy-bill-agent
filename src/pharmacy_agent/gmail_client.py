"""Gmail send client (PRD S9 / T38).

Builds an authenticated Gmail API service via google_oauth.py's shared
credential loader (T48 factored the OAuth plumbing out so drive_client.py
can reuse it against the same refresh token with a different scope).
"""
from __future__ import annotations

import base64
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

from .google_oauth import load_credentials as _load_credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def load_credentials() -> Credentials:
    return _load_credentials(SCOPES)


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


def send_email_with_attachment(
    to: str,
    cc: str,
    subject: str,
    body: str,
    filename: str,
    file_bytes: bytes,
    service: Resource | None = None,
) -> dict:
    service = service or get_service()
    message = MIMEMultipart()
    message["to"] = to
    message["cc"] = cc
    message["subject"] = subject
    message.attach(MIMEText(body))

    attachment = MIMEApplication(file_bytes)
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    message.attach(attachment)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return service.users().messages().send(userId="me", body={"raw": raw}).execute()
