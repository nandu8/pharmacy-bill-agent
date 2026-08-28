"""Durable pause state (PRD S7.6 / T44, T45): persists a parked run's full
context to `agent_runs` when it ends in pending_pharmacist or
pending_vendor, and reads it back to rehydrate a fresh session once a reply
arrives.

The `agent_runs` doc id is the same `bill_doc_id` `record_bill_result`
(terminal.py) already wrote to the `bills` collection for this run -- one
correlation key shared across both collections, so resuming a run is a
direct lookup rather than a name-matching problem. The correlation key is
also stored as its own field (PRD S7.6 lists it as a distinct piece of
serialized state, not just implied by the doc id).

`find_resumable_run` (T45) is how the inbound webhook picks *which* parked
run a reply belongs to: a single equality filter (no order_by) avoids
needing a composite Firestore index, sorted client-side by each doc's own
`update_time` instead -- same reasoning status_page.py already uses for
`list_bills`. Matching a specific reply to a specific run via a stronger
signal (e.g. WhatsApp's own reply-to message id) is a real gap this
"most recently parked, not yet resumed" heuristic leaves for a
multi-bill-in-flight deployment -- acceptable for this single-pharmacist,
one-bill-parked-at-a-time hackathon build (PRD S11's Demo Bill 4), not a
general solution.
"""
from __future__ import annotations

import dataclasses

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from ..firestore_client import agent_runs_collection
from ..formats.schema import Bill, LineItem
from .terminal import PENDING_PHARMACIST

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
        "source_format": bill.source_format if bill is not None else None,
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


def deserialize_bill(serialized_state: dict) -> Bill | None:
    """Reconstruct the real Bill/LineItem instances the agent's tools expect
    (dataclass attribute access, dataclasses.asdict) from the plain dicts
    Firestore hands back -- the inverse of serialize_run_state's line_items
    encoding."""
    if serialized_state.get("vendor") is None:
        return None
    line_items = [LineItem(**item) for item in serialized_state.get("line_items", [])]
    source_format = serialized_state.get("source_format") or (
        line_items[0].source_format if line_items else "unknown"
    )
    return Bill(
        vendor=serialized_state["vendor"],
        invoice_no=serialized_state["invoice_no"],
        invoice_date=serialized_state["invoice_date"],
        source_format=source_format,
        line_items=line_items,
        total_amount=serialized_state.get("total_amount"),
    )


def get_agent_run(bill_doc_id: str, client: firestore.Client | None = None) -> dict | None:
    doc = agent_runs_collection(client).document(bill_doc_id).get()
    if not doc.exists:
        return None
    return doc.to_dict()


def mark_resumed(bill_doc_id: str, client: firestore.Client | None = None) -> None:
    agent_runs_collection(client).document(bill_doc_id).update(
        {"resumed_at": firestore.SERVER_TIMESTAMP}
    )


def _pick_most_recent(docs: list) -> object | None:
    if not docs:
        return None
    return max(docs, key=lambda doc: doc.update_time)


def find_resumable_run(
    client: firestore.Client | None = None,
    statuses: tuple[str, ...] = (PENDING_PHARMACIST,),
) -> dict | None:
    """The most recently parked run (in one of `statuses`) that hasn't been
    resumed yet -- see the module docstring for the correlation heuristic
    and its limits. Defaults to pending_pharmacist only: a pending_vendor
    run needs a corrected file from the vendor over Gmail, not a WhatsApp
    text reply, so resuming it is T46's job, not this one.

    Filtering by status happens client-side, not as a second Firestore
    equality clause -- a compound query needs a composite index Firestore
    won't auto-create, and this project's whole agent_runs collection is
    small enough that fetching the (few) unresumed docs and filtering in
    Python is simpler than provisioning one."""
    docs = list(
        agent_runs_collection(client)
        .where(filter=FieldFilter("resumed_at", "==", None))
        .stream()
    )
    candidates = [
        doc for doc in docs if doc.to_dict().get("serialized_state", {}).get("status") in statuses
    ]
    latest = _pick_most_recent(candidates)
    return latest.to_dict() if latest is not None else None
