import os

import pytest

from conftest import SAMPLES_DIR

from pharmacy_agent.agent import tools as agent_tools
from pharmacy_agent.agent.loop import resume_bill, resume_bill_with_file, run_bill
from pharmacy_agent.agent.terminal import PENDING_PHARMACIST, PENDING_VENDOR, RESOLVED
from pharmacy_agent.email_vendor import email_log_doc_id
from pharmacy_agent.firestore_client import (
    agent_runs_collection,
    bills_collection,
    get_client,
    pharmacist_resolutions_collection,
    purchase_ledger_collection,
)
from pharmacy_agent.formats.schema import Bill, LineItem
from pharmacy_agent.pharmacist_resolutions import resolution_doc_id
from pharmacy_agent.purchase_ledger import ledger_doc_id, normalize_item_key, record_purchase
from pharmacy_agent.vendor_directory import (
    set_pharmacist_email,
    set_vendor_email,
    vendor_directory_collection,
    vendor_directory_doc_id,
)

# The account's own address (same one authorized via OAuth, T08/T09) -- kept
# out of source since it's the developer's personal email; set it locally to
# run the live dispute-send test below. Same convention as
# test_email_vendor.py.
TEST_ADDRESS = os.environ.get("TEST_GMAIL_ADDRESS")


def _stub_pharmacist_whatsapp(monkeypatch):
    # T41's WhatsApp send (Meta Cloud API) shouldn't fire on every test run --
    # a live agent-loop run genuinely calls notify_pharmacist/ask_pharmacist,
    # so every test driving a full run stubs the send itself rather than
    # hitting the real API (unlike Gmail, which the project's other live
    # tests do send for real).
    monkeypatch.setattr(
        agent_tools.pharmacist_whatsapp_mod,
        "notify_pharmacist",
        lambda vendor, reference, message: {"sent": True, "mode": "notify"},
    )
    monkeypatch.setattr(
        agent_tools.pharmacist_whatsapp_mod,
        "ask_pharmacist",
        lambda vendor, reference, question: {"sent": True, "mode": "ask"},
    )


def _cleanup(bill, bill_doc_id):
    client = get_client()
    bills_collection(client).document(bill_doc_id).delete()
    # T44: pending_pharmacist/pending_vendor runs also write an agent_runs
    # doc under this same id -- harmless no-op delete when a run resolved
    # and never wrote one.
    agent_runs_collection(client).document(bill_doc_id).delete()
    if bill is None:
        return
    for item in bill.line_items:
        doc_id = ledger_doc_id(bill.vendor, bill.invoice_no, item.item_name, item.batch_no)
        purchase_ledger_collection(client).document(doc_id).delete()


def test_run_bill_processes_a_clean_format_b_bill_end_to_end(monkeypatch):
    # T31 milestone check (PRD Phase 5): a clean sample bill goes in,
    # resolves automatically, and actually lands in purchase_ledger -- with
    # no anomalies. Live Gemini turns + live Firestore -- PSPH12474.CSV is
    # confirmed clean (see
    # test_parse_and_normalize.py::test_format_b_csv_normalizes_and_validates),
    # so this is a real multi-turn agent loop over real sample data, not a
    # scripted pipeline.
    _stub_pharmacist_whatsapp(monkeypatch)
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

        # T54: the whole run shares one Cloud Trace id, recorded on the
        # `bills` doc (PRD S10 schema) for the status page (S7.11) to link.
        assert result.trace_id is not None
        assert len(result.trace_id) == 32
        int(result.trace_id, 16)

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

        bill_doc = bills_collection(client).document(result.bill_doc_id).get()
        assert bill_doc.to_dict()["trace_id"] == result.trace_id
    finally:
        _cleanup(result.bill, result.bill_doc_id)


def _history_bill(vendor, invoice_no, invoice_date, item_name, rate):
    taxable = round(rate, 2)
    tax = round(taxable * 0.06, 2)
    item = LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        item_name=item_name,
        batch_no="AT-HIST",
        expiry_date="2028-01-01",
        quantity=1.0,
        rate=rate,
        discount=0.0,
        taxable_value=taxable,
        tax_component_1_label="CGST",
        tax_component_1_rate=6.0,
        tax_component_1_amount=tax,
        tax_component_2_label="SGST",
        tax_component_2_rate=6.0,
        tax_component_2_amount=tax,
        mrp=rate * 1.5,
        line_total=round(taxable + 2 * tax, 2),
        hsn_code="3004",
        source_format="synthetic",
    )
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date=invoice_date,
        source_format="synthetic",
        line_items=[item],
        total_amount=item.line_total,
    )


