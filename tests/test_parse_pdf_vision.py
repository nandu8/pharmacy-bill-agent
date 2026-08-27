from conftest import SAMPLES_DIR

from pharmacy_agent.formats.parse_pdf_vision import parse_pdf_vision
from pharmacy_agent.validate import validate_bill


def test_pdf_vision_matches_xls_twin():
    # 002652_..._152516.pdf is the PDF twin of the xls already covered by
    # test_format_c_xls_line_total_matches_pdf_twin -- same invoice, so the
    # vision read must land on the same hand-verified numbers (PRD S7.4):
    # SILVEREX SSD CREAM 20GM, taxable 148.57, CGST 3.71, SGST 3.71,
    # line_total 155.99.
    data = (SAMPLES_DIR / "002652_26_I_260027300152516.pdf").read_bytes()
    bill = parse_pdf_vision(data)

    assert bill.line_items, "expected at least one line item"
    first = bill.line_items[0]
    assert "SILVEREX" in first.item_name.upper()
    assert first.taxable_value == 148.57
    assert first.tax_component_1_amount == 3.71
    assert first.tax_component_2_amount == 3.71
    assert first.line_total == 155.99

    issues = validate_bill(bill)
    assert issues == [], issues


def test_pdf_vision_resolves_reconciliation_case():
    # rptGSTSALESINVOICE_HMSPL...pdf is Harbor Medicare's own PDF for
    # PH-26-49832 -- the vendor's authoritative document that resolves
    # which of the two conflicting CSV versions (13-item/2959.00 vs
    # 14-item/3268.00, see test_parse_and_normalize.py) is correct. This is
    # the reconciliation case, S7.3/S7.4, Demo Bill 3.
    data = (SAMPLES_DIR / "rptGSTSALESINVOICE_HMSPL650172026215756654.pdf").read_bytes()
    bill = parse_pdf_vision(data)

    assert bill.invoice_no == "PH-26-49832"
    assert len(bill.line_items) == 13
    assert bill.total_amount == 2959.00

    issues = validate_bill(bill)
    assert issues == [], issues
