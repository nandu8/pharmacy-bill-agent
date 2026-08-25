"""check_vendor_silence (PRD S7.8/S5 / T37): has this vendor gone unusually
quiet relative to its own established billing cadence?

This is the one proactive check that survives the purchase-ledger-only
scope decision (S5) -- billing cadence is purchase-side data the agent
already has, no stock levels required. It runs "on a Cloud Scheduler
cadence, independent of any incoming email" (S7.8): unlike every other
check in this module (S7.4), it has no incoming bill to react to, so it is
not part of the per-bill agent loop. This module is the detection logic a
scheduled job would call, one vendor at a time; actually deploying the
Cloud Scheduler trigger that iterates vendors and calls it is deploy/infra
work (no different in kind from T50's "install Drive Desktop sync"), not
covered here.

Cadence is estimated from the gaps between this vendor's own past invoice
dates (median gap, robust to one unusually fast or slow reorder), then
compared against how long it's actually been since the last one.
"""
from __future__ import annotations

import dataclasses
import statistics
from datetime import date
from enum import Enum

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .firestore_client import purchase_ledger_collection

DEFAULT_MIN_INVOICES = 3
# More than this many multiples of the vendor's own typical gap since the
# last invoice counts as "gone quiet" -- a small overrun is normal jitter.
DEFAULT_THRESHOLD_MULTIPLIER = 2.0


class VendorSilenceSignal(str, Enum):
    SILENT = "silent"
    ON_CADENCE = "on_cadence"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclasses.dataclass
class VendorSilenceResult:
    signal: VendorSilenceSignal
    vendor: str
    last_invoice_date: str | None
    days_since_last_invoice: int | None
    typical_gap_days: float | None
    invoice_count: int


def check_vendor_silence(
    vendor: str,
    as_of_date: str,
    min_invoices: int = DEFAULT_MIN_INVOICES,
    threshold_multiplier: float = DEFAULT_THRESHOLD_MULTIPLIER,
    client: firestore.Client | None = None,
) -> VendorSilenceResult:
    query = purchase_ledger_collection(client).where(filter=FieldFilter("vendor", "==", vendor))
    invoice_dates: set[date] = set()
    for doc in query.stream():
        data = doc.to_dict()
        purchase_date = data.get("purchase_date")
        if purchase_date:
            invoice_dates.add(date.fromisoformat(purchase_date))

    if not invoice_dates:
        return VendorSilenceResult(
            signal=VendorSilenceSignal.INSUFFICIENT_DATA,
            vendor=vendor,
            last_invoice_date=None,
            days_since_last_invoice=None,
            typical_gap_days=None,
            invoice_count=0,
        )

    sorted_dates = sorted(invoice_dates)
    last = sorted_dates[-1]
    days_since_last = (date.fromisoformat(as_of_date) - last).days

    if len(sorted_dates) < min_invoices:
        return VendorSilenceResult(
            signal=VendorSilenceSignal.INSUFFICIENT_DATA,
            vendor=vendor,
            last_invoice_date=last.isoformat(),
            days_since_last_invoice=days_since_last,
            typical_gap_days=None,
            invoice_count=len(sorted_dates),
        )

    gaps = [(sorted_dates[i] - sorted_dates[i - 1]).days for i in range(1, len(sorted_dates))]
    typical_gap = statistics.median(gaps)
    silent = typical_gap > 0 and days_since_last > typical_gap * threshold_multiplier

    return VendorSilenceResult(
        signal=VendorSilenceSignal.SILENT if silent else VendorSilenceSignal.ON_CADENCE,
        vendor=vendor,
        last_invoice_date=last.isoformat(),
        days_since_last_invoice=days_since_last,
        typical_gap_days=typical_gap,
        invoice_count=len(sorted_dates),
    )
