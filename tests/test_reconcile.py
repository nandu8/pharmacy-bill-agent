from conftest import SAMPLES_DIR

from pharmacy_agent.formats.parse_csv import parse_format_a_csv
from pharmacy_agent.normalize import build_bill_from_format_a_rows
from pharmacy_agent.reconcile import reconcile

VENDOR = "Harbor Medicare Solutions"
_AUTHORITATIVE_CSV = "PH-26-49832_16-Aug-26_172026215756652.csv"  # 13-item, 2959.00
_CONFLICTING_CSV = "PH-26-49832_16-Aug-26_07202621332798.csv"  # 14-item, 3268.00
_AUTHORITATIVE_PDF = "rptGSTSALESINVOICE_HMSPL650172026215756654.pdf"


def _bill_from_csv(filename: str):
    data = (SAMPLES_DIR / filename).read_bytes()
    rows = parse_format_a_csv(data)
    return build_bill_from_format_a_rows(rows, vendor=VENDOR)


def test_reconcile_returns_no_discrepancy_when_pdf_is_already_in_hand():
    pdf_bytes = (SAMPLES_DIR / _AUTHORITATIVE_PDF).read_bytes()
    from pharmacy_agent.formats.parse_pdf_vision import parse_pdf_vision

    bill = parse_pdf_vision(pdf_bytes)
    result = reconcile(bill, candidates=[])
    assert result.authoritative_found
    assert result.authoritative_source == "in_hand"
    assert result.matches_bill_in_hand
    assert result.discrepancies == []


def test_reconcile_reports_no_authoritative_document_without_a_pdf_candidate():
    bill = _bill_from_csv(_CONFLICTING_CSV)
    conflicting_twin = (SAMPLES_DIR / _AUTHORITATIVE_CSV).read_bytes()
    result = reconcile(bill, candidates=[("twin.csv", conflicting_twin)])
    assert not result.authoritative_found
    assert result.authoritative_bill is None
    assert result.discrepancies == []


def test_reconcile_flags_discrepancy_between_conflicting_bill_and_authoritative_pdf():
    bill = _bill_from_csv(_CONFLICTING_CSV)  # 14-item, 3268.00 -- wrong version
    pdf_bytes = (SAMPLES_DIR / _AUTHORITATIVE_PDF).read_bytes()

    result = reconcile(bill, candidates=[("authoritative.pdf", pdf_bytes)])

    assert result.authoritative_found
    assert result.authoritative_source == "authoritative.pdf"
    assert result.authoritative_bill.invoice_no == "PH-26-49832"
    assert result.authoritative_bill.total_amount == 2959.00
    assert not result.matches_bill_in_hand
    assert result.discrepancies, "expected at least one discrepancy for the 14-item vs 13-item mismatch"
    assert any("total_amount differs" in d for d in result.discrepancies)


def test_reconcile_confirms_match_when_authoritative_csv_matches_the_pdf():
    bill = _bill_from_csv(_AUTHORITATIVE_CSV)  # 13-item, 2959.00 -- the correct version
    pdf_bytes = (SAMPLES_DIR / _AUTHORITATIVE_PDF).read_bytes()

    result = reconcile(bill, candidates=[("authoritative.pdf", pdf_bytes)])

    assert result.authoritative_found
    assert result.authoritative_bill.total_amount == 2959.00
    assert result.matches_bill_in_hand, result.discrepancies
    assert result.discrepancies == []