def _format_b_csv(vendor, invoice_no, invoice_date_ddmmyyyy, item_name, qty, rate, mrp):
    taxable = round(qty * rate, 2)
    tax = round(taxable * 0.06, 2)
    bill_amount = round(taxable + 2 * tax, 2)
    text = (
        "Type, Code, Name, Packing, Quantity, Free, Selling Rate, MRP, Batch No., "
        "Exp. Date, Discount, VAT %, VAT Amt, TS %, TS Amt, Cess, Amount,HSN,PTR ,RackNo\n"
        f"H, Supplier,{vendor}\n"
        f"H, Inv.No.,{invoice_no}\n"
        f"H , Inv.Date,{invoice_date_ddmmyyyy}\n"
        f"D,999001,{item_name},{qty}TAB,{qty},,{rate:.2f},{mrp:.2f},AT-DEV-1,12/28,,"
        f"6.00,{tax:.2f},6.00,{tax:.2f},,{taxable:.2f},30049099,{rate:.2f},Z-1\n"
        f"F, Bill Amount,{bill_amount:.2f}\n"
    )
    return text.encode("latin-1")


def test_run_bill_takes_a_longer_investigation_chain_on_a_confirmed_price_deviation(monkeypatch):
    # T55: confirm a bill needing investigation produces a visibly longer,
    # distinguishable reasoning chain (and a distinct trace id, T54) next to
    # a clean bill's short one -- PRD S8's whole point. A dedicated
    # single-line-item vendor/item combo (not any real sample) avoids any
    # dependency on the T25 seed script having already run.
    _stub_pharmacist_whatsapp(monkeypatch)
    vendor = "AT Investigation Vendor"
    item_name = "AT INVESTIGATION ITEM"
    client = get_client()
    seed_doc_ids: list[str] = []
    for i, (date_iso, rate) in enumerate(
        [("2026-05-15", 20.00), ("2026-06-15", 20.50), ("2026-07-15", 21.00)], start=1
    ):
        seed_bill = _history_bill(vendor, f"AT-DEV-HIST-{i}", date_iso, item_name, rate)
        seed_doc_ids.extend(record_purchase(seed_bill, client=client))

    clean_data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    deviation_data = _format_b_csv(
        vendor, "AT-DEV-CURRENT", "20/08/2026", item_name, qty=10, rate=27.00, mrp=35.00
    )

    clean_result = None
    deviation_result = None
    try:
        clean_result = run_bill(clean_data)
        deviation_result = run_bill(deviation_data)

        # Distinguishable: the investigation run calls check_price_deviation
        # (a ~27% rise vs. the seeded history, matching PRD S7.4's worked
        # examples), the clean run doesn't need to -- and needs strictly
        # more turns to get there.
        clean_tools = [r.tool for r in clean_result.tool_call_history]
        deviation_tools = [r.tool for r in deviation_result.tool_call_history]
        assert "check_price_deviation" not in clean_tools
        assert "check_price_deviation" in deviation_tools
        assert deviation_result.turn_count > clean_result.turn_count

        # No other vendor has ever bought this made-up item, so
        # cross_check_other_vendors can't conclusively call it a market
        # move -- the agent can't autonomously resolve it and must park it.
        assert deviation_result.status == PENDING_PHARMACIST

        # Retrievable (T54): each run's whole reasoning chain lives under
        # its own trace id, and the two runs are genuinely different traces.
        assert clean_result.trace_id is not None
        assert deviation_result.trace_id is not None
        assert clean_result.trace_id != deviation_result.trace_id

        # T44: the parked run's context is durably serialized, keyed on the
        # same doc id as its `bills` record, with the actual question the
        # model asked over WhatsApp -- not just the terminal status.
        assert bills_collection(client).document(clean_result.bill_doc_id).get().exists
        assert not agent_runs_collection(client).document(clean_result.bill_doc_id).get().exists
        run_doc = agent_runs_collection(client).document(deviation_result.bill_doc_id).get().to_dict()
        assert run_doc["correlation_key"] == deviation_result.bill_doc_id
        assert run_doc["serialized_state"]["vendor"] == vendor
        assert run_doc["open_question"]
        assert "ask_pharmacist" in [c["tool"] for c in run_doc["tool_call_history"]]
    finally:
        for doc_id in seed_doc_ids:
            purchase_ledger_collection(client).document(doc_id).delete()
        if clean_result is not None:
            _cleanup(clean_result.bill, clean_result.bill_doc_id)
        if deviation_result is not None:
            _cleanup(deviation_result.bill, deviation_result.bill_doc_id)


