from pharmacy_agent.check_price_deviation import PriceDeviationResult, PriceDeviationSignal
from pharmacy_agent.cross_check_other_vendors import CrossVendorCheckResult, CrossVendorSignal
from pharmacy_agent.dispute_gate import check_dispute_gate
from pharmacy_agent.email_vendor import EMAIL_LOG_COLLECTION, email_log_doc_id
from pharmacy_agent.firestore_client import get_client


def _deviation(signal, confirmed, prior_invoice_count=3):
    return PriceDeviationResult(
        signal=signal,
        current_rate=26.0,
        reference_rate=20.0,
        pct_change=0.30,
        prior_invoice_count=prior_invoice_count,
        confirmed=confirmed,
        history=[],
    )


def _cross_vendor(signal):
    return CrossVendorCheckResult(signal=signal, vendor_movements=[])


def test_all_four_conditions_pass_authorizes_dispute():
    result = check_dispute_gate(
        vendor="DG Vendor Pass",
        invoice_no="DG-INV-1",
        price_deviation=_deviation(PriceDeviationSignal.DEVIATION_DETECTED, confirmed=True),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.NO_MOVEMENT),
        disputed_amount=4200.0,
    )
    assert result.authorized is True
    assert result.failed_conditions == []


def test_no_deviation_fails_condition_1():
    result = check_dispute_gate(
        vendor="DG Vendor NoDev",
        invoice_no="DG-INV-2",
        price_deviation=_deviation(PriceDeviationSignal.WITHIN_NORMAL, confirmed=True),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.NO_MOVEMENT),
        disputed_amount=4200.0,
    )
    assert result.authorized is False
    assert "no_deviation_detected" in result.failed_conditions


def test_unconfirmed_deviation_fails_condition_1():
    result = check_dispute_gate(
        vendor="DG Vendor Thin",
        invoice_no="DG-INV-3",
        price_deviation=_deviation(PriceDeviationSignal.DEVIATION_DETECTED, confirmed=False, prior_invoice_count=2),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.NO_MOVEMENT),
        disputed_amount=4200.0,
    )
    assert result.authorized is False
    assert "unconfirmed_deviation" in result.failed_conditions


def test_market_movement_fails_condition_2():
    result = check_dispute_gate(
        vendor="DG Vendor Market",
        invoice_no="DG-INV-4",
        price_deviation=_deviation(PriceDeviationSignal.DEVIATION_DETECTED, confirmed=True),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.MARKET_MOVEMENT),
        disputed_amount=4200.0,
    )
    assert result.authorized is False
    assert "cross_vendor_check_not_conclusive" in result.failed_conditions


def test_insufficient_cross_vendor_data_fails_condition_2():
    result = check_dispute_gate(
        vendor="DG Vendor Insuff",
        invoice_no="DG-INV-5",
        price_deviation=_deviation(PriceDeviationSignal.DEVIATION_DETECTED, confirmed=True),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.INSUFFICIENT_DATA),
        disputed_amount=4200.0,
    )
    assert result.authorized is False
    assert "cross_vendor_check_not_conclusive" in result.failed_conditions


def test_amount_below_floor_fails_condition_3():
    result = check_dispute_gate(
        vendor="DG Vendor Small",
        invoice_no="DG-INV-6",
        price_deviation=_deviation(PriceDeviationSignal.DEVIATION_DETECTED, confirmed=True),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.NO_MOVEMENT),
        disputed_amount=250.0,
    )
    assert result.authorized is False
    assert "below_amount_floor" in result.failed_conditions


def test_amount_exactly_at_floor_fails_condition_3():
    result = check_dispute_gate(
        vendor="DG Vendor AtFloor",
        invoice_no="DG-INV-7",
        price_deviation=_deviation(PriceDeviationSignal.DEVIATION_DETECTED, confirmed=True),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.NO_MOVEMENT),
        disputed_amount=500.0,
    )
    assert result.authorized is False
    assert "below_amount_floor" in result.failed_conditions


def test_duplicate_dispute_fails_condition_4():
    client = get_client()
    vendor = "DG Vendor Dup"
    invoice_no = "DG-INV-8"
    doc_id = email_log_doc_id(vendor, invoice_no, "dispute")
    log_doc = client.collection(EMAIL_LOG_COLLECTION).document(doc_id)
    try:
        log_doc.set({"vendor": vendor, "reference": invoice_no, "mode": "dispute"})

        result = check_dispute_gate(
            vendor=vendor,
            invoice_no=invoice_no,
            price_deviation=_deviation(PriceDeviationSignal.DEVIATION_DETECTED, confirmed=True),
            cross_vendor_check=_cross_vendor(CrossVendorSignal.NO_MOVEMENT),
            disputed_amount=4200.0,
            client=client,
        )
        assert result.authorized is False
        assert result.failed_conditions == ["dispute_already_sent"]
    finally:
        log_doc.delete()


def test_multiple_failing_conditions_all_reported():
    result = check_dispute_gate(
        vendor="DG Vendor Multi",
        invoice_no="DG-INV-9",
        price_deviation=_deviation(PriceDeviationSignal.WITHIN_NORMAL, confirmed=False, prior_invoice_count=1),
        cross_vendor_check=_cross_vendor(CrossVendorSignal.MARKET_MOVEMENT),
        disputed_amount=100.0,
    )
    assert result.authorized is False
    assert result.failed_conditions == [
        "no_deviation_detected",
        "cross_vendor_check_not_conclusive",
        "below_amount_floor",
    ]
