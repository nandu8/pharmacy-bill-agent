from pharmacy_agent.check_price_deviation import PriceDeviationSignal, check_price_deviation
from pharmacy_agent.firestore_client import (
    get_client,
    pharmacist_resolutions_collection,
    purchase_ledger_collection,
)
from pharmacy_agent.formats.schema import Bill, LineItem
from pharmacy_agent.pharmacist_resolutions import PharmacistDecision, record_pharmacist_resolution
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


def test_no_history_returns_no_history_signal():
    client = get_client()
    result = check_price_deviation("PD No History Vendor", "AMLODIPINE 5MG", 21.0, client=client)
    assert result.signal == PriceDeviationSignal.NO_HISTORY
    assert result.prior_invoice_count == 0
    assert result.confirmed is False
    assert result.reference_rate is None


def test_small_move_is_within_normal():
    client = get_client()
    bills = [
        _make_bill("PD Vendor Normal", "PD-N-001", "2026-05-15", rate=20.00, batch_no="N1"),
        _make_bill("PD Vendor Normal", "PD-N-002", "2026-06-15", rate=20.50, batch_no="N2"),
        _make_bill("PD Vendor Normal", "PD-N-003", "2026-07-15", rate=21.00, batch_no="N3"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        result = check_price_deviation("PD Vendor Normal", "AMLODIPINE 5MG", 21.20, client=client)
        assert result.signal == PriceDeviationSignal.WITHIN_NORMAL
        assert result.reference_rate == 21.00
        assert result.prior_invoice_count == 3
        assert result.confirmed is True
    finally:
        _cleanup(doc_ids, client)


def test_large_move_with_three_priors_is_confirmed_deviation():
    client = get_client()
    bills = [
        _make_bill("PD Vendor Confirmed", "PD-C-001", "2026-05-15", rate=20.00, batch_no="C1"),
        _make_bill("PD Vendor Confirmed", "PD-C-002", "2026-06-15", rate=20.50, batch_no="C2"),
        _make_bill("PD Vendor Confirmed", "PD-C-003", "2026-07-15", rate=21.00, batch_no="C3"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        # 26% above the most recent prior rate (21.00) -- PRD S7.4 vendor-error worked example.
        result = check_price_deviation("PD Vendor Confirmed", "AMLODIPINE 5MG", 26.46, client=client)
        assert result.signal == PriceDeviationSignal.DEVIATION_DETECTED
        assert result.prior_invoice_count == 3
        assert result.confirmed is True
        assert round(result.pct_change, 2) == 0.26
    finally:
        _cleanup(doc_ids, client)


def test_large_move_with_fewer_than_three_priors_is_unconfirmed():
    client = get_client()
    bills = [
        _make_bill("PD Vendor Thin", "PD-T-001", "2026-06-15", rate=20.00, batch_no="T1"),
        _make_bill("PD Vendor Thin", "PD-T-002", "2026-07-15", rate=20.50, batch_no="T2"),
    ]
    doc_ids = []
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)

        result = check_price_deviation("PD Vendor Thin", "AMLODIPINE 5MG", 26.0, client=client)
        assert result.signal == PriceDeviationSignal.DEVIATION_DETECTED
        assert result.prior_invoice_count == 2
        assert result.confirmed is False
    finally:
        _cleanup(doc_ids, client)


def test_previously_approved_rate_stops_being_flagged():
    # PRD S7.7: "if the pharmacist approves a price rise for a vendor, the
    # agent stops flagging it" -- a standing resolution at-or-above the
    # current rate downgrades what would otherwise be a confirmed deviation.
    client = get_client()
    bills = [
        _make_bill("PD Vendor Approved", "PD-A-001", "2026-05-15", rate=20.00, batch_no="A1"),
        _make_bill("PD Vendor Approved", "PD-A-002", "2026-06-15", rate=20.50, batch_no="A2"),
        _make_bill("PD Vendor Approved", "PD-A-003", "2026-07-15", rate=21.00, batch_no="A3"),
    ]
    doc_ids = []
    resolution_doc_id = None
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)
        resolution_doc_id = record_pharmacist_resolution(
            "PD Vendor Approved",
            "AMLODIPINE 5MG",
            rate=26.46,
            decision=PharmacistDecision.APPROVED,
            invoice_no="PD-A-PRIOR",
            client=client,
        )

        result = check_price_deviation("PD Vendor Approved", "AMLODIPINE 5MG", 26.46, client=client)
        assert result.signal == PriceDeviationSignal.WITHIN_NORMAL
        assert result.resolution is not None
        assert result.resolution.decision == PharmacistDecision.APPROVED

        # A further rise past the approved rate is still worth flagging.
        result_higher = check_price_deviation("PD Vendor Approved", "AMLODIPINE 5MG", 30.0, client=client)
        assert result_higher.signal == PriceDeviationSignal.DEVIATION_DETECTED
    finally:
        _cleanup(doc_ids, client)
        if resolution_doc_id:
            pharmacist_resolutions_collection(client).document(resolution_doc_id).delete()


def test_previously_rejected_deviation_lowers_the_threshold():
    # PRD S7.7: a rejected pattern is "weighted more heavily next time" --
    # translated here as a stricter (halved) deviation threshold for this
    # vendor+item, so a smaller move than usual still gets flagged.
    client = get_client()
    bills = [
        _make_bill("PD Vendor Rejected", "PD-R-001", "2026-05-15", rate=20.00, batch_no="R1"),
        _make_bill("PD Vendor Rejected", "PD-R-002", "2026-06-15", rate=20.50, batch_no="R2"),
        _make_bill("PD Vendor Rejected", "PD-R-003", "2026-07-15", rate=21.00, batch_no="R3"),
    ]
    doc_ids = []
    resolution_doc_id = None
    try:
        for bill in bills:
            doc_ids += record_purchase(bill, client=client)
        resolution_doc_id = record_pharmacist_resolution(
            "PD Vendor Rejected",
            "AMLODIPINE 5MG",
            rate=26.46,
            decision=PharmacistDecision.REJECTED,
            invoice_no="PD-R-PRIOR",
            client=client,
        )

        # A 7% move -- within the default 10% threshold, but above the
        # halved 5% threshold a rejected pattern now applies.
        result = check_price_deviation("PD Vendor Rejected", "AMLODIPINE 5MG", 22.47, client=client)
        assert result.signal == PriceDeviationSignal.DEVIATION_DETECTED
        assert result.resolution is not None
        assert result.resolution.decision == PharmacistDecision.REJECTED
    finally:
        _cleanup(doc_ids, client)
        if resolution_doc_id:
            pharmacist_resolutions_collection(client).document(resolution_doc_id).delete()
