"""record_purchase (PRD S7.2/S7.10 / T22): write a normalized Bill's line
items to the `purchase_ledger` collection, idempotent on retry/resume.

Each line item becomes its own ledger doc (the ledger is priced per item,
not per bill). The doc ID is a deterministic hash of vendor + invoice_no +
item_name + batch_no, so re-running the same bill (retry after a partial
failure, or a resumed paused run -- PRD S7.6) overwrites the same docs
instead of appending duplicates.
"""
from __future__ import annotations

import hashlib
import re

from google.cloud import firestore

from .firestore_client import purchase_ledger_collection
from .formats.schema import Bill

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def normalize_item_key(item_name: str) -> str:
    slug = _SLUG_RE.sub("_", item_name.strip().lower()).strip("_")
    return slug


def ledger_doc_id(vendor: str, invoice_no: str, item_name: str, batch_no: str) -> str:
    composite = f"{vendor}::{invoice_no}::{item_name}::{batch_no}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def record_purchase(bill: Bill, client: firestore.Client | None = None, seeded: bool = False) -> list[str]:
    """`seeded` marks synthesized (not observed) history -- PRD S10: any
    history beyond the real sample invoices must be distinguishable in the
    data itself, not just presented as observed. Real ingestion always
    passes the default `False`.
    """
    collection = purchase_ledger_collection(client)
    doc_ids: list[str] = []
    for item in bill.line_items:
        doc_id = ledger_doc_id(bill.vendor, bill.invoice_no, item.item_name, item.batch_no)
        payload = {
            "item_name": item.item_name,
            "normalized_item_key": normalize_item_key(item.item_name),
            "vendor": bill.vendor,
            "rate": item.rate,
            "mrp": item.mrp,
            "quantity": item.quantity,
            "batch_number": item.batch_no,
            "expiry_date": item.expiry_date,
            "invoice_no": bill.invoice_no,
            "purchase_date": bill.invoice_date,
            "seeded": seeded,
        }
        collection.document(doc_id).set(payload)
        doc_ids.append(doc_id)
    return doc_ids
