from pharmacy_agent.agent.terminal import (
    PENDING_VENDOR,
    RESOLVED,
    bill_doc_id,
    record_bill_result,
    _finish_impl,
)
from pharmacy_agent.firestore_client import bills_collection, get_client
from pharmacy_agent.formats.schema import Bill, LineItem


def _make_bill(vendor="AT Terminal Vendor", invoice_no="AT-TERM-001"):
    item = LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-01",
        item_name="AT TERMINAL ITEM",
        batch_no="B1",
        expiry_date="2027-01-01",
        quantity=1.0,
        rate=10.0,
        discount=0.0,
        taxable_value=10.0,
        tax_component_1_label="CGST",
        tax_component_1_rate=6.0,
        tax_component_1_amount=0.6,
        tax_component_2_label="SGST",
        tax_component_2_rate=6.0,
        tax_component_2_amount=0.6,
        mrp=15.0,
        line_total=11.2,
        hsn_code="3004",
        source_format="format_a",
    )
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-01",
        source_format="format_a",
        line_items=[item],
        total_amount=11.2,
    )


def test_finish_impl_sets_terminal_status_and_appends_finding():
    state = {"_findings": ["earlier note"]}
    result = _finish_impl(state, "resolved", "clean bill, recorded")
    assert result == {"status": "resolved", "summary": "clean bill, recorded"}
    assert state["_terminal_status"] == "resolved"
    assert state["_findings"] == ["earlier note", "clean bill, recorded"]


def test_bill_doc_id_is_deterministic():
    assert bill_doc_id("V", "INV-1") == bill_doc_id("V", "INV-1")
    assert bill_doc_id("V", "INV-1") != bill_doc_id("V", "INV-2")


def test_record_bill_result_with_bill_is_idempotent_and_readable_by_check_duplicate():
    client = get_client()
    bill = _make_bill()
    doc_id = record_bill_result(bill, RESOLVED, ["ok"], client=client)
    try:
        data = bills_collection(client).document(doc_id).get().to_dict()
        assert data["vendor"] == bill.vendor
        assert data["invoice_number"] == bill.invoice_no
        assert data["status"] == RESOLVED
        assert data["findings"] == ["ok"]

        doc_id_2 = record_bill_result(bill, RESOLVED, ["ok", "again"], client=client)
        assert doc_id_2 == doc_id
    finally:
        bills_collection(client).document(doc_id).delete()


def test_record_bill_result_without_bill_uses_status_and_null_identity():
    client = get_client()
    doc_id = record_bill_result(None, PENDING_VENDOR, ["file unreadable"], client=client)
    try:
        data = bills_collection(client).document(doc_id).get().to_dict()
        assert data["vendor"] is None
        assert data["invoice_number"] is None
        assert data["status"] == PENDING_VENDOR
    finally:
        bills_collection(client).document(doc_id).delete()


def test_record_bill_result_stores_trace_id_when_given():
    # PRD S10 schema / T54: the status page (S7.11) links a bill to its
    # Cloud Trace reasoning chain via this field.
    client = get_client()
    bill = _make_bill(vendor="AT Trace Vendor", invoice_no="AT-TRACE-001")
    doc_id = record_bill_result(bill, RESOLVED, ["ok"], client=client, trace_id="a" * 32)
    try:
        data = bills_collection(client).document(doc_id).get().to_dict()
        assert data["trace_id"] == "a" * 32
    finally:
        bills_collection(client).document(doc_id).delete()


def test_record_bill_result_without_bill_uses_fallback_doc_id_when_given():
    # T46: a repeat unreadable resend should converge on the same
    # placeholder doc, not spawn a fresh random one each retry.
    client = get_client()
    doc_id = record_bill_result(
        None, PENDING_VENDOR, ["still unreadable"], client=client, fallback_doc_id="at-placeholder-1"
    )
    try:
        assert doc_id == "at-placeholder-1"
        data = bills_collection(client).document(doc_id).get().to_dict()
        assert data["status"] == PENDING_VENDOR
    finally:
        bills_collection(client).document(doc_id).delete()


def test_record_bill_result_omits_trace_id_when_not_given():
    client = get_client()
    doc_id = record_bill_result(None, PENDING_VENDOR, ["file unreadable"], client=client)
    try:
        data = bills_collection(client).document(doc_id).get().to_dict()
        assert "trace_id" not in data
    finally:
        bills_collection(client).document(doc_id).delete()
