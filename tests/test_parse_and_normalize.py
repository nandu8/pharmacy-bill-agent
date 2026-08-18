from conftest import SAMPLES_DIR

from pharmacy_agent.formats.parse_csv import parse_format_a_csv, parse_format_b_csv
from pharmacy_agent.formats.parse_xls import parse_format_c_xls
from pharmacy_agent.normalize import build_bill_from_format_a_rows, build_bill_from_format_b
from pharmacy_agent.validate import validate_bill


def test_format_a_csv_normalizes_and_validates_clean():
    # PH-26-49832_..._172026215756652.csv is the 13-item, 2959.00 version --
    # the one the vendor's own PDF confirms is authoritative (Bill 3, PRD S11).
    data = (SAMPLES_DIR / "PH-26-49832_16-Aug-26_172026215756652.csv").read_bytes()
    rows = parse_format_a_csv(data)
    assert len(rows) == 13
    bill = build_bill_from_format_a_rows(rows, vendor="Getwell Medicare Solution")
    assert bill.invoice_no == "PH-26-49832"
    assert bill.total_amount == 2959.00
    issues = validate_bill(bill)
    assert issues == [], issues


def test_format_a_csv_conflicting_version_has_extra_line():
    # The 14-item, 3268.00 version -- same invoice number, different
    # content. This is the reconciliation case (S7.3/S7.4), not a plain
    # duplicate: it must not silently be treated as identical to the file
    # above.
    data = (SAMPLES_DIR / "PH-26-49832_16-Aug-26_07202621332798.csv").read_bytes()
    rows = parse_format_a_csv(data)
    assert len(rows) == 14
    bill = build_bill_from_format_a_rows(rows, vendor="Getwell Medicare Solution")
    assert bill.invoice_no == "PH-26-49832"
    assert bill.total_amount == 3268.00


def test_format_b_csv_normalizes_and_validates():
    data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    parsed = parse_format_b_csv(data)
    assert parsed.vendor == "SUMMIT PHARMA"
    assert parsed.invoice_no == "SPH12474"
    bill = build_bill_from_format_b(parsed)
    assert bill.invoice_date == "2026-08-15"
    assert len(bill.line_items) == 10
    first = bill.line_items[0]
    assert first.item_name == "KAINOCET  TABLET"
    assert first.taxable_value == 117.60
    assert first.tax_component_1_label == "VAT"
    assert first.tax_component_1_amount == 5.88
    assert first.line_total == 123.48  # taxable + VAT; NOT the "Amount" column (117.60)
    issues = validate_bill(bill)
    assert issues == [], issues


def test_format_c_xls_all_samples_parse_and_validate():
    for f in sorted(SAMPLES_DIR.glob("*.xls")):
        rows = parse_format_c_xls(f.read_bytes())
        bill = build_bill_from_format_a_rows(rows, vendor="Bruklyn Associates", source_format="format_c")
        issues = validate_bill(bill)
        assert issues == [], f"{f.name}: {issues}"


def test_format_c_xls_line_total_matches_pdf_twin():
    # samples/002652_..._152516.xls line 1 (SILVEREX SSD CREAM 20GM):
    # taxable 148.57, CGST 3.71, SGST 3.71, line_total 155.99 -- verified
    # by hand against the vendor's own PDF invoice (PRD S7.4 worked example).
    data = (SAMPLES_DIR / "002652_26_I_260027300152516.xls").read_bytes()
    rows = parse_format_c_xls(data)
    bill = build_bill_from_format_a_rows(rows, vendor="Bruklyn Associates", source_format="format_c")
    first = bill.line_items[0]
    assert first.item_name.strip() == "SILVEREX  SSD CREAM  20GM"
    assert first.taxable_value == 148.57
    assert first.tax_component_1_amount == 3.71
    assert first.tax_component_2_amount == 3.71
    assert first.line_total == 155.99
    assert first.invoice_no == "I152516"  # pfx+invno assembly, see normalize.py
