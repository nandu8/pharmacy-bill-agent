"""Per-line arithmetic validator (PRD S7.4 / T20).

Checks *every line*, not just the invoice footer:
    taxable_value = quantity * rate - discount
    line_total    = taxable_value + tax_component_1_amount + tax_component_2_amount

Checking only quantity*rate=total is wrong for real pharma bills -- every
sample format carries a per-line discount and a two-component tax split
(see normalize.py), and a naive check misfires on every clean invoice.
Verified against real samples, e.g. 148.57 taxable + 3.71 CGST + 3.71 SGST
= 155.99 line amount (PRD S7.4).
"""
from __future__ import annotations

from dataclasses import dataclass

from .formats.schema import Bill, LineItem

DEFAULT_TOLERANCE = 0.02  # rupees; absorbs per-line rounding


@dataclass
class ValidationIssue:
    scope: str  # "line" | "bill"
    item_index: int | None
    message: str


def validate_line(item: LineItem, tolerance: float = DEFAULT_TOLERANCE) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    expected_taxable = round(item.quantity * item.rate - item.discount, 2)
    if abs(expected_taxable - item.taxable_value) > tolerance:
        issues.append(ValidationIssue(
            "line", None,
            f"{item.item_name}: taxable_value {item.taxable_value} != "
            f"qty*rate-discount {expected_taxable}",
        ))

    expected_total = round(
        item.taxable_value + item.tax_component_1_amount + item.tax_component_2_amount, 2
    )
    if abs(expected_total - item.line_total) > tolerance:
        issues.append(ValidationIssue(
            "line", None,
            f"{item.item_name}: line_total {item.line_total} != "
            f"taxable_value+tax1+tax2 {expected_total}",
        ))

    return issues


def validate_bill(bill: Bill, tolerance: float = DEFAULT_TOLERANCE) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for i, item in enumerate(bill.line_items):
        for issue in validate_line(item, tolerance):
            issues.append(ValidationIssue(issue.scope, i, issue.message))

    if bill.total_amount is not None:
        summed = round(sum(i.line_total for i in bill.line_items), 2)
        # bill.total_amount is the invoice's own footer/header figure --
        # confirmed against real samples to be the tax-inclusive grand
        # total (e.g. "Payable Amt"), so it's compared against the sum of
        # line_total, not taxable_value. A wider, item-count-scaled
        # tolerance is used here because the real invoices carry their own
        # invoice-level rounding adjustment (seen as small as -0.27 to
        # -0.33 across samples) that per-line tolerance doesn't cover.
        if abs(summed - bill.total_amount) > max(tolerance * len(bill.line_items), 0.5):
            issues.append(ValidationIssue(
                "bill", None,
                f"sum of line_total {summed} != invoice total_amount {bill.total_amount}",
            ))
    return issues
