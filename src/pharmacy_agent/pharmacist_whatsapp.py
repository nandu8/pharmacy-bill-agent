"""notify_pharmacist / ask_pharmacist (PRD S7.2/S7.10 / T41): WhatsApp
messages to the pharmacist via the Meta WhatsApp Cloud API.

The recipient is always resolved from the trusted config directory
(vendor_directory.py's `config/pharmacist` doc), never a raw number passed
by a caller -- same guardrail as email_vendor's vendor-directory lookup. A
deterministic dedup key (vendor + reference + mode), logged to
`whatsapp_log`, enforces "at most once per invoice per mode" (S7.10) the
same way email_vendor's `email_log` does.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from google.cloud import firestore

from .firestore_client import get_client
from .meta_whatsapp_client import send_whatsapp
from .vendor_directory import get_pharmacist_whatsapp

WHATSAPP_LOG_COLLECTION = "whatsapp_log"

Mode = Literal["notify", "ask"]
_VALID_MODES = ("notify", "ask")


def whatsapp_log_doc_id(vendor: str, reference: str, mode: str) -> str:
    composite = f"{vendor}::{reference}::{mode}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def _send(
    vendor: str,
    reference: str,
    mode: Mode,
    message: str,
    client: firestore.Client | None,
    messaging_client,
) -> dict:
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")

    client = client or get_client()
    log_collection = client.collection(WHATSAPP_LOG_COLLECTION)
    doc_id = whatsapp_log_doc_id(vendor, reference, mode)

    if log_collection.document(doc_id).get().exists:
        return {"sent": False, "reason": "already_sent", "mode": mode, "log_id": doc_id}

    to_number = get_pharmacist_whatsapp(client=client)
    if not to_number:
        return {"sent": False, "reason": "pharmacist_not_configured", "mode": mode, "log_id": doc_id}

    result = send_whatsapp(to=to_number, body=message, client=messaging_client)

    log_collection.document(doc_id).set(
        {
            "vendor": vendor,
            "reference": reference,
            "mode": mode,
            "to": to_number,
            "message": message,
            "message_id": result.get("sid"),
        }
    )

    return {
        "sent": True,
        "mode": mode,
        "log_id": doc_id,
        "message_id": result.get("sid"),
        "to": to_number,
    }


def notify_pharmacist(
    vendor: str,
    reference: str,
    message: str,
    client: firestore.Client | None = None,
    messaging_client=None,
) -> dict:
    """Outbound-only WhatsApp: success digest, or notice of an action
    already taken (PRD S7.2). Fires at most once per bill per mode (S7.10)."""
    return _send(vendor, reference, "notify", message, client, messaging_client)


def ask_pharmacist(
    vendor: str,
    reference: str,
    question: str,
    client: firestore.Client | None = None,
    messaging_client=None,
) -> dict:
    """Outbound WhatsApp with one targeted question. The caller is
    responsible for ending the run in pending_pharmacist afterward (PRD
    S7.1) -- durable serialize/resume so the eventual reply can rehydrate
    the run is T44/T45, not built here. Fires at most once per bill per mode
    (S7.10)."""
    return _send(vendor, reference, "ask", question, client, messaging_client)
