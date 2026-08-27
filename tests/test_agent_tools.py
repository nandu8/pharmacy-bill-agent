from conftest import SAMPLES_DIR

from pharmacy_agent.agent import tools as agent_tools
from pharmacy_agent.firestore_client import bills_collection, get_client, purchase_ledger_collection
from pharmacy_agent.formats.schema import Bill, LineItem


def test_detect_format_impl_reads_bytes_and_writes_state():
    data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    state = {"_file_bytes": data}
    fmt = agent_tools._detect_format_impl(state)
    assert fmt == "format_b_csv"
    assert state["_detected_format"] == "format_b_csv"


def test_parse_csv_impl_format_a_uses_vendor_hint_and_validates_clean():
    data = (SAMPLES_DIR / "PH-26-49832_16-Aug-26_172026215756652.csv").read_bytes()
    state = {"_file_bytes": data, "_detected_format": "format_a_csv", "_vendor_hint": "Harbor Medicare Solutions"}
    result = agent_tools._parse_csv_impl(state)
    assert result["vendor"] == "Harbor Medicare Solutions"
    assert result["invoice_no"] == "PH-26-49832"
    assert result["total_amount"] == 2959.00
    assert result["validation_issues"] == []
    assert len(result["line_items"]) == 13
    assert isinstance(state["_bill"], Bill)


def test_parse_csv_impl_format_b_takes_vendor_from_content():
    data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    state = {"_file_bytes": data, "_detected_format": "format_b_csv"}
    result = agent_tools._parse_csv_impl(state)
    assert result["vendor"] == "SUMMIT PHARMA"
    assert result["invoice_no"] == "SPH12474"


def test_parse_csv_impl_wrong_format_returns_error_not_exception():
    state = {"_file_bytes": b"whatever", "_detected_format": "format_c_xls"}
    result = agent_tools._parse_csv_impl(state)
    assert "error" in result
    assert "_bill" not in state


def test_parse_xls_impl_parses_and_validates_clean():
    f = SAMPLES_DIR / "002652_26_I_260027300152516.xls"
    state = {"_file_bytes": f.read_bytes(), "_detected_format": "format_c_xls", "_vendor_hint": "Northfield Associates"}
    result = agent_tools._parse_xls_impl(state)
    assert result["vendor"] == "Northfield Associates"
    assert result["validation_issues"] == []
    first = result["line_items"][0]
    assert first["item_name"].strip() == "SILVEREX  SSD CREAM  20GM"
    assert first["line_total"] == 155.99


def test_parse_pdf_vision_impl_matches_hand_verified_line():
    # Live Gemini call -- same anchor invoice as test_parse_pdf_vision.py.
    f = SAMPLES_DIR / "002652_26_I_260027300152516.pdf"
    state = {"_file_bytes": f.read_bytes(), "_detected_format": "format_d_pdf"}
    result = agent_tools._parse_pdf_vision_impl(state)
    assert result["validation_issues"] == []
    first = result["line_items"][0]
    assert "SILVEREX" in first["item_name"].upper()
    assert first["line_total"] == 155.99


def _make_bill(vendor, invoice_no, item_name="AT TOOL ITEM", rate=10.0):
    item = LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-01",
        item_name=item_name,
        batch_no="B1",
        expiry_date="2027-01-01",
        quantity=1.0,
        rate=rate,
        discount=0.0,
        taxable_value=rate,
        tax_component_1_label="CGST",
        tax_component_1_rate=6.0,
        tax_component_1_amount=0.6,
        tax_component_2_label="SGST",
        tax_component_2_rate=6.0,
        tax_component_2_amount=0.6,
        mrp=rate * 1.5,
        line_total=rate + 1.2,
        hsn_code="3004",
        source_format="format_a",
    )
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-01",
        source_format="format_a",
        line_items=[item],
        total_amount=item.line_total,
    )


def test_lookup_vendor_history_impl_uses_current_bill_vendor():
    client = get_client()
    seed_bill = _make_bill("AT Vendor", "AT-HIST-001", item_name="AT TOOL ITEM", rate=9.0)
    doc_ids = agent_tools.purchase_ledger.record_purchase(seed_bill, client=client)
    try:
        state = {"_bill": _make_bill("AT Vendor", "AT-CURRENT-001", item_name="AT TOOL ITEM", rate=10.0)}
        entries = agent_tools._lookup_vendor_history_impl(state, "AT TOOL ITEM", None)
        assert len(entries) == 1
        assert entries[0]["invoice_no"] == "AT-HIST-001"
        assert entries[0]["seeded"] is False
    finally:
        for doc_id in doc_ids:
            purchase_ledger_collection(client).document(doc_id).delete()


def test_lookup_vendor_history_impl_without_parsed_bill_returns_error():
    result = agent_tools._lookup_vendor_history_impl({}, "ANYTHING", None)
    assert result == [{"error": "no bill parsed yet -- call a parse tool first"}]


def test_check_duplicate_impl_new_bill_is_new():
    state = {"_bill": _make_bill("AT Dup Vendor", "AT-DUP-001")}
    result = agent_tools._check_duplicate_impl(state)
    assert result == {"status": "new", "matched_bill_id": None, "matched_bill_status": None}


