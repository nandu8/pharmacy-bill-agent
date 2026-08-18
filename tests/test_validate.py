"""Rate vs MRP plausibility check (PRD S7.4 / T32).

Real vendor samples (see samples_sanitized/, checked by hand for this task)
show clean rate:MRP ratios ranging from ~0.20 (steep-margin generics, e.g.
KAINOCET TABLET at 3.92/19.50) up to ~0.86 (thin-margin OTC). Thresholds
below are calibrated to stay well clear of that observed range so real
clean invoices never misfire.
"""
from pharmacy_agent.formats.schema import LineItem
from pharmacy_agent.validate import validate_line


def _make_line_item(rate: float, mrp: float) -> LineItem:
    quantity = 10.0
    discount = 0.0
    taxable_value = round(quantity * rate - discount, 2)
    tax1 = round(taxable_value * 0.025, 2)
    tax2 = round(taxable_value * 0.025, 2)
    line_total = round(taxable_value + tax1 + tax2, 2)
    return LineItem(
        vendor="Test Vendor",
        invoice_no="INV1",
        invoice_date="2026-08-15",
        item_name="TEST ITEM",
        batch_no="B1",
        expiry_date="2028-01-01",
        quantity=quantity,
        rate=rate,
        discount=discount,
        taxable_value=taxable_value,
        tax_component_1_label="CGST",
        tax_component_1_rate=2.5,
        tax_component_1_amount=tax1,
        tax_component_2_label="SGST",
        tax_component_2_rate=2.5,
        tax_component_2_amount=tax2,
        mrp=mrp,
        line_total=line_total,
        hsn_code="30049099",
        source_format="format_a",
    )


def test_rate_within_normal_margin_of_mrp_is_clean():
    item = _make_line_item(rate=40.0, mrp=52.5)  # ratio 0.762 -- matches real samples
    assert validate_line(item) == []


def test_steep_but_real_margin_is_not_flagged():
    item = _make_line_item(rate=10.4, mrp=52.5)  # ratio 0.198, near real KAINOCET sample (0.20)
    assert validate_line(item) == []


def test_rate_exceeding_mrp_is_flagged():
    item = _make_line_item(rate=60.0, mrp=52.5)  # rate above the retail ceiling
    issues = validate_line(item)
    assert len(issues) == 1
    assert "exceeds MRP" in issues[0].message


def test_rate_implausibly_low_vs_mrp_is_flagged():
    item = _make_line_item(rate=1.0, mrp=52.5)  # ratio 0.019, well below the observed floor
    issues = validate_line(item)
    assert len(issues) == 1
    assert "implausibly low" in issues[0].message


def test_zero_or_missing_mrp_is_not_flagged():
    item = _make_line_item(rate=40.0, mrp=0.0)  # no MRP on record -- nothing to compare against
    assert validate_line(item) == []
