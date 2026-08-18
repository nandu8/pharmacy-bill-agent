from pharmacy_agent.firestore_client import get_client
from pharmacy_agent.formats.schema import Bill, LineItem
from pharmacy_agent.lookup_vendor_history import lookup_vendor_history
from pharmacy_agent.purchase_ledger import record_purchase


def _make_bill(vendor, invoice_no, purchase_date, item_name="AMLODIPINE 5MG", rate=5.0, batch_no="B1"):
    item = LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=purchase_date,
        item_name=item_name,
        batch_no=batch_no,
        expiry_date="2027-01-01",
        quantity=10.0,
        rate=rate,
        discount=0.0,
        taxable_value=rate * 10.0,
        tax_component_1_label="CGST",
        tax_component_1_rate=6.0,
        tax_component_1_amount=3.0,
        tax_component_2_label="SGST",
        tax_component_2_rate=6.0,
        tax_component_2_amount=3.0,
        mrp=8.0,
        line_total=rate * 10.0 + 6.0,
        hsn_code="3004",
        source_format="format_a",
    )
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=purchase_date,
        source_format="format_a",
        line_items=[item],
        total_amount=item.line_total,
    )


def _cleanup(doc_ids, client):
    from pharmacy_agent.firestore_client import purchase_ledger_collection

    for doc_id in doc_ids:
        purchase_ledger_collection(client).document(doc_id).delete()


def test_lookup_vendor_history_returns_empty_when_no_history():
    client = get_client()
    result = lookup_vendor_history("No Such Vendor", "NOTHING HERE", client=client)
    assert result == []


def test_lookup_vendor_history_matches_vendor_and_item_only():
    client = get_client()
    bill_match = _make_bill("VH Vendor", "VH-INV-001", "2026-06-01")
    bill_other_vendor = _make_bill("VH Other Vendor", "VH-INV-002", "2026-06-01")
    bill_other_item = _make_bill("VH Vendor", "VH-INV-003", "2026-06-01", item_name="PARACETAMOL 500MG")
    doc_ids = []
    try:
        doc_ids += record_purchase(bill_match, client=client)
        doc_ids += record_purchase(bill_other_vendor, client=client)
        doc_ids += record_purchase(bill_other_item, client=client)

        result = lookup_vendor_history("VH Vendor", "AMLODIPINE 5MG", client=client)
        assert len(result) == 1
        assert result[0].invoice_no == "VH-INV-001"
        assert result[0].vendor == "VH Vendor"
    finally:
        _cleanup(doc_ids, client)


def test_lookup_vendor_history_sorted_most_recent_first():
    client = get_client()
    bills = [
        _make_bill("VH Sort Vendor", "VH-SORT-001", "2026-01-01", rate=5.0, batch_no="B1"),
        _make_bill("VH Sort Vendor", "VH-SORT-002", "2026-03-01", rate=6.0, batch_no="B2"),
        _make_bill("VH Sort Vendor", "VH-SORT-003", "2026-02-01", rate=5.5, batch_no="B3"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        result = lookup_vendor_history("VH Sort Vendor", "AMLODIPINE 5MG", client=client)
        assert [entry.invoice_no for entry in result] == ["VH-SORT-002", "VH-SORT-003", "VH-SORT-001"]
        assert [entry.purchase_date for entry in result] == ["2026-03-01", "2026-02-01", "2026-01-01"]
    finally:
        _cleanup(doc_ids, client)


def test_lookup_vendor_history_respects_limit():
    client = get_client()
    bills = [
        _make_bill("VH Limit Vendor", f"VH-LIMIT-{i:03d}", f"2026-01-{i:02d}", batch_no=f"B{i}")
        for i in range(1, 6)
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        result = lookup_vendor_history("VH Limit Vendor", "AMLODIPINE 5MG", client=client, limit=4)
        assert len(result) == 4
        assert [entry.invoice_no for entry in result] == [
            "VH-LIMIT-005",
            "VH-LIMIT-004",
            "VH-LIMIT-003",
            "VH-LIMIT-002",
        ]
    finally:
        _cleanup(doc_ids, client)
