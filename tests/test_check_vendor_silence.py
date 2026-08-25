from pharmacy_agent.check_vendor_silence import VendorSilenceSignal, check_vendor_silence
from pharmacy_agent.firestore_client import get_client, purchase_ledger_collection
from pharmacy_agent.formats.schema import Bill, LineItem
from pharmacy_agent.purchase_ledger import record_purchase


def _make_bill(vendor, invoice_no, purchase_date, batch_no="B1"):
    item = LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=purchase_date,
        item_name="AMLODIPINE 5MG",
        batch_no=batch_no,
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
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=purchase_date,
        source_format="format_a",
        line_items=[item],
        total_amount=56.0,
    )


def _cleanup(doc_ids, client):
    for doc_id in doc_ids:
        purchase_ledger_collection(client).document(doc_id).delete()


def test_check_vendor_silence_returns_insufficient_data_for_unknown_vendor():
    client = get_client()
    result = check_vendor_silence("VS Vendor With No History", as_of_date="2026-06-01", client=client)
    assert result.signal == VendorSilenceSignal.INSUFFICIENT_DATA
    assert result.invoice_count == 0
    assert result.last_invoice_date is None


def test_check_vendor_silence_insufficient_data_below_min_invoices():
    client = get_client()
    bills = [
        _make_bill("VS Vendor Thin History", "VS-T-001", "2026-04-01", batch_no="T1"),
        _make_bill("VS Vendor Thin History", "VS-T-002", "2026-05-01", batch_no="T2"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)
        result = check_vendor_silence(
            "VS Vendor Thin History", as_of_date="2026-06-01", min_invoices=3, client=client
        )
        assert result.signal == VendorSilenceSignal.INSUFFICIENT_DATA
        assert result.invoice_count == 2
    finally:
        _cleanup(doc_ids, client)


def test_check_vendor_silence_on_cadence_when_recent():
    client = get_client()
    # Regular ~30-day cadence, last invoice only 10 days before as_of_date --
    # well within the typical gap, not overdue.
    bills = [
        _make_bill("VS Vendor On Cadence", "VS-C-001", "2026-03-01", batch_no="C1"),
        _make_bill("VS Vendor On Cadence", "VS-C-002", "2026-04-01", batch_no="C2"),
        _make_bill("VS Vendor On Cadence", "VS-C-003", "2026-05-01", batch_no="C3"),
        _make_bill("VS Vendor On Cadence", "VS-C-004", "2026-05-22", batch_no="C4"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)
        result = check_vendor_silence("VS Vendor On Cadence", as_of_date="2026-06-01", client=client)
        assert result.signal == VendorSilenceSignal.ON_CADENCE
        assert result.invoice_count == 4
        assert result.typical_gap_days == 30
        assert result.days_since_last_invoice == 10
    finally:
        _cleanup(doc_ids, client)


def test_check_vendor_silence_flags_overdue_vendor():
    client = get_client()
    # Same regular ~30-day cadence, but the last invoice was 4 months ago --
    # well past 2x the typical gap.
    bills = [
        _make_bill("VS Vendor Gone Quiet", "VS-Q-001", "2026-01-01", batch_no="Q1"),
        _make_bill("VS Vendor Gone Quiet", "VS-Q-002", "2026-02-01", batch_no="Q2"),
        _make_bill("VS Vendor Gone Quiet", "VS-Q-003", "2026-03-01", batch_no="Q3"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)
        result = check_vendor_silence("VS Vendor Gone Quiet", as_of_date="2026-07-15", client=client)
        assert result.signal == VendorSilenceSignal.SILENT
        assert result.days_since_last_invoice == 136
        assert result.typical_gap_days == 29.5  # median(Jan->Feb 31d, Feb->Mar 28d)
    finally:
        _cleanup(doc_ids, client)
