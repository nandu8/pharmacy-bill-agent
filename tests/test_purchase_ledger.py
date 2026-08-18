from google.cloud.firestore_v1.base_query import FieldFilter

from pharmacy_agent.firestore_client import get_client, purchase_ledger_collection
from pharmacy_agent.formats.schema import Bill, LineItem
from pharmacy_agent.purchase_ledger import ledger_doc_id, normalize_item_key, record_purchase


def _make_bill(vendor="Test Vendor", invoice_no="TEST-INV-001", n_items=2):
    items = [
        LineItem(
            vendor=vendor,
            invoice_no=invoice_no,
            invoice_date="2026-08-18",
            item_name=f"TEST ITEM {i}",
            batch_no=f"B{i}",
            expiry_date="2027-01-01",
            quantity=10.0,
            rate=5.0,
            discount=0.0,
            taxable_value=50.0,
            tax_component_1_label="CGST",
            tax_component_1_rate=6.0,
            tax_component_1_amount=3.0,
            tax_component_2_label="SGST",
            tax_component_2_rate=6.0,
            tax_component_2_amount=3.0,
            mrp=8.0,
            line_total=56.0,
            hsn_code="3004",
            source_format="format_a",
        )
        for i in range(n_items)
    ]
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-18",
        source_format="format_a",
        line_items=items,
        total_amount=112.0,
    )


def _cleanup(doc_ids):
    client = get_client()
    for doc_id in doc_ids:
        purchase_ledger_collection(client).document(doc_id).delete()


def test_normalize_item_key_is_stable_lowercase_slug():
    assert normalize_item_key("SILVEREX SSD CREAM 20GM") == "silverex_ssd_cream_20gm"
    assert normalize_item_key("  Extra   Spaces  ") == "extra_spaces"


def test_ledger_doc_id_is_deterministic():
    id1 = ledger_doc_id("Test Vendor", "TEST-INV-001", "TEST ITEM 0", "B0")
    id2 = ledger_doc_id("Test Vendor", "TEST-INV-001", "TEST ITEM 0", "B0")
    assert id1 == id2
    id3 = ledger_doc_id("Test Vendor", "TEST-INV-001", "TEST ITEM 0", "B1")
    assert id1 != id3


def test_record_purchase_writes_one_doc_per_line_item():
    bill = _make_bill(invoice_no="TEST-INV-RP-001")
    client = get_client()
    doc_ids = record_purchase(bill, client=client)
    try:
        assert len(doc_ids) == 2
        for doc_id, item in zip(doc_ids, bill.line_items):
            snapshot = purchase_ledger_collection(client).document(doc_id).get()
            assert snapshot.exists
            data = snapshot.to_dict()
            assert data == {
                "item_name": item.item_name,
                "normalized_item_key": normalize_item_key(item.item_name),
                "vendor": bill.vendor,
                "rate": item.rate,
                "mrp": item.mrp,
                "quantity": item.quantity,
                "batch_number": item.batch_no,
                "expiry_date": item.expiry_date,
                "invoice_no": bill.invoice_no,
                "purchase_date": bill.invoice_date,
                "seeded": False,
            }
    finally:
        _cleanup(doc_ids)


def test_record_purchase_marks_seeded_synthetic_history():
    # PRD S10: synthesized history beyond the real samples must be
    # distinguishable in the data itself, not just presented as observed.
    bill = _make_bill(invoice_no="TEST-INV-RP-003")
    client = get_client()
    doc_ids = record_purchase(bill, client=client, seeded=True)
    try:
        for doc_id in doc_ids:
            data = purchase_ledger_collection(client).document(doc_id).get().to_dict()
            assert data["seeded"] is True
    finally:
        _cleanup(doc_ids)


def test_record_purchase_is_idempotent_on_retry():
    bill = _make_bill(invoice_no="TEST-INV-RP-002")
    client = get_client()
    first_ids = record_purchase(bill, client=client)
    try:
        second_ids = record_purchase(bill, client=client)
        assert sorted(first_ids) == sorted(second_ids)

        collection = purchase_ledger_collection(client)
        matching = [
            doc
            for doc in collection.where(filter=FieldFilter("invoice_no", "==", bill.invoice_no)).stream()
        ]
        assert len(matching) == len(bill.line_items)
    finally:
        _cleanup(first_ids)
