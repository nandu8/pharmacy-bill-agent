from pharmacy_agent.agent.terminal import RESOLVED, record_bill_result
from pharmacy_agent.firestore_client import bills_collection, get_client
from pharmacy_agent.formats.schema import Bill, LineItem
from pharmacy_agent.status_page import (
    BillSummary,
    cloud_trace_url,
    list_bills,
    render_status_page,
)


def _make_bill(vendor="AT Status Vendor", invoice_no="AT-STATUS-001"):
    item = LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-01",
        item_name="AT STATUS ITEM",
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


def test_list_bills_reflects_a_recorded_bill():
    client = get_client()
    bill = _make_bill()
    doc_id = record_bill_result(
        bill, RESOLVED, ["clean, recorded"], client=client, trace_id="b" * 32
    )
    try:
        summaries = list_bills(client=client, limit=200)
        matching = [s for s in summaries if s.doc_id == doc_id]
        assert len(matching) == 1
        summary = matching[0]
        assert summary.vendor == "AT Status Vendor"
        assert summary.invoice_number == "AT-STATUS-001"
        assert summary.status == RESOLVED
        assert summary.findings == ["clean, recorded"]
        assert summary.trace_id == "b" * 32
        assert summary.updated_at is not None
    finally:
        bills_collection(client).document(doc_id).delete()


def test_cloud_trace_url_embeds_project_and_trace_id():
    url = cloud_trace_url("pharmacy-bill-agent", "a" * 32)
    assert "pharmacy-bill-agent" in url
    assert "a" * 32 in url
    assert url.startswith("https://console.cloud.google.com/")


def _summary(**overrides) -> BillSummary:
    defaults = dict(
        doc_id="doc1",
        vendor="Clean Vendor",
        invoice_number="INV-1",
        status="resolved",
        findings=["ok"],
        trace_id="c" * 32,
        updated_at="2026-08-25T00:00:00+00:00",
    )
    defaults.update(overrides)
    return BillSummary(**defaults)


def test_render_status_page_escapes_untrusted_vendor_content():
    # Vendor/invoice/findings text ultimately comes from a vendor's own
    # document -- untrusted content this page must never render as live
    # HTML (PRD S7.10's "untrusted content is never treated as
    # instructions" guardrail applies here too).
    malicious = _summary(
        vendor="<script>alert(1)</script>",
        invoice_number='"><img src=x onerror=alert(2)>',
        findings=["<b>not bold</b>"],
    )
    page = render_status_page([malicious], project_id="pharmacy-bill-agent")
    assert "<script>alert(1)</script>" not in page
    assert "<img src=x" not in page
    assert "<b>not bold</b>" not in page
    assert "&lt;script&gt;" in page


def test_render_status_page_links_to_cloud_trace_when_trace_id_present():
    import html

    summary = _summary(trace_id="d" * 32)
    page = render_status_page([summary], project_id="pharmacy-bill-agent")
    expected_href = html.escape(cloud_trace_url("pharmacy-bill-agent", "d" * 32))
    assert expected_href in page


def test_render_status_page_omits_trace_link_when_absent():
    summary = _summary(trace_id=None)
    page = render_status_page([summary], project_id="pharmacy-bill-agent")
    assert "console.cloud.google.com/traces" not in page


def test_render_status_page_shows_vendor_and_status():
    summary = _summary(vendor="Real Vendor Co", status="pending_pharmacist")
    page = render_status_page([summary], project_id="pharmacy-bill-agent")
    assert "Real Vendor Co" in page
    assert "pending_pharmacist" in page
