"""email_vendor (PRD S7.2/S7.10 / T38): resend-request and dispute modes.

Recipient is always resolved from the trusted vendor directory (T38's
vendor_directory.py) -- the tool takes a vendor *name*, never a raw email
address, so nothing extracted from a parsed bill or an email body can
redirect where the agent sends mail. Every send CCs the pharmacist. A
deterministic dedup key (vendor + reference + mode), logged to
`email_log`, enforces "fires at most once per invoice per mode" (S7.10)
across retries and resumed runs (S7.6) -- `reference` is the invoice
number for dispute mode, or whatever identifies the unreadable document
for resend mode (a bill may not parse far enough to yield an invoice_no).
"""
from __future__ import annotations

import hashlib
from typing import Literal

from google.cloud import firestore

from .firestore_client import get_client
from .gmail_client import send_email
from .vendor_directory import get_pharmacist_email, get_vendor_email

EMAIL_LOG_COLLECTION = "email_log"

Mode = Literal["resend", "dispute"]
_VALID_MODES = ("resend", "dispute")


def email_log_doc_id(vendor: str, reference: str, mode: str) -> str:
    composite = f"{vendor}::{reference}::{mode}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def email_vendor(
    vendor: str,
    reference: str,
    mode: Mode,
    subject: str,
    body: str,
    client: firestore.Client | None = None,
    gmail_service=None,
) -> dict:
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {_VALID_MODES}, got {mode!r}")

    client = client or get_client()
    log_collection = client.collection(EMAIL_LOG_COLLECTION)
    doc_id = email_log_doc_id(vendor, reference, mode)

    if log_collection.document(doc_id).get().exists:
        return {"sent": False, "reason": "already_sent", "mode": mode, "log_id": doc_id}

    to_email = get_vendor_email(vendor, client=client)
    if not to_email:
        return {"sent": False, "reason": "vendor_not_in_directory", "mode": mode, "log_id": doc_id}

    cc_email = get_pharmacist_email(client=client)
    if not cc_email:
        return {"sent": False, "reason": "pharmacist_not_configured", "mode": mode, "log_id": doc_id}

    result = send_email(to=to_email, cc=cc_email, subject=subject, body=body, service=gmail_service)

    log_collection.document(doc_id).set(
        {
            "vendor": vendor,
            "reference": reference,
            "mode": mode,
            "to": to_email,
            "cc": cc_email,
            "subject": subject,
            "gmail_message_id": result.get("id"),
        }
    )

    return {
        "sent": True,
        "mode": mode,
        "log_id": doc_id,
        "gmail_message_id": result.get("id"),
        "to": to_email,
        "cc": cc_email,
    }