@pytest.mark.skipif(not TEST_ADDRESS, reason="TEST_GMAIL_ADDRESS not set")
def test_run_bill_autonomously_sends_a_dispute_email_on_a_confirmed_vendor_error(monkeypatch):
    # T43 milestone check (PRD S11 Demo Bill 2 / S7.4's "vendor error" worked
    # example): a rate confirmed against this vendor's own last three
    # invoices, with no other vendor moving on the same item in the same
    # window -- the agent concludes a vendor-side pricing error, sends the
    # dispute email itself (dispute_gate authorizes it), and notifies the
    # pharmacist. Zero human involvement. Live Gemini + live Firestore +
    # live Gmail send, same pattern as T38's own live-send test -- gated on
    # TEST_GMAIL_ADDRESS so it doesn't fire on every run.
    notify_calls = []
    monkeypatch.setattr(
        agent_tools.pharmacist_whatsapp_mod,
        "notify_pharmacist",
        lambda vendor, reference, message: notify_calls.append((vendor, reference, message))
        or {"sent": True, "mode": "notify"},
    )
    monkeypatch.setattr(
        agent_tools.pharmacist_whatsapp_mod,
        "ask_pharmacist",
        lambda vendor, reference, question: {"sent": True, "mode": "ask"},
    )

    vendor = "AT Dispute Investigation Vendor"
    other_vendor = "AT Dispute Market Vendor"
    item_name = "AT DISPUTE ITEM"
    client = get_client()

    seed_doc_ids: list[str] = []
    for i, (date_iso, rate) in enumerate(
        [("2026-05-15", 20.00), ("2026-06-15", 20.50), ("2026-07-15", 21.00)], start=1
    ):
        seed_bill = _history_bill(vendor, f"AT-DISP-HIST-{i}", date_iso, item_name, rate)
        seed_doc_ids.extend(record_purchase(seed_bill, client=client))
    # A second vendor, stable pricing on the same item within the 60-day
    # cross-check window -- the "conclusive no market movement" signal PRD
    # S7.5 condition 2 needs before an autonomous dispute can fire.
    for date_iso in ("2026-07-01", "2026-08-01"):
        market_bill = _history_bill(other_vendor, f"AT-DISP-MARKET-{date_iso}", date_iso, item_name, 15.00)
        seed_doc_ids.extend(record_purchase(market_bill, client=client))

    vendor_doc_id = vendor_directory_doc_id(vendor)
    set_vendor_email(vendor, TEST_ADDRESS, client=client)
    set_pharmacist_email(TEST_ADDRESS, client=client)

    # ~28% above this vendor's own last rate (PRD S7.4 cites 26-27% moves),
    # at a quantity that clears the ₹500 dispute floor.
    deviation_data = _format_b_csv(
        vendor, "AT-DISP-CURRENT", "28/08/2026", item_name, qty=200, rate=26.90, mrp=35.00
    )

    result = None
    dispute_log_doc_id = None
    try:
        result = run_bill(deviation_data)

        tools_called = [r.tool for r in result.tool_call_history]
        assert "check_price_deviation" in tools_called
        assert "cross_check_other_vendors" in tools_called
        assert "send_dispute_email" in tools_called

        assert result.bill is not None
        assert result.status == RESOLVED

        dispute_log_doc_id = email_log_doc_id(vendor, result.bill.invoice_no, "dispute")
        log_doc = client.collection("email_log").document(dispute_log_doc_id).get()
        assert log_doc.exists
        log_data = log_doc.to_dict()
        assert log_data["mode"] == "dispute"
        assert log_data["to"] == TEST_ADDRESS
        assert log_data["cc"] == TEST_ADDRESS

        assert notify_calls  # pharmacist is told, even though no human acted

        item = result.bill.line_items[0]
        doc_id = ledger_doc_id(vendor, result.bill.invoice_no, item.item_name, item.batch_no)
        assert purchase_ledger_collection(client).document(doc_id).get().exists
    finally:
        for doc_id in seed_doc_ids:
            purchase_ledger_collection(client).document(doc_id).delete()
        vendor_directory_collection(client).document(vendor_doc_id).delete()
        if dispute_log_doc_id:
            client.collection("email_log").document(dispute_log_doc_id).delete()
        if result is not None:
            _cleanup(result.bill, result.bill_doc_id)


