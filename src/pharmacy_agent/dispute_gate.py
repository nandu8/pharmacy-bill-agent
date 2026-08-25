"""Dispute gating (PRD S7.5 / T39): the four conditions an autonomous
dispute must clear before email_vendor (T38) is allowed to fire in dispute
mode.

Wraps the price-deviation (T34) and cross-vendor (T33) signals, an amount
floor, and a duplicate-dispute check into a single authorize/deny decision
with named reasons, so the agent makes one lookup instead of re-deriving
four scattered checks. This module only decides yes/no and why -- it never
sends anything itself. If any condition fails, PRD S7.5 says the agent
falls back to ask_pharmacist with its findings attached; `failed_conditions`
is exactly that findings list.

Condition 4 (no dispute already sent for this invoice) reads the same
`email_log` collection email_vendor's own dedup writes to (T38), so the two
checks can never disagree about whether a dispute already went out.
"""
from __future__ import annotations

import dataclasses

from google.cloud import firestore

from .check_price_deviation import PriceDeviationResult, PriceDeviationSignal
from .cross_check_other_vendors import CrossVendorCheckResult, CrossVendorSignal
from .email_vendor import EMAIL_LOG_COLLECTION, email_log_doc_id
from .firestore_client import get_client

# PRD S7.5 condition 3: small deviations are logged, not disputed.
DEFAULT_AMOUNT_FLOOR = 500.0


@dataclasses.dataclass
class DisputeGateResult:
    authorized: bool
    failed_conditions: list[str]


def check_dispute_gate(
    vendor: str,
    invoice_no: str,
    price_deviation: PriceDeviationResult,
    cross_vendor_check: CrossVendorCheckResult,
    disputed_amount: float,
    amount_floor: float = DEFAULT_AMOUNT_FLOOR,
    client: firestore.Client | None = None,
) -> DisputeGateResult:
    client = client or get_client()
    failed: list[str] = []

    # Condition 1: deviation confirmed against >=3 prior invoices.
    if price_deviation.signal != PriceDeviationSignal.DEVIATION_DETECTED:
        failed.append("no_deviation_detected")
    elif not price_deviation.confirmed:
        failed.append("unconfirmed_deviation")

    # Condition 2: cross_check_other_vendors must be conclusively "no
    # market movement" -- MARKET_MOVEMENT and INSUFFICIENT_DATA are both
    # inconclusive for a dispute, for different reasons, but the gate
    # treats them the same: not cleared to dispute autonomously.
    if cross_vendor_check.signal != CrossVendorSignal.NO_MOVEMENT:
        failed.append("cross_vendor_check_not_conclusive")

    # Condition 3: disputed amount must exceed the value floor.
    if abs(disputed_amount) <= amount_floor:
        failed.append("below_amount_floor")

    # Condition 4: no dispute already sent for this invoice.
    doc_id = email_log_doc_id(vendor, invoice_no, "dispute")
    if client.collection(EMAIL_LOG_COLLECTION).document(doc_id).get().exists:
        failed.append("dispute_already_sent")

    return DisputeGateResult(authorized=not failed, failed_conditions=failed)
