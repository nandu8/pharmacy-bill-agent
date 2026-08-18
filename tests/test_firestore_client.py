import uuid

from pharmacy_agent.firestore_client import (
    AGENT_RUNS_COLLECTION,
    BILLS_COLLECTION,
    PURCHASE_LEDGER_COLLECTION,
    agent_runs_collection,
    bills_collection,
    purchase_ledger_collection,
)


def _roundtrip(collection_ref, payload):
    # Firestore collections are virtual -- they exist only while they hold a
    # document -- so a write/read/delete against each one is both the proof
    # of connectivity and the closest thing to "creating" it (T21).
    doc_id = f"test-{uuid.uuid4().hex}"
    doc_ref = collection_ref.document(doc_id)
    doc_ref.set(payload)
    try:
        snapshot = doc_ref.get()
        assert snapshot.exists
        assert snapshot.to_dict() == payload
    finally:
        doc_ref.delete()


def test_bills_collection_roundtrip():
    payload = {
        "bill_id": "test-bill",
        "vendor": "Test Vendor",
        "invoice_number": "INV-001",
        "invoice_date": "2026-08-18",
        "line_items": [],
        "total_amount": 100.0,
        "status": "resolved",
        "findings": [],
        "resolution_history": [],
        "dispute_sent": False,
        "drive_file_url": None,
        "trace_id": None,
    }
    _roundtrip(bills_collection(), payload)


def test_purchase_ledger_collection_roundtrip():
    payload = {
        "item_name": "TEST ITEM",
        "normalized_item_key": "test_item",
        "vendor": "Test Vendor",
        "rate": 10.0,
        "mrp": 15.0,
        "quantity": 1.0,
        "batch_number": "B1",
        "expiry_date": "2027-01-01",
        "invoice_no": "INV-001",
        "purchase_date": "2026-08-18",
    }
    _roundtrip(purchase_ledger_collection(), payload)


def test_agent_runs_collection_roundtrip():
    payload = {
        "bill_id": "test-bill",
        "correlation_key": "corr-1",
        "serialized_state": {},
        "tool_call_history": [],
        "open_question": None,
        "paused_at": None,
        "resumed_at": None,
    }
    _roundtrip(agent_runs_collection(), payload)


def test_collection_names_match_prd():
    assert BILLS_COLLECTION == "bills"
    assert PURCHASE_LEDGER_COLLECTION == "purchase_ledger"
    assert AGENT_RUNS_COLLECTION == "agent_runs"
