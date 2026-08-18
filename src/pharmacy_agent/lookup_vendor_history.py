"""lookup_vendor_history (PRD S7.2 / T24): this vendor's prior purchases of
a given item, from the purchase ledger -- feeds the price-deviation check
(T34) and the market-shift vs. vendor-error cross-check (T33, PRD S7.4
worked examples: "confirms the rise is real", "this vendor's own last four
invoices").

Matches on `normalized_item_key` (not raw item_name) so minor formatting
differences between invoices of the same item still line up.
"""
from __future__ import annotations

import dataclasses

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .firestore_client import purchase_ledger_collection
from .purchase_ledger import normalize_item_key


@dataclasses.dataclass
class VendorHistoryEntry:
    item_name: str
    normalized_item_key: str
    vendor: str
    rate: float
    mrp: float
    quantity: float
    batch_number: str
    expiry_date: str
    invoice_no: str
    purchase_date: str


def lookup_vendor_history(
    vendor: str,
    item_name: str,
    client: firestore.Client | None = None,
    limit: int | None = None,
) -> list[VendorHistoryEntry]:
    key = normalize_item_key(item_name)
    query = purchase_ledger_collection(client).where(
        filter=FieldFilter("vendor", "==", vendor)
    ).where(filter=FieldFilter("normalized_item_key", "==", key))
    entries = [VendorHistoryEntry(**doc.to_dict()) for doc in query.stream()]
    entries.sort(key=lambda entry: entry.purchase_date, reverse=True)
    if limit is not None:
        entries = entries[:limit]
    return entries
