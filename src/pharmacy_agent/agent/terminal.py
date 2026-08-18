"""Terminal states (PRD S7.1/S10 / T29): resolved / pending_pharmacist /
pending_vendor. The `finish` tool is how the model explicitly declares
which one a run ended in -- ADK's own "no more tool calls" signal only
tells the loop the model stopped, not what it concluded, and PRD S7.10
requires every bill land in a visible state, never be silently dropped.

`record_bill_result` persists that outcome to the `bills` collection, using
the same field names check_duplicate.py already reads (`invoice_number`,
`status`) so a bill written here is visible to future duplicate checks.
"""
from __future__ import annotations

import dataclasses
import hashlib
import uuid
from typing import Literal

from google.adk.tools.tool_context import ToolContext
from google.cloud import firestore

from ..firestore_client import bills_collection
from ..formats.schema import Bill

RESOLVED = "resolved"
PENDING_PHARMACIST = "pending_pharmacist"
PENDING_VENDOR = "pending_vendor"
TERMINAL_STATUSES = (RESOLVED, PENDING_PHARMACIST, PENDING_VENDOR)

FINISH_TOOL_NAME = "finish"


def _finish_impl(state, status: str, summary: str) -> dict:
    state["_terminal_status"] = status
    findings = list(state.get("_findings", []))
    findings.append(summary)
    state["_findings"] = findings
    return {"status": status, "summary": summary}


def finish(
    status: Literal["resolved", "pending_pharmacist", "pending_vendor"],
    summary: str,
    tool_context: ToolContext,
) -> dict:
    """Call this exactly once, as your last action, to end the run.
    status must be one of: "resolved" (the bill is verified and, where
    appropriate, recorded -- nothing more to do), "pending_pharmacist" (you
    are genuinely unsure and a human needs to decide), or "pending_vendor"
    (the file could not be read by any available tool). summary is one
    sentence explaining your conclusion."""
    return _finish_impl(tool_context.state, status, summary)


def bill_doc_id(vendor: str, invoice_no: str) -> str:
    """Deterministic like purchase_ledger.ledger_doc_id, so re-running a
    bill (retry, resume) converges on the same `bills` doc (PRD S7.10)."""
    composite = f"{vendor}::{invoice_no}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def record_bill_result(
    bill: Bill | None,
    status: str,
    findings: list[str],
    client: firestore.Client | None = None,
) -> str:
    """Persist a run's terminal outcome. When no bill was ever parsed (the
    pending_vendor "couldn't read the file by any route" case), there is no
    vendor/invoice_no to key on, so the doc gets a fresh random id each
    time -- there is nothing to converge on yet.
    """
    collection = bills_collection(client)
    if bill is not None:
        doc_id = bill_doc_id(bill.vendor, bill.invoice_no)
        payload = {
            "vendor": bill.vendor,
            "invoice_number": bill.invoice_no,
            "invoice_date": bill.invoice_date,
            "line_items": [dataclasses.asdict(li) for li in bill.line_items],
            "total_amount": bill.total_amount,
            "status": status,
            "findings": findings,
        }
    else:
        doc_id = uuid.uuid4().hex
        payload = {
            "vendor": None,
            "invoice_number": None,
            "status": status,
            "findings": findings,
        }
    collection.document(doc_id).set(payload, merge=True)
    return doc_id
