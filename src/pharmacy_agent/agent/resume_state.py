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

from ..firestore_client import agent_runs_collection, bills_collection
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
    vendor_hint: str = "",
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
        # T46: the pending_vendor case has no parsed bill at all -- the
        # Gmail sender (ingestion context, never the document itself) is
        # the only vendor signal find_resumable_run has to match a resend
        # against, so it's captured even though bill.vendor already covers
        # this when a bill did parse.
        "vendor_hint": vendor_hint,
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


def retire_placeholder(bill_doc_id: str, client: firestore.Client | None = None) -> None:
    """T46: a pending_vendor park with no bill yet keys its bills/agent_runs
    docs on a random placeholder id (terminal.py's record_bill_result). Once
    a resend finally parses into a real vendor/invoice_no key -- a
    different id from that placeholder -- the placeholder is stale and
    would otherwise linger forever as a phantom pending_vendor bill next to
    the real, now-resolved one."""
    bills_collection(client).document(bill_doc_id).delete()
    agent_runs_collection(client).document(bill_doc_id).delete()


def _pick_most_recent(docs: list) -> object | None:
    if not docs:
        return None
    return max(docs, key=lambda doc: doc.update_time)


def find_resumable_run(
    client: firestore.Client | None = None,
    statuses: tuple[str, ...] = (PENDING_PHARMACIST,),
    vendor_hint: str | None = None,
) -> dict | None:
    """The most recently parked run (in one of `statuses`) that hasn't been
    resumed yet -- see the module docstring for the correlation heuristic
    and its limits. Defaults to pending_pharmacist only: a pending_vendor
    run needs a corrected file from the vendor over Gmail, not a WhatsApp
    text reply (T46 passes statuses=(PENDING_VENDOR,) and its own
    vendor_hint instead).

    `vendor_hint`, when given, narrows candidates to runs parked from that
    same Gmail sender (T46) -- without it, a resend from any vendor would
    match whichever pending_vendor run happened to be most recent, which is
    wrong the moment more than one vendor has a bill parked at once.

    Filtering happens client-side, not as Firestore equality clauses --
    compound queries need a composite index Firestore won't auto-create,
    and this project's whole agent_runs collection is small enough that
    fetching the (few) unresumed docs and filtering in Python is simpler
    than provisioning one."""
    docs = list(
        agent_runs_collection(client)
        .where(filter=FieldFilter("resumed_at", "==", None))
        .stream()
    )
    candidates = [
        doc for doc in docs if doc.to_dict().get("serialized_state", {}).get("status") in statuses
    ]
    if vendor_hint is not None:
        candidates = [
            doc
            for doc in candidates
            if doc.to_dict().get("serialized_state", {}).get("vendor_hint") == vendor_hint
        ]
    latest = _pick_most_recent(candidates)
    return latest.to_dict() if latest is not None else None
