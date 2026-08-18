"""check_duplicate (PRD S7.2/S7.4 / T23): invoice_no + vendor lookup against
the `bills` collection.

Same invoice number + vendor recurring is not automatically a duplicate --
per PRD S7.3, if the content differs from what's on file, it's the
reconciliation case (same number, conflicting content, e.g. a resend with an
extra line item) and must be routed to reconciliation logic (T36), not
skipped as an already-processed repeat.
"""
from __future__ import annotations

import dataclasses
from enum import Enum

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .firestore_client import bills_collection
from .formats.schema import Bill, LineItem


class DuplicateStatus(str, Enum):
    NEW = "new"
    DUPLICATE = "duplicate"
    RECONCILIATION = "reconciliation"


@dataclasses.dataclass
class DuplicateCheckResult:
    status: DuplicateStatus
    matched_bill_id: str | None = None
    matched_bill_status: str | None = None


def _line_item_key(item: LineItem | dict) -> tuple:
    data = dataclasses.asdict(item) if isinstance(item, LineItem) else item
    return (
        data.get("item_name"),
        data.get("batch_no"),
        data.get("quantity"),
        data.get("rate"),
        data.get("taxable_value"),
        data.get("line_total"),
    )


def _content_fingerprint(total_amount, line_items) -> tuple:
    return (total_amount, tuple(sorted(_line_item_key(item) for item in line_items)))


def check_duplicate(bill: Bill, client: firestore.Client | None = None) -> DuplicateCheckResult:
    query = bills_collection(client).where(
        filter=FieldFilter("vendor", "==", bill.vendor)
    ).where(filter=FieldFilter("invoice_number", "==", bill.invoice_no))
    matches = list(query.stream())
    if not matches:
        return DuplicateCheckResult(status=DuplicateStatus.NEW)

    new_fingerprint = _content_fingerprint(bill.total_amount, bill.line_items)
    for doc in matches:
        data = doc.to_dict()
        existing_fingerprint = _content_fingerprint(data.get("total_amount"), data.get("line_items") or [])
        if existing_fingerprint == new_fingerprint:
            return DuplicateCheckResult(
                status=DuplicateStatus.DUPLICATE,
                matched_bill_id=doc.id,
                matched_bill_status=data.get("status"),
            )

    first = matches[0]
    return DuplicateCheckResult(
        status=DuplicateStatus.RECONCILIATION,
        matched_bill_id=first.id,
        matched_bill_status=first.to_dict().get("status"),
    )
