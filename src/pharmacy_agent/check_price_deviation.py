"""check_price_deviation (PRD S7.2/S7.4/S7.5 / T34): this vendor's current
rate vs. its own prior purchase history for the same item -- the
"requires accumulated or seeded history" check PRD S7.4 lists ahead of
cross_check_other_vendors (T33).

lookup_vendor_history (T24) supplies the raw prior entries; this tool turns
them into a signal by comparing the current rate against the most recent
prior invoice ("27% above last month", PRD S7.4 worked examples). It also
reports whether the deviation is `confirmed` -- backed by >=3 prior invoices,
the threshold PRD S7.5 condition 1 requires before an autonomous dispute can
fire on it -- so a thin-history deviation still surfaces but downstream
dispute-gating (T39) knows not to act on it alone.

PRD S7.7 (T58): a standing pharmacist resolution for this vendor+item
(pharmacist_resolutions.py) adjusts the signal -- an APPROVED resolution at
or above the current rate downgrades what would otherwise be a confirmed
deviation to WITHIN_NORMAL ("the agent stops flagging it"); a REJECTED one
halves the detection threshold for this vendor+item ("weighted more heavily
next time"), a hard, checkable rule rather than asking the model to recall
and apply a past decision itself.
"""
from __future__ import annotations

import dataclasses
from enum import Enum

from google.cloud import firestore

from .lookup_vendor_history import VendorHistoryEntry, lookup_vendor_history
from .pharmacist_resolutions import (
    PharmacistDecision,
    PharmacistResolution,
    lookup_pharmacist_resolution,
)

# A rate move of 10%+ vs. the most recent prior invoice is a deviation worth
# investigating further (cross_check_other_vendors, T33) -- below this is
# normal month-to-month variation. PRD S7.4 worked examples cite 26-27%
# moves as the deviations that trigger investigation.
DEFAULT_DEVIATION_THRESHOLD = 0.10

# PRD S7.7: a REJECTED resolution halves the threshold for this vendor+item
# ("weighted more heavily next time") rather than requiring an even larger
# move to trigger investigation.
REJECTED_THRESHOLD_MULTIPLIER = 0.5

# PRD S7.5 condition 1: an autonomous dispute needs >=3 prior invoices from
# this vendor for this item to confirm the deviation isn't a one-off.
MIN_PRIOR_INVOICES_FOR_CONFIRMATION = 3


class PriceDeviationSignal(str, Enum):
    NO_HISTORY = "no_history"
    WITHIN_NORMAL = "within_normal"
    DEVIATION_DETECTED = "deviation_detected"


@dataclasses.dataclass
class PriceDeviationResult:
    signal: PriceDeviationSignal
    current_rate: float
    reference_rate: float | None
    pct_change: float | None
    prior_invoice_count: int
    confirmed: bool
    history: list[VendorHistoryEntry]
    resolution: PharmacistResolution | None = None


def check_price_deviation(
    vendor: str,
    item_name: str,
    current_rate: float,
    threshold: float = DEFAULT_DEVIATION_THRESHOLD,
    history_limit: int = 4,
    client: firestore.Client | None = None,
) -> PriceDeviationResult:
    history = lookup_vendor_history(vendor, item_name, client=client, limit=history_limit)
    prior_invoice_count = len(history)
    confirmed = prior_invoice_count >= MIN_PRIOR_INVOICES_FOR_CONFIRMATION
    resolution = lookup_pharmacist_resolution(vendor, item_name, client=client)

    if not history:
        return PriceDeviationResult(
            signal=PriceDeviationSignal.NO_HISTORY,
            current_rate=current_rate,
            reference_rate=None,
            pct_change=None,
            prior_invoice_count=0,
            confirmed=False,
            history=[],
            resolution=resolution,
        )

    reference_rate = history[0].rate  # most recent prior invoice ("last month")
    pct_change = (current_rate - reference_rate) / reference_rate

    effective_threshold = threshold
    if resolution is not None and resolution.decision == PharmacistDecision.REJECTED:
        effective_threshold = threshold * REJECTED_THRESHOLD_MULTIPLIER

    signal = (
        PriceDeviationSignal.DEVIATION_DETECTED
        if abs(pct_change) >= effective_threshold
        else PriceDeviationSignal.WITHIN_NORMAL
    )

    if (
        signal == PriceDeviationSignal.DEVIATION_DETECTED
        and resolution is not None
        and resolution.decision == PharmacistDecision.APPROVED
        and current_rate <= resolution.rate
    ):
        signal = PriceDeviationSignal.WITHIN_NORMAL

    return PriceDeviationResult(
        signal=signal,
        current_rate=current_rate,
        reference_rate=reference_rate,
        pct_change=pct_change,
        prior_invoice_count=prior_invoice_count,
        confirmed=confirmed,
        history=history,
        resolution=resolution,
    )
