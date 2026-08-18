import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from google.cloud.firestore_v1.base_query import FieldFilter

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
        assert docs, "expected Bruklyn Associates docs from the real .xls samples"
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
