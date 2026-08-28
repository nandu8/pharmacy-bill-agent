import sys
from datetime import date
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from google.cloud.firestore_v1.base_query import FieldFilter

from pharmacy_agent.cross_check_other_vendors import CrossVendorSignal, cross_check_other_vendors
from pharmacy_agent.firestore_client import get_client, purchase_ledger_collection

import seed_purchase_ledger as seed  # noqa: E402  (needs SCRIPTS_DIR on sys.path first)


def _cleanup_vendor(vendor: str, client) -> None:
    query = purchase_ledger_collection(client).where(filter=FieldFilter("vendor", "==", vendor))
    for doc in query.stream():
        doc.reference.delete()


def test_seed_real_samples_writes_clean_unseeded_docs():
    client = get_client()
    try:
        count = seed.seed_real_samples(client=client)
        assert count > 0

        query = purchase_ledger_collection(client).where(
            filter=FieldFilter("vendor", "==", seed.FORMAT_C_VENDOR)
        )
        docs = [d.to_dict() for d in query.stream()]
        assert docs, "expected Northfield Associates docs from the real .xls samples"
        assert all(d["seeded"] is False for d in docs)

        # Re-running is idempotent (record_purchase's deterministic doc IDs) --
        # same vendor's doc count shouldn't grow.
        recount = seed.seed_real_samples(client=client)
        requery = list(
            purchase_ledger_collection(client)
            .where(filter=FieldFilter("vendor", "==", seed.FORMAT_C_VENDOR))
            .stream()
        )
        assert len(requery) == len([d for d in docs if True])
        assert recount == count
    finally:
        _cleanup_vendor(seed.FORMAT_C_VENDOR, client)
        _cleanup_vendor(seed.FORMAT_B_SAMPLE_VENDOR, client)
        _cleanup_vendor(seed.FORMAT_A_VENDOR, client)


def test_seed_synthetic_price_history_is_labelled_seeded():
    client = get_client()
    try:
        count = seed.seed_synthetic_price_history(client=client)
        assert count == len(seed.SYNTHETIC_HISTORY)

        query = purchase_ledger_collection(client).where(
            filter=FieldFilter("vendor", "==", seed.SYNTHETIC_VENDOR)
        )
        docs = [d.to_dict() for d in query.stream()]
        assert len(docs) == len(seed.SYNTHETIC_HISTORY)
        assert all(d["seeded"] is True for d in docs)
        assert all(d["normalized_item_key"] == "amlodipine_5mg" for d in docs)
    finally:
        _cleanup_vendor(seed.SYNTHETIC_VENDOR, client)


def test_seed_cross_vendor_stability_produces_no_movement_signal():
    # T59: the missing piece T25 flagged -- a second vendor's own stable
    # (non-moving) price history for the same item, dated relative to
    # today so the 60-day cross-vendor window (cross_check_other_vendors,
    # T33) is always satisfied whenever the demo actually gets recorded,
    # not just on the day this was written. Without this, Demo Bill 2's
    # cross-vendor check reports insufficient_data instead of the
    # conclusive no_movement signal PRD S7.5 condition 2 requires before an
    # autonomous dispute can fire.
    client = get_client()
    try:
        count = seed.seed_cross_vendor_stability(client=client)
        assert count == len(seed.OTHER_VENDOR_HISTORY)

        result = cross_check_other_vendors(
            seed.SYNTHETIC_VENDOR,
            seed._SYNTHETIC_ITEM,
            as_of_date=date.today().isoformat(),
            client=client,
        )
        assert result.signal == CrossVendorSignal.NO_MOVEMENT
        assert any(m.vendor == seed.OTHER_VENDOR for m in result.vendor_movements)

        # Idempotent re-run, same pattern as the other seed entry points.
        recount = seed.seed_cross_vendor_stability(client=client)
        assert recount == count
    finally:
        _cleanup_vendor(seed.OTHER_VENDOR, client)
