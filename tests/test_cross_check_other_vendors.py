from pharmacy_agent.cross_check_other_vendors import CrossVendorSignal, cross_check_other_vendors
from pharmacy_agent.firestore_client import get_client, purchase_ledger_collection
from pharmacy_agent.formats.schema import Bill, LineItem
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
    for doc_id in doc_ids:
        purchase_ledger_collection(client).document(doc_id).delete()


def test_cross_check_returns_insufficient_data_with_no_other_vendor_history():
    client = get_client()
    result = cross_check_other_vendors(
        "CC No History Vendor", "AMLODIPINE 5MG", as_of_date="2026-06-01", client=client
    )
    assert result.signal == CrossVendorSignal.INSUFFICIENT_DATA
    assert result.vendor_movements == []


def test_cross_check_detects_market_wide_movement():
    client = get_client()
    bills = [
        _make_bill("CC Vendor A", "CC-A-001", "2026-05-15", rate=6.5, batch_no="A1"),
        _make_bill("CC Vendor B", "CC-B-001", "2026-04-15", rate=5.0, batch_no="B1"),
        _make_bill("CC Vendor B", "CC-B-002", "2026-05-15", rate=6.5, batch_no="B2"),
        _make_bill("CC Vendor C", "CC-C-001", "2026-04-15", rate=5.0, batch_no="C1"),
        _make_bill("CC Vendor C", "CC-C-002", "2026-05-15", rate=6.5, batch_no="C2"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        result = cross_check_other_vendors(
            "CC Vendor A", "AMLODIPINE 5MG", as_of_date="2026-06-01", client=client
        )
        assert result.signal == CrossVendorSignal.MARKET_MOVEMENT
        moved_vendors = {m.vendor for m in result.vendor_movements if m.moved}
        assert moved_vendors == {"CC Vendor B", "CC Vendor C"}
        assert "CC Vendor A" not in {m.vendor for m in result.vendor_movements}
    finally:
        _cleanup(doc_ids, client)


def test_cross_check_detects_no_movement():
    client = get_client()
    bills = [
        _make_bill("CC Vendor E", "CC-E-001", "2026-04-15", rate=5.0, batch_no="E1"),
        _make_bill("CC Vendor E", "CC-E-002", "2026-05-15", rate=5.05, batch_no="E2"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        result = cross_check_other_vendors(
            "CC Vendor Under Investigation", "AMLODIPINE 5MG", as_of_date="2026-06-01", client=client
        )
        assert result.signal == CrossVendorSignal.NO_MOVEMENT
        assert len(result.vendor_movements) == 1
        assert result.vendor_movements[0].moved is False
    finally:
        _cleanup(doc_ids, client)


def test_cross_check_ignores_entries_outside_window():
    client = get_client()
    bills = [
        _make_bill("CC Vendor F", "CC-F-001", "2026-01-01", rate=5.0, batch_no="F1"),
        _make_bill("CC Vendor F", "CC-F-002", "2026-05-15", rate=9.0, batch_no="F2"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        result = cross_check_other_vendors(
            "CC Vendor Under Investigation",
            "AMLODIPINE 5MG",
            as_of_date="2026-06-01",
            window_days=60,
            client=client,
        )
        # Only the 2026-05-15 entry falls inside the 60-day window ending
        # 2026-06-01 -- a single point in-window can't show movement, so
        # this vendor contributes no movement entry at all.
        assert result.signal == CrossVendorSignal.INSUFFICIENT_DATA
        assert result.vendor_movements == []
    finally:
        _cleanup(doc_ids, client)
