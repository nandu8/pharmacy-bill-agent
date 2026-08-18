import dataclasses
import uuid

from pharmacy_agent.check_duplicate import DuplicateStatus, check_duplicate
from pharmacy_agent.firestore_client import bills_collection, get_client
from pharmacy_agent.formats.schema import Bill, LineItem


def _make_line_item(item_name="TEST ITEM", quantity=10.0, rate=5.0):
    return LineItem(
        vendor="Test Vendor",
        invoice_no="TEST-INV-CD-001",
        invoice_date="2026-08-18",
        item_name=item_name,
        batch_no="B1",
        expiry_date="2027-01-01",
        quantity=quantity,
        rate=rate,
        discount=0.0,
        taxable_value=quantity * rate,
        tax_component_1_label="CGST",
        tax_component_1_rate=6.0,
        tax_component_1_amount=3.0,
        tax_component_2_label="SGST",
        tax_component_2_rate=6.0,
        tax_component_2_amount=3.0,
        mrp=8.0,
        line_total=quantity * rate + 6.0,
        hsn_code="3004",
        source_format="format_a",
    )


def _make_bill(line_items, total_amount, invoice_no="TEST-INV-CD-001", vendor="Test Vendor"):
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-18",
        source_format="format_a",
        line_items=line_items,
        total_amount=total_amount,
    )


def _write_bill_doc(client, vendor, invoice_number, total_amount, line_items, status="resolved"):
    doc_id = f"test-bill-{uuid.uuid4().hex}"
    payload = {
        "bill_id": doc_id,
        "vendor": vendor,
        "invoice_number": invoice_number,
        "invoice_date": "2026-08-18",
        "line_items": [dataclasses.asdict(item) for item in line_items],
        "total_amount": total_amount,
        "status": status,
        "findings": [],
        "resolution_history": [],
        "dispute_sent": False,
        "drive_file_url": None,
        "trace_id": None,
    }
    bills_collection(client).document(doc_id).set(payload)
    return doc_id


def test_check_duplicate_returns_new_when_no_match():
    client = get_client()
    bill = _make_bill([_make_line_item()], total_amount=56.0, invoice_no="TEST-INV-CD-NONE")
    result = check_duplicate(bill, client=client)
    assert result.status == DuplicateStatus.NEW
    assert result.matched_bill_id is None


def test_check_duplicate_returns_duplicate_for_identical_content():
    client = get_client()
    items = [_make_line_item()]
    doc_id = _write_bill_doc(client, "Test Vendor", "TEST-INV-CD-DUP", 56.0, items, status="resolved")
    try:
        bill = _make_bill(items, total_amount=56.0, invoice_no="TEST-INV-CD-DUP")
        result = check_duplicate(bill, client=client)
        assert result.status == DuplicateStatus.DUPLICATE
        assert result.matched_bill_id == doc_id
        assert result.matched_bill_status == "resolved"
    finally:
        bills_collection(client).document(doc_id).delete()


def test_check_duplicate_returns_reconciliation_for_different_content():
    client = get_client()
    existing_items = [_make_line_item(item_name="ITEM A", quantity=10.0, rate=5.0)]
    doc_id = _write_bill_doc(client, "Test Vendor", "TEST-INV-CD-RECON", 56.0, existing_items)
    try:
        new_items = [
            _make_line_item(item_name="ITEM A", quantity=10.0, rate=5.0),
            _make_line_item(item_name="ITEM B", quantity=2.0, rate=20.0),
        ]
        bill = _make_bill(new_items, total_amount=102.0, invoice_no="TEST-INV-CD-RECON")
        result = check_duplicate(bill, client=client)
        assert result.status == DuplicateStatus.RECONCILIATION
        assert result.matched_bill_id == doc_id
    finally:
        bills_collection(client).document(doc_id).delete()
