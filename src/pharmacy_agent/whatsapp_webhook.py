"""Inbound WhatsApp webhook (PRD S7.6 / T42): the separate Cloud Run
entrypoint that receives the pharmacist's WhatsApp replies.

Meta's WhatsApp Cloud API requires two things of a webhook URL:

1. A one-time GET handshake to prove ownership (`hub.mode`/`hub.verify_token`
   /`hub.challenge`), the token compared against a value chosen when
   subscribing the webhook in the Meta App Dashboard.
2. Every POST delivery signed with `X-Hub-Signature-256` (HMAC-SHA256 over
   the raw body, keyed with the app secret) -- same "internet-reachable
   endpoint must verify its caller" guardrail as app.py's Pub/Sub push
   verification (T52), just Meta's scheme instead of Google's OIDC one.

This module only receives and durably logs each inbound text message,
deduped on Meta's own message id (at-least-once delivery, same as the
Pub/Sub push). Matching a reply to a parked run via correlation key and
rehydrating the agent's serialized state is T44/T45 (Phase 8) -- not built
here.
"""
from __future__ import annotations

import hashlib
import hmac

from google.cloud import firestore

from .firestore_client import whatsapp_inbound_collection
from .secrets_client import access_secret

WEBHOOK_VERIFY_TOKEN_SECRET_ID = "meta-whatsapp-webhook-verify-token"
APP_SECRET_SECRET_ID = "meta-whatsapp-app-secret"


def webhook_verify_token() -> str:
    return access_secret(WEBHOOK_VERIFY_TOKEN_SECRET_ID)


def webhook_app_secret() -> str:
    return access_secret(APP_SECRET_SECRET_ID)


def verify_subscription_token(mode: str | None, token: str | None, expected_token: str) -> bool:
    return mode == "subscribe" and token is not None and hmac.compare_digest(token, expected_token)


def verify_signature(app_secret: str, raw_body: bytes, signature_header: str | None) -> bool:
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


def extract_messages(payload: dict) -> list[dict]:
    """Flatten Meta's nested entry/changes/value/messages structure into
    {message_id, from, body, timestamp} dicts. Delivery-status callbacks
    (value.statuses) and non-text messages (media, reactions, ...) are
    skipped -- only text replies drive the eventual resume flow."""
    messages = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            for message in change.get("value", {}).get("messages", []):
                if message.get("type") != "text":
                    continue
                messages.append(
                    {
                        "message_id": message["id"],
                        "from": message["from"],
                        "body": message.get("text", {}).get("body", ""),
                        "timestamp": message.get("timestamp"),
                    }
                )
    return messages


def record_inbound_message(message: dict, client: firestore.Client | None = None) -> bool:
    """Idempotent store keyed on Meta's own message id. Returns True if this
    was a new message, False if already recorded (webhook retry)."""
    doc_ref = whatsapp_inbound_collection(client).document(message["message_id"])
    if doc_ref.get().exists:
        return False
    doc_ref.set(message)
    return True
