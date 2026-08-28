"""Pub/Sub push -> agent-run orchestration (PRD S9/S7.1 / T52).

Decodes a Gmail Pub/Sub push notification, resolves which messages arrived
since the last processed history ID (gmail_history.py, T51/T52), and
starts an agent run (agent/loop.py, T28) for each attachment found. The
history watermark is persisted in Firestore (gmail_history.py) so Cloud
Run's stateless instances don't need to remember it between invocations.

At-least-once delivery means the same notification can arrive twice; that
is safe here because every downstream write (record_purchase, bills,
email_log) is already idempotent on vendor+invoice_no (S7.10) -- a
reprocessed attachment just converges on the same docs rather than
duplicating them.

T46/PRD S7.6: before treating a new attachment as a brand new bill, check
whether it's actually a vendor's resend of a bill parked pending_vendor
(the earlier attempt was unreadable by any parser) -- matched by sender
address (resume_state.find_resumable_run's vendor_hint), the same signal
ingestion already uses as the vendor identity for a fresh bill. If one
matches, resume_bill_with_file_fn continues that parked run instead of
starting a new one.
"""
from __future__ import annotations

import base64
import json

from google.cloud import firestore
from googleapiclient.discovery import Resource

from .agent.loop import resume_bill_with_file as _default_resume_bill_with_file
from .agent.loop import run_bill as _default_run_bill
from .agent.resume_state import find_resumable_run
from .agent.terminal import PENDING_VENDOR
from .firestore_client import get_client
from .gmail_history import fetch_new_attachments, get_last_history_id, set_last_history_id


def decode_pubsub_message(envelope: dict) -> dict:
    data = base64.b64decode(envelope["message"]["data"])
    return json.loads(data)


def handle_pubsub_push(
    envelope: dict,
    gmail_service: Resource | None = None,
    firestore_client: firestore.Client | None = None,
    run_bill_fn=_default_run_bill,
    resume_bill_with_file_fn=_default_resume_bill_with_file,
) -> list[dict]:
    firestore_client = firestore_client or get_client()
    notification = decode_pubsub_message(envelope)
    new_history_id = notification["historyId"]

    last_history_id = get_last_history_id(client=firestore_client)
    if last_history_id is None:
        # First notification since watch registration (T51) -- nothing to
        # diff against yet, so just establish the baseline.
        set_last_history_id(new_history_id, client=firestore_client)
        return []

    attachments = fetch_new_attachments(last_history_id, service=gmail_service)

    results = []
    for attachment in attachments:
        resumable = find_resumable_run(
            client=firestore_client,
            statuses=(PENDING_VENDOR,),
            vendor_hint=attachment["sender"],
        )
        if resumable is not None:
            run_result = resume_bill_with_file_fn(resumable["bill_id"], attachment["bytes"])
        else:
            run_result = run_bill_fn(attachment["bytes"], vendor_hint=attachment["sender"])
        results.append(
            {
                "message_id": attachment["message_id"],
                "sender": attachment["sender"],
                "filename": attachment["filename"],
                "run_result": run_result,
            }
        )

    set_last_history_id(new_history_id, client=firestore_client)
    return results
