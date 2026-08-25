"""Gmail history delta fetching (PRD S9 / T52): resolves which messages
arrived since the last processed Gmail history ID and pulls their
attachments, so the Pub/Sub push handler (ingest.py) doesn't have to poll
or re-read the whole mailbox on every notification.

The last processed history ID is persisted in Firestore (`config/gmail_watch`)
rather than kept in memory -- Cloud Run instances are stateless/ephemeral
between invocations, so nothing survives in-process between pushes.
"""
from __future__ import annotations

import base64

from google.cloud import firestore
from googleapiclient.discovery import Resource, build

from .firestore_client import get_client
from .google_oauth import load_credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
GMAIL_WATCH_CONFIG_DOC = "gmail_watch"

# Container formats the agent can parse -- skip anything else (inline
# signature images, etc.) rather than running every attachment through
# the pipeline.
ATTACHMENT_EXTENSIONS = (".csv", ".xls", ".pdf")


def get_service() -> Resource:
    return build("gmail", "v1", credentials=load_credentials(SCOPES))


def get_last_history_id(client: firestore.Client | None = None) -> str | None:
    client = client or get_client()
    doc = client.collection("config").document(GMAIL_WATCH_CONFIG_DOC).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("last_history_id")


def set_last_history_id(history_id: str, client: firestore.Client | None = None) -> None:
    client = client or get_client()
    client.collection("config").document(GMAIL_WATCH_CONFIG_DOC).set(
        {"last_history_id": history_id}, merge=True
    )


def _get_sender(headers: list[dict]) -> str:
    for header in headers:
        if header.get("name", "").lower() == "from":
            return header.get("value", "")
    return ""


def _walk_parts_for_attachments(message_id: str, parts: list[dict], service: Resource) -> list[dict]:
    attachments = []
    for part in parts or []:
        filename = part.get("filename") or ""
        body = part.get("body", {})
        if filename.lower().endswith(ATTACHMENT_EXTENSIONS) and body.get("attachmentId"):
            attachment = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=body["attachmentId"])
                .execute()
            )
            file_bytes = base64.urlsafe_b64decode(attachment["data"])
            attachments.append({"filename": filename, "bytes": file_bytes})
        if part.get("parts"):
            attachments.extend(_walk_parts_for_attachments(message_id, part["parts"], service))
    return attachments


def fetch_new_attachments(start_history_id: str, service: Resource | None = None) -> list[dict]:
    """One dict per new message that carries a parseable attachment:
    {message_id, sender, filename, bytes}. Messages with no matching
    attachment (plain replies, notifications) are skipped."""
    service = service or get_service()

    message_ids: set[str] = set()
    page_token = None
    while True:
        response = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                pageToken=page_token,
            )
            .execute()
        )
        for record in response.get("history", []):
            for added in record.get("messagesAdded", []):
                message_ids.add(added["message"]["id"])
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    results: list[dict] = []
    for message_id in message_ids:
        message = service.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = message.get("payload", {})
        sender = _get_sender(payload.get("headers", []))
        for attachment in _walk_parts_for_attachments(message_id, payload.get("parts", []), service):
            results.append(
                {
                    "message_id": message_id,
                    "sender": sender,
                    "filename": attachment["filename"],
                    "bytes": attachment["bytes"],
                }
            )

    return results
