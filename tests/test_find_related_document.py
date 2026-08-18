from conftest import SAMPLES_DIR

from pharmacy_agent.find_related_document import find_related_document, invoice_numbers_match
from pharmacy_agent.formats.detect import FORMAT_A_CSV, FORMAT_C_XLS, FORMAT_D_PDF


def test_invoice_numbers_match_by_digit_suffix_not_exact_equality():
    # Format C's assembled invoice_no ("I152516") doesn't carry the PDF's
    # full branch/series prefix ("260027300152516") -- only the numeric
    # tail is common to both (normalize.py S7.3 note).
    assert invoice_numbers_match("I152516", "260027300152516")
    assert invoice_numbers_match("PH-26-49832", "PH-26-49832")
    assert not invoice_numbers_match("I152516", "260027300999999")


def test_invoice_numbers_match_rejects_short_coincidental_suffix():
    assert not invoice_numbers_match("1", "21")


def test_find_related_document_returns_none_with_no_candidates():
    assert find_related_document("PH-26-49832", "Harbor Medicare Solutions", []) is None


def test_find_related_document_skips_unparseable_candidate():
    # Well-formed Format A header, zero data rows -- detect_format routes it
    # to parse_csv, but build_bill_from_format_a_rows raises on empty rows.
    # That failure must be swallowed (most candidates are expected not to
    # match), not propagated.
    garbage = b"c2code,br,yr,pfx,invno\n"
    result = find_related_document(
        "PH-26-49832", "Harbor Medicare Solutions", [("empty.csv", garbage)]
    )
    assert result is None


def test_find_related_document_skips_excluded_format_even_on_content_match():
    data = (SAMPLES_DIR / "PH-26-49832_16-Aug-26_172026215756652.csv").read_bytes()
    result = find_related_document(
        "PH-26-49832",
        "Harbor Medicare Solutions",
        [("twin.csv", data)],
        exclude_format=FORMAT_A_CSV,
    )
    assert result is None


def test_find_related_document_finds_authoritative_pdf_for_reconciliation():
    # rptGSTSALESINVOICE_HMSPL...pdf is Harbor Medicare's own PDF for
    # PH-26-49832 -- the authoritative document reconciliation (T36) needs
    # among a set of candidates that also includes the conflicting CSVs.
    conflicting_csv = (SAMPLES_DIR / "PH-26-49832_16-Aug-26_07202621332798.csv").read_bytes()
    pdf = (SAMPLES_DIR / "rptGSTSALESINVOICE_HMSPL650172026215756654.pdf").read_bytes()

    result = find_related_document(
        "PH-26-49832",
        "Harbor Medicare Solutions",
        [("conflicting.csv", conflicting_csv), ("authoritative.pdf", pdf)],
        exclude_format=FORMAT_A_CSV,
    )
    assert result is not None
    assert result.detected_format == FORMAT_D_PDF
    assert result.bill.invoice_no == "PH-26-49832"
    assert result.bill.total_amount == 2959.00
    assert len(result.bill.line_items) == 13


def test_find_related_document_finds_xls_twin_via_suffix_recovery_ladder():
    # 002652_..._152516.pdf/.xls are twins (T18) -- the recovery-ladder
    # scenario (S7.3 step 3): the PDF vision call is the one that "failed"
    # (or is unavailable), and the readable xls twin is found by invoice
    # number instead. The search target ("260027300152516") is the PDF's
    # own printed invoice number, the same one T18's vision test read off
    # this exact document (test_parse_pdf_vision.py) -- reused here as a
    # constant rather than a live Gemini call, since this test only needs
    # to exercise the suffix match against the readable xls twin.
    xls = (SAMPLES_DIR / "002652_26_I_260027300152516.xls").read_bytes()

    result = find_related_document(
        "260027300152516",
        "",
        [("twin.xls", xls)],
        exclude_format=FORMAT_D_PDF,
    )
    assert result is not None
    assert result.detected_format == FORMAT_C_XLS
    assert result.bill.invoice_no == "I152516"
