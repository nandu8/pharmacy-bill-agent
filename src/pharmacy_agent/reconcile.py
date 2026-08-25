"""reconcile (PRD S7.3/S7.4 / T36): same invoice number, conflicting content.

Not a parse-failure recovery (that's find_related_document's other caller,
S7.3 step 3) -- this fires once check_duplicate has already returned
RECONCILIATION (same vendor + invoice_no on file, different line items or
total). The vendor's own PDF is legally the authoritative GST document
(S7.3), so this module locates it among whatever candidate documents the
run has (other attachments from the same email / already-staged files),
treats it as ground truth, and produces a human-readable discrepancy so the
pharmacist notice / dispute email (T38/T41 -- not wired yet) has something
concrete to cite. It does not itself stage or email anything -- Drive
staging (T49) and messaging (T38/T41) are later-phase infra; this returns
the decision for the loop to act on once those exist.

Matching by normalized item key (purchase_ledger.normalize_item_key) plus
batch_no, not raw item_name equality: the authoritative bill may come from
parse_pdf_vision, a separate Gemini read that won't reproduce a CSV's exact
text formatting even when it's describing the same line.
"""
from __future__ import annotations

import dataclasses

from .find_related_document import find_related_document
from .formats import detect as detect_mod
from .formats.schema import Bill, LineItem
from .purchase_ledger import normalize_item_key


@dataclasses.dataclass
class ReconciliationResult:
    authoritative_found: bool
    authoritative_bill: Bill | None
    authoritative_source: str | None  # "in_hand", or the matched candidate's filename
    matches_bill_in_hand: bool
    discrepancies: list[str]


def _item_key(item: LineItem) -> tuple:
    return (normalize_item_key(item.item_name), item.batch_no.strip().upper())


def _diff_bills(bill: Bill, authoritative: Bill) -> list[str]:
    current_by_key = {_item_key(li): li for li in bill.line_items}
    auth_by_key = {_item_key(li): li for li in authoritative.line_items}
    diffs: list[str] = []

    for key in sorted(auth_by_key.keys() - current_by_key.keys()):
        li = auth_by_key[key]
        diffs.append(
            f"authoritative PDF has a line not in the bill on hand: "
            f"{li.item_name} (batch {li.batch_no}), qty {li.quantity} @ {li.rate} = {li.line_total}"
        )
    for key in sorted(current_by_key.keys() - auth_by_key.keys()):
        li = current_by_key[key]
        diffs.append(
            f"bill on hand has a line the authoritative PDF does not: "
            f"{li.item_name} (batch {li.batch_no}), qty {li.quantity} @ {li.rate} = {li.line_total}"
        )
    for key in sorted(current_by_key.keys() & auth_by_key.keys()):
        cur = current_by_key[key]
        auth = auth_by_key[key]
        if (cur.quantity, cur.rate, cur.taxable_value, cur.line_total) != (
            auth.quantity,
            auth.rate,
            auth.taxable_value,
            auth.line_total,
        ):
            diffs.append(
                f"{cur.item_name} (batch {cur.batch_no}): bill on hand has qty {cur.quantity} @ "
                f"{cur.rate} = {cur.line_total}, authoritative PDF has qty {auth.quantity} @ "
                f"{auth.rate} = {auth.line_total}"
            )

    if bill.total_amount != authoritative.total_amount:
        diffs.append(
            f"total_amount differs: bill on hand {bill.total_amount}, authoritative PDF {authoritative.total_amount}"
        )
    return diffs


def reconcile(bill: Bill, candidates: list[tuple[str, bytes]]) -> ReconciliationResult:
    """Resolve a same-invoice-number/conflicting-content case for `bill`
    against `candidates` (other documents already available to this run --
    other email attachments, already-staged files). If `bill` is itself the
    PDF, it's already authoritative. Otherwise, searches `candidates` for
    the vendor's PDF twin (via find_related_document) and diffs `bill`
    against it. If no PDF twin is among the candidates, authoritative_found
    is False -- the caller (agent loop) falls back to ask_pharmacist,
    same as the S7.3 recovery ladder's last resort.
    """
    if bill.source_format == "format_d":
        return ReconciliationResult(
            authoritative_found=True,
            authoritative_bill=bill,
            authoritative_source="in_hand",
            matches_bill_in_hand=True,
            discrepancies=[],
        )

    match = find_related_document(bill.invoice_no, bill.vendor, candidates)
    if match is None or match.detected_format != detect_mod.FORMAT_D_PDF:
        return ReconciliationResult(
            authoritative_found=False,
            authoritative_bill=None,
            authoritative_source=None,
            matches_bill_in_hand=False,
            discrepancies=[],
        )

    diffs = _diff_bills(bill, match.bill)
    return ReconciliationResult(
        authoritative_found=True,
        authoritative_bill=match.bill,
        authoritative_source=match.filename,
        matches_bill_in_hand=not diffs,
        discrepancies=diffs,
    )
