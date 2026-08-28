"""Durable pause state (PRD S7.6 / T44): persists a parked run's full
context to `agent_runs` when it ends in pending_pharmacist or
pending_vendor, so a later inbound reply (T45/T46) can rehydrate the exact
turn it stopped on instead of restarting the bill from scratch.

The `agent_runs` doc id is the same `bill_doc_id` `record_bill_result`
(terminal.py) already wrote to the `bills` collection for this run -- one
correlation key shared across both collections, so resuming a run is a
direct lookup rather than a name-matching problem. The correlation key is
also stored as its own field (PRD S7.6 lists it as a distinct piece of
serialized state, not just implied by the doc id).
"""
from __future__ import annotations

import dataclasses

from google.cloud import firestore

from ..firestore_client import agent_runs_collection
from ..formats.schema import Bill

ASK_PHARMACIST_TOOL_NAME = "ask_pharmacist"


def _extract_open_question(tool_call_history) -> str | None:
    for record in reversed(tool_call_history):
        if record.tool == ASK_PHARMACIST_TOOL_NAME:
            return record.args.get("question")
    return None


def serialize_run_state(
    bill: Bill | None,
    status: str,
    findings: list[str],
    tool_call_history,
    bill_doc_id: str,
    client: firestore.Client | None = None,
) -> str:
    serialized_state = {
        "vendor": bill.vendor if bill is not None else None,
        "invoice_no": bill.invoice_no if bill is not None else None,
        "invoice_date": bill.invoice_date if bill is not None else None,
        "total_amount": bill.total_amount if bill is not None else None,
        "line_items": [dataclasses.asdict(li) for li in bill.line_items] if bill is not None else [],
        "findings": list(findings),
        "status": status,
    }
    payload = {
        "bill_id": bill_doc_id,
        "correlation_key": bill_doc_id,
        "serialized_state": serialized_state,
        "tool_call_history": [{"tool": r.tool, "args": r.args} for r in tool_call_history],
        "open_question": _extract_open_question(tool_call_history),
        "paused_at": firestore.SERVER_TIMESTAMP,
        "resumed_at": None,
    }
    agent_runs_collection(client).document(bill_doc_id).set(payload)
    return bill_doc_id