def test_run_bill_parks_on_turn_cap_exhaustion(monkeypatch):
    # PRD S7.10 turn-cap guardrail. The clean SUMMIT PHARMA bill genuinely
    # needs several turns (detect -> parse -> check_duplicate -> record ->
    # finish) to reach a real conclusion, so max_turns=1 is guaranteed to
    # trip the cap regardless of which tool the model picks first -- a
    # deterministic way to exercise a live cutoff without an artificial
    # infinite-loop tool.
    _stub_pharmacist_whatsapp(monkeypatch)
    data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
    result = run_bill(data, max_turns=1)
    try:
        assert result.turn_count == 1
        assert result.status == PENDING_PHARMACIST
        assert any("turn cap" in finding for finding in result.findings)

        # T44: parked even though the model never got to call ask_pharmacist
        # -- open_question is None, not a missing/failed serialize.
        run_doc = agent_runs_collection(get_client()).document(result.bill_doc_id).get().to_dict()
        assert run_doc["open_question"] is None
        assert run_doc["tool_call_history"]
    finally:
        _cleanup(result.bill, result.bill_doc_id)


def test_resume_bill_continues_a_parked_run_to_resolution(monkeypatch):
    # T45/PRD S7.6 milestone (Demo Bill 4's mechanism): a bill parks on a
    # confirmed-but-unresolved price deviation, then a WhatsApp reply
    # resumes it -- on a fresh ADK session/runner, not the one that parked
    # it -- and it resolves and actually records the purchase, same as
    # T47's "days later, different container" scenario but without needing
    # a real webhook round-trip to exercise the resume mechanics.
    _stub_pharmacist_whatsapp(monkeypatch)
    vendor = "AT Resume Vendor"
    item_name = "AT RESUME ITEM"
    client = get_client()
    seed_doc_ids: list[str] = []
    for i, (date_iso, rate) in enumerate(
        [("2026-05-15", 20.00), ("2026-06-15", 20.50), ("2026-07-15", 21.00)], start=1
    ):
        seed_bill = _history_bill(vendor, f"AT-RESUME-HIST-{i}", date_iso, item_name, rate)
        seed_doc_ids.extend(record_purchase(seed_bill, client=client))

    deviation_data = _format_b_csv(
        vendor, "AT-RESUME-CURRENT", "20/08/2026", item_name, qty=10, rate=27.00, mrp=35.00
    )

    parked_result = None
    resumed_result = None
    try:
        parked_result = run_bill(deviation_data)
        assert parked_result.status == PENDING_PHARMACIST

        resumed_result = resume_bill(
            parked_result.bill_doc_id,
            "Yes, that rate is correct -- go ahead and record it.",
        )

        assert resumed_result is not None
        assert resumed_result.status == RESOLVED
        assert resumed_result.bill_doc_id == parked_result.bill_doc_id

        # T44/T45: the resumed run's tool-call history spans the whole
        # bill, not just the segment since resuming.
        resumed_tools = [r.tool for r in resumed_result.tool_call_history]
        assert "check_price_deviation" in resumed_tools
        assert "record_purchase" in resumed_tools

        run_doc = agent_runs_collection(client).document(parked_result.bill_doc_id).get().to_dict()
        assert run_doc["resumed_at"] is not None

        bill_doc = bills_collection(client).document(parked_result.bill_doc_id).get().to_dict()
        assert bill_doc["status"] == RESOLVED
    finally:
        for doc_id in seed_doc_ids:
            purchase_ledger_collection(client).document(doc_id).delete()
        if parked_result is not None:
            _cleanup(parked_result.bill, parked_result.bill_doc_id)


def test_resume_bill_returns_none_for_an_unknown_agent_run_id():
    assert resume_bill("no-such-agent-run", "any reply") is None


