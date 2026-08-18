"""cross_check_other_vendors (PRD S7.2/S7.4/S7.5 / T33): same molecule,
other vendors -- distinguishes a market-wide price shift from a single
vendor's own pricing error.

`lookup_vendor_history` (T24) already confirms a rate rise is real for the
vendor under investigation. This tool answers the follow-up question: did
*other* vendors move on the same molecule in the same window? Two or more
raising it -> market movement (auto-approve, PRD S7.4 worked example). No
other vendor moving -> the "conclusive no market movement" signal that PRD
S7.5 condition 2 requires before an autonomous dispute can fire.
"""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta
from enum import Enum

from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from .firestore_client import purchase_ledger_collection
from .purchase_ledger import normalize_item_key

DEFAULT_WINDOW_DAYS = 60
# A same-molecule rate rise of 5%+ at another vendor, within the window,
# counts as that vendor having "moved" -- below this is noise/rounding.
DEFAULT_MOVEMENT_THRESHOLD = 0.05


class CrossVendorSignal(str, Enum):
    MARKET_MOVEMENT = "market_movement"
    NO_MOVEMENT = "no_movement"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclasses.dataclass
class VendorRateMovement:
    vendor: str
    earliest_date: str
    earliest_rate: float
    latest_date: str
    latest_rate: float
    pct_change: float
    moved: bool


@dataclasses.dataclass
class CrossVendorCheckResult:
    signal: CrossVendorSignal
    vendor_movements: list[VendorRateMovement]


def cross_check_other_vendors(
    vendor: str,
    item_name: str,
    as_of_date: str,
    window_days: int = DEFAULT_WINDOW_DAYS,
    movement_threshold: float = DEFAULT_MOVEMENT_THRESHOLD,
    client: firestore.Client | None = None,
) -> CrossVendorCheckResult:
    key = normalize_item_key(item_name)
    window_start = (date.fromisoformat(as_of_date) - timedelta(days=window_days)).isoformat()

    query = purchase_ledger_collection(client).where(
        filter=FieldFilter("normalized_item_key", "==", key)
    )
    by_vendor: dict[str, list[dict]] = {}
    for doc in query.stream():
        data = doc.to_dict()
        if data.get("vendor") == vendor:
            continue
        purchase_date = data.get("purchase_date") or ""
        if not (window_start <= purchase_date <= as_of_date):
            continue
        by_vendor.setdefault(data["vendor"], []).append(data)

    movements: list[VendorRateMovement] = []
    for other_vendor, entries in by_vendor.items():
        # Need at least two priced points inside the window to tell whether
        # this vendor moved -- a single in-window entry has no baseline.
        if len(entries) < 2:
            continue
        entries.sort(key=lambda e: e["purchase_date"])
        earliest, latest = entries[0], entries[-1]
        if earliest["rate"] <= 0:
            continue
        pct_change = (latest["rate"] - earliest["rate"]) / earliest["rate"]
        movements.append(VendorRateMovement(
            vendor=other_vendor,
            earliest_date=earliest["purchase_date"],
            earliest_rate=earliest["rate"],
            latest_date=latest["purchase_date"],
            latest_rate=latest["rate"],
            pct_change=pct_change,
            moved=pct_change >= movement_threshold,
        ))

    if not movements:
        signal = CrossVendorSignal.INSUFFICIENT_DATA
    elif any(m.moved for m in movements):
        signal = CrossVendorSignal.MARKET_MOVEMENT
    else:
        signal = CrossVendorSignal.NO_MOVEMENT

    return CrossVendorCheckResult(signal=signal, vendor_movements=movements)