def test_check_duplicate_impl_without_parsed_bill_returns_error():
    assert agent_tools._check_duplicate_impl({}) == {"error": "no bill parsed yet -- call a parse tool first"}


def test_record_purchase_impl_writes_ledger_docs():
    client = get_client()
    bill = _make_bill("AT Record Vendor", "AT-REC-001")
    state = {"_bill": bill}
    result = agent_tools._record_purchase_impl(state)
    try:
        assert result["count"] == 1
        snapshot = purchase_ledger_collection(client).document(result["doc_ids"][0]).get()
        assert snapshot.exists
        assert snapshot.to_dict()["seeded"] is False
    finally:
        for doc_id in result["doc_ids"]:
            purchase_ledger_collection(client).document(doc_id).delete()


def test_record_purchase_impl_without_parsed_bill_returns_error():
    assert agent_tools._record_purchase_impl({}) == {"error": "no bill parsed yet -- call a parse tool first"}


def test_check_price_deviation_impl_detects_a_confirmed_rise():
    client = get_client()
    doc_ids: list[str] = []
    for i, rate in enumerate([20.0, 20.5, 21.0], start=1):
        seed_bill = _make_bill("AT Deviation Vendor", f"AT-DEV-HIST-{i}", item_name="AT DEV ITEM", rate=rate)
        doc_ids.extend(agent_tools.purchase_ledger.record_purchase(seed_bill, client=client))
    try:
        state = {"_bill": _make_bill("AT Deviation Vendor", "AT-DEV-CURRENT", item_name="AT DEV ITEM", rate=27.0)}
        result = agent_tools._check_price_deviation_impl(state, "AT DEV ITEM", 27.0)
        assert result["signal"] == "deviation_detected"
        assert result["confirmed"] is True
        assert result["prior_invoice_count"] == 3
        assert result["reference_rate"] == 21.0
    finally:
        for doc_id in doc_ids:
            purchase_ledger_collection(client).document(doc_id).delete()


def test_check_price_deviation_impl_without_parsed_bill_returns_error():
    result = agent_tools._check_price_deviation_impl({}, "ANYTHING", 10.0)
    assert result == {"error": "no bill parsed yet -- call a parse tool first"}


def test_cross_check_other_vendors_impl_uses_current_bill_vendor_and_invoice_date():
    state = {"_bill": _make_bill("AT Cross Vendor", "AT-CROSS-001", item_name="AT CROSS ITEM", rate=10.0)}
    result = agent_tools._cross_check_other_vendors_impl(state, "AT CROSS ITEM")
    # No other vendor has ever purchased this made-up item -- there's
    # nothing to compare against yet.
    assert result["signal"] == "insufficient_data"
    assert result["vendor_movements"] == []


def test_cross_check_other_vendors_impl_without_parsed_bill_returns_error():
    result = agent_tools._cross_check_other_vendors_impl({}, "ANYTHING")
    assert result == {"error": "no bill parsed yet -- call a parse tool first"}


def test_resolve_vendor_reference_uses_bill_when_parsed():
    state = {"_bill": _make_bill("AT WA Vendor", "AT-WA-001")}
    assert agent_tools._resolve_vendor_reference(state) == ("AT WA Vendor", "AT-WA-001")


def test_resolve_vendor_reference_falls_back_to_vendor_hint_when_unparsed():
    state = {"_vendor_hint": "AT WA Hint Vendor"}
    assert agent_tools._resolve_vendor_reference(state) == ("AT WA Hint Vendor", "unparsed")
    assert agent_tools._resolve_vendor_reference({}) == ("unknown", "unparsed")


def test_notify_pharmacist_impl_delegates_with_resolved_vendor_and_reference(monkeypatch):
    captured = {}

    def fake_notify(vendor, reference, message):
        captured["args"] = (vendor, reference, message)
        return {"sent": True, "mode": "notify"}

    monkeypatch.setattr(agent_tools.pharmacist_whatsapp_mod, "notify_pharmacist", fake_notify)
    state = {"_bill": _make_bill("AT WA Vendor", "AT-WA-002")}

    result = agent_tools._notify_pharmacist_impl(state, "Bill processed cleanly.")

    assert result == {"sent": True, "mode": "notify"}
    assert captured["args"] == ("AT WA Vendor", "AT-WA-002", "Bill processed cleanly.")


def test_ask_pharmacist_impl_delegates_with_resolved_vendor_and_reference(monkeypatch):
    captured = {}

    def fake_ask(vendor, reference, question):
        captured["args"] = (vendor, reference, question)
        return {"sent": True, "mode": "ask"}

    monkeypatch.setattr(agent_tools.pharmacist_whatsapp_mod, "ask_pharmacist", fake_ask)
    state = {"_vendor_hint": "AT WA Hint Vendor"}

    result = agent_tools._ask_pharmacist_impl(state, "Is this deviation expected?")

    assert result == {"sent": True, "mode": "ask"}
    assert captured["args"] == ("AT WA Hint Vendor", "unparsed", "Is this deviation expected?")