def test_resume_bill_with_file_reparses_a_vendor_resend_to_resolution(monkeypatch):
    # T46/PRD S7.6: a pending_vendor park (file unreadable by any tool)
    # resumes once the vendor resends a readable file over Gmail -- same
    # durable-pause mechanism as T45's WhatsApp reply, but re-seeding
    # _file_bytes and rerunning the normal parse kickoff instead of
    # answering a question, since there was never a bill to reason over.
    _stub_pharmacist_whatsapp(monkeypatch)
    unreadable_data = b"this is not a recognizable bill in any known format, just plain garbage text"
    vendor_hint = "at-resend-vendor@example.com"

    parked_result = None
    resumed_result = None
    try:
        parked_result = run_bill(unreadable_data, vendor_hint=vendor_hint)
        assert parked_result.status == PENDING_VENDOR

        clean_data = (SAMPLES_DIR / "PSPH12474.CSV").read_bytes()
        resumed_result = resume_bill_with_file(parked_result.bill_doc_id, clean_data)

        assert resumed_result is not None
        assert resumed_result.status == RESOLVED
        assert resumed_result.bill is not None
        assert resumed_result.bill.vendor == "SUMMIT PHARMA"

        # T46: the resend parsed into a real vendor/invoice_no key,
        # different from the placeholder id the unreadable park used --
        # the placeholder must be retired, not left behind as a phantom
        # pending_vendor bill.
        assert resumed_result.bill_doc_id != parked_result.bill_doc_id
        assert not bills_collection(get_client()).document(parked_result.bill_doc_id).get().exists
        assert not agent_runs_collection(get_client()).document(parked_result.bill_doc_id).get().exists

        bill_doc = bills_collection(get_client()).document(resumed_result.bill_doc_id).get().to_dict()
        assert bill_doc["status"] == RESOLVED

        resumed_tools = [r.tool for r in resumed_result.tool_call_history]
        assert "detect_format" in resumed_tools  # from the original unreadable attempt
        assert "record_purchase" in resumed_tools  # from the successful resend
    finally:
        if resumed_result is not None:
            _cleanup(resumed_result.bill, resumed_result.bill_doc_id)
        elif parked_result is not None:
            _cleanup(parked_result.bill, parked_result.bill_doc_id)


def test_resume_bill_records_pharmacist_approval_and_a_later_bill_stops_being_flagged(monkeypatch):
    # T58/PRD S7.7 milestone: once the pharmacist approves a price rise for
    # a vendor+item, the agent stops flagging it -- confirmed here across
    # two separate runs (the resume that captures the approval, then a
    # brand-new bill at the same rate that must resolve without asking
    # again), not just within one run's session state.
    _stub_pharmacist_whatsapp(monkeypatch)
    vendor = "AT Memory Vendor"
    item_name = "AT MEMORY ITEM"
    client = get_client()
    seed_doc_ids: list[str] = []
    for i, (date_iso, rate) in enumerate(
        [("2026-05-15", 20.00), ("2026-06-15", 20.50), ("2026-07-15", 21.00)], start=1
    ):
        seed_bill = _history_bill(vendor, f"AT-MEM-HIST-{i}", date_iso, item_name, rate)
        seed_doc_ids.extend(record_purchase(seed_bill, client=client))

    deviation_data = _format_b_csv(
        vendor, "AT-MEM-CURRENT", "20/08/2026", item_name, qty=10, rate=27.00, mrp=35.00
    )
    resolution_id = resolution_doc_id(vendor, item_name)

    parked_result = None
    resumed_result = None
    later_result = None
    try:
        parked_result = run_bill(deviation_data)
        assert parked_result.status == PENDING_PHARMACIST

        resumed_result = resume_bill(
            parked_result.bill_doc_id,
            "Yes, that rate is correct and approved -- go ahead and record it, "
            "and you don't need to flag this again.",
        )
        assert resumed_result is not None
        assert resumed_result.status == RESOLVED
        resumed_tools = [r.tool for r in resumed_result.tool_call_history]
        assert "record_pharmacist_resolution" in resumed_tools

        resolution_doc = pharmacist_resolutions_collection(client).document(resolution_id).get()
        assert resolution_doc.exists
        assert resolution_doc.to_dict()["decision"] == "approved"

        # A brand-new bill, same vendor+item+rate: check_price_deviation now
        # reports within_normal because of the standing approval, so the
        # model resolves without asking the pharmacist a second time.
        repeat_data = _format_b_csv(
            vendor, "AT-MEM-REPEAT", "25/08/2026", item_name, qty=10, rate=27.00, mrp=35.00
        )
        later_result = run_bill(repeat_data)
        assert later_result.status == RESOLVED
        later_tools = [r.tool for r in later_result.tool_call_history]
        assert "ask_pharmacist" not in later_tools
    finally:
        for doc_id in seed_doc_ids:
            purchase_ledger_collection(client).document(doc_id).delete()
        pharmacist_resolutions_collection(client).document(resolution_id).delete()
        if parked_result is not None:
            _cleanup(parked_result.bill, parked_result.bill_doc_id)
        if later_result is not None:
            _cleanup(later_result.bill, later_result.bill_doc_id)
