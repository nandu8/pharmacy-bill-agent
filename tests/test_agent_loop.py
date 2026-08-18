from conftest import SAMPLES_DIR

from pharmacy_agent.agent.loop import run_bill
from pharmacy_agent.agent.terminal import PENDING_PHARMACIST, RESOLVED
from pharmacy_agent.firestore_client import bills_collection, get_client, purchase_ledger_collection
from pharmacy_agent.purchase_ledger import ledger_doc_id, normalize_item_key


def _cleanup(bill, bill_doc_id):
    client = get_client()
    bills_collection(client).document(bill_doc_id).delete()
    if bill is None:
        return
    for item in bill.line_items:
        doc_id = ledger_doc_id(bill.vendor, bill.invoice_no, item.item_name, item.batch_no)
        purchase_ledger_collection(client).document(doc_id).delete()


def test_run_bill_processes_a_clean_format_b_bill_end_to_end():
    # T31 milestone check (PRD Phase 5): a clean sample bill goes in,
    # resolves automatically, and actually lands in purchase_ledger -- with
    # no anomalies. Live Gemini turns + live Firestore -- PSPH12474.CSV is
    # confirmed clean (see
    # test_parse_and_normalize.py::test_format_b_csv_normalizes_and_validates),
    # so this is a real multi-turn agent loop over real sample data, not a
    # scripted pipeline.
    data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    result = run_bill(data)
    try:
        assert result.turn_count > 0
        tools_called = [record.tool for record in result.tool_call_history]
        assert "detect_format" in tools_called
        assert "parse_csv" in tools_called
        assert "record_purchase" in tools_called
        assert "finish" in tools_called

        assert result.bill is not None
        assert result.bill.vendor == "SUMMIT PHARMA"
        assert result.bill.invoice_no == "SPH12474"
        assert result.validation_issues == []
        assert result.status == RESOLVED
        assert result.findings
        assert result.final_text.strip() != ""

        client = get_client()
        assert result.bill.line_items
        for item in result.bill.line_items:
            doc_id = ledger_doc_id(
                result.bill.vendor, result.bill.invoice_no, item.item_name, item.batch_no
            )
            ledger_doc = purchase_ledger_collection(client).document(doc_id).get()
            assert ledger_doc.exists
            payload = ledger_doc.to_dict()
            assert payload["vendor"] == result.bill.vendor
            assert payload["invoice_no"] == result.bill.invoice_no
            assert payload["normalized_item_key"] == normalize_item_key(item.item_name)
            assert payload["seeded"] is False
    finally:
        _cleanup(result.bill, result.bill_doc_id)


def test_run_bill_parks_on_turn_cap_exhaustion():
    # PRD S7.10 turn-cap guardrail. The clean SUMMIT PHARMA bill genuinely
    # needs several turns (detect -> parse -> check_duplicate -> record ->
    # finish) to reach a real conclusion, so max_turns=1 is guaranteed to
    # trip the cap regardless of which tool the model picks first -- a
    # deterministic way to exercise a live cutoff without an artificial
    # infinite-loop tool.
    data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    result = run_bill(data, max_turns=1)
    try:
        assert result.turn_count == 1
        assert result.status == PENDING_PHARMACIST
        assert any("turn cap" in finding for finding in result.findings)
    finally:
        _cleanup(result.bill, result.bill_doc_id)
