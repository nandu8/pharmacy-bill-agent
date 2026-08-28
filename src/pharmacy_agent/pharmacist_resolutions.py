"""Pharmacist resolution memory (PRD S7.7 / T58): every human decision on a
parked price question is written back to Firestore and feeds the validation
context of *future* runs for that vendor+item -- not just re-displayed on
the one bill that raised it.

Keyed on vendor + `normalized_item_key` (not invoice_no), one doc per
vendor+item, same idempotent-overwrite pattern as `purchase_ledger`: a new
resolution for the same vendor+item supersedes the old one rather than
accumulating a history the lookup would have to sort, since only the
*latest* human decision governs what happens next (PRD S7.7: "the agent
stops flagging it" / "weighted more heavily next time" are both standing
decisions, not one-off notes).

`check_price_deviation` (T34) is the consumer -- see its module docstring
for how `APPROVED`/`REJECTED` change its signal.
"""
from __future__ import annotations

import dataclasses
import hashlib
from enum import Enum

from google.cloud import firestore

from .firestore_client import pharmacist_resolutions_collection
from .purchase_ledger import normalize_item_key


class PharmacistDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclasses.dataclass
class PharmacistResolution:
    vendor: str
    item_name: str
    normalized_item_key: str
    rate: float
    decision: PharmacistDecision
    note: str
    invoice_no: str
    resolved_at: str


def resolution_doc_id(vendor: str, item_name: str) -> str:
    composite = f"{vendor}::{normalize_item_key(item_name)}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def record_pharmacist_resolution(
    vendor: str,
    item_name: str,
    rate: float,
    decision: PharmacistDecision,
    note: str = "",
    invoice_no: str = "",
    client: firestore.Client | None = None,
) -> str:
    doc_id = resolution_doc_id(vendor, item_name)
    payload = {
        "vendor": vendor,
        "item_name": item_name,
        "normalized_item_key": normalize_item_key(item_name),
        "rate": rate,
        "decision": decision.value if isinstance(decision, PharmacistDecision) else decision,
        "note": note,
        "invoice_no": invoice_no,
        "resolved_at": firestore.SERVER_TIMESTAMP,
    }
    pharmacist_resolutions_collection(client).document(doc_id).set(payload)
    return doc_id


def lookup_pharmacist_resolution(
    vendor: str, item_name: str, client: firestore.Client | None = None
) -> PharmacistResolution | None:
    doc = pharmacist_resolutions_collection(client).document(resolution_doc_id(vendor, item_name)).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    resolved_at = data.get("resolved_at")
    return PharmacistResolution(
        vendor=data["vendor"],
        item_name=data["item_name"],
        normalized_item_key=data["normalized_item_key"],
        rate=data["rate"],
        decision=PharmacistDecision(data["decision"]),
        note=data.get("note", ""),
        invoice_no=data.get("invoice_no", ""),
        resolved_at=resolved_at.isoformat() if resolved_at is not None else "",
    )
