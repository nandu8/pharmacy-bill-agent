import dataclasses

from pharmacy_agent.agent.loop import ToolCallRecord
from pharmacy_agent.agent.resume_state import (
    _pick_most_recent,
    deserialize_bill,
    find_resumable_run,
    get_agent_run,
    mark_resumed,
    serialize_run_state,
)
from pharmacy_agent.agent.terminal import PENDING_PHARMACIST, PENDING_VENDOR
from pharmacy_agent.firestore_client import agent_runs_collection, get_client
from pharmacy_agent.formats.schema import Bill, LineItem


def _make_bill(vendor="ARS Resume Vendor", invoice_no="ARS-INV-1"):
    item = LineItem(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-20",
        item_name="ARS RESUME ITEM",
        batch_no="B1",
        expiry_date="2027-01-01",
        quantity=1.0,
        rate=10.0,
        discount=0.0,
        taxable_value=10.0,
        tax_component_1_label="CGST",
        tax_component_1_rate=6.0,
        tax_component_1_amount=0.6,
        tax_component_2_label="SGST",
        tax_component_2_rate=6.0,
        tax_component_2_amount=0.6,
        mrp=15.0,
        line_total=11.2,
        hsn_code="3004",
        source_format="format_a",
    )
    return Bill(
        vendor=vendor,
        invoice_no=invoice_no,
        invoice_date="2026-08-20",
        source_format="format_a",
        line_items=[item],
        total_amount=11.2,
    )


def test_serialize_run_state_captures_open_question_from_ask_pharmacist_call():
    client = get_client()
    bill = _make_bill()
    tool_call_history = [
        ToolCallRecord(tool="detect_format", args={}),
        ToolCallRecord(tool="parse_csv", args={}),
        ToolCallRecord(
            tool="ask_pharmacist",
            args={"question": "Is a 27% rate rise on AMLODIPINE expected?"},
        ),
        ToolCallRecord(tool="finish", args={"status": "pending_pharmacist", "summary": "unsure"}),
    ]
    doc_id = serialize_run_state(
        bill=bill,
        status=PENDING_PHARMACIST,
        findings=["confirmed deviation, no market movement signal"],
        tool_call_history=tool_call_history,
        bill_doc_id="ars-test-doc-1",
        client=client,
    )
    try:
        assert doc_id == "ars-test-doc-1"
        data = agent_runs_collection(client).document(doc_id).get().to_dict()
        assert data["bill_id"] == "ars-test-doc-1"
        assert data["correlation_key"] == "ars-test-doc-1"
        assert data["open_question"] == "Is a 27% rate rise on AMLODIPINE expected?"
        assert data["serialized_state"]["vendor"] == bill.vendor
        assert data["serialized_state"]["invoice_no"] == bill.invoice_no
        assert data["serialized_state"]["source_format"] == bill.source_format
        assert data["serialized_state"]["line_items"] == [
            dataclasses.asdict(bill.line_items[0])
        ]
        assert data["serialized_state"]["findings"] == [
            "confirmed deviation, no market movement signal"
        ]
        assert data["tool_call_history"] == [
            {"tool": "detect_format", "args": {}},
            {"tool": "parse_csv", "args": {}},
            {
                "tool": "ask_pharmacist",
                "args": {"question": "Is a 27% rate rise on AMLODIPINE expected?"},
            },
            {"tool": "finish", "args": {"status": "pending_pharmacist", "summary": "unsure"}},
        ]
        assert data["resumed_at"] is None
        assert data["paused_at"] is not None
    finally:
        agent_runs_collection(client).document(doc_id).delete()


def test_serialize_run_state_without_bill_has_no_open_question_when_none_asked():
    client = get_client()
    tool_call_history = [
        ToolCallRecord(tool="detect_format", args={}),
        ToolCallRecord(tool="finish", args={"status": "pending_vendor", "summary": "unreadable"}),
    ]
    doc_id = serialize_run_state(
        bill=None,
        status=PENDING_VENDOR,
        findings=["file unreadable by any parser"],
        tool_call_history=tool_call_history,
        bill_doc_id="ars-test-doc-2",
        client=client,
    )
    try:
        data = agent_runs_collection(client).document(doc_id).get().to_dict()
        assert data["open_question"] is None
        assert data["serialized_state"]["vendor"] is None
        assert data["serialized_state"]["line_items"] == []
    finally:
        agent_runs_collection(client).document(doc_id).delete()


def test_deserialize_bill_reconstructs_bill_and_line_items():
    client = get_client()
    bill = _make_bill()
    doc_id = serialize_run_state(
        bill=bill,
        status=PENDING_PHARMACIST,
        findings=[],
        tool_call_history=[],
        bill_doc_id="ars-test-doc-3",
        client=client,
    )
    try:
        serialized_state = agent_runs_collection(client).document(doc_id).get().to_dict()["serialized_state"]
        rebuilt = deserialize_bill(serialized_state)
        assert rebuilt == bill
    finally:
        agent_runs_collection(client).document(doc_id).delete()


def test_deserialize_bill_returns_none_when_no_vendor():
    assert deserialize_bill({"vendor": None, "line_items": []}) is None


def test_get_agent_run_roundtrips_and_returns_none_when_missing():
    client = get_client()
    assert get_agent_run("ars-does-not-exist", client=client) is None

    doc_id = serialize_run_state(
        bill=None,
        status=PENDING_VENDOR,
        findings=["f"],
        tool_call_history=[],
        bill_doc_id="ars-test-doc-4",
        client=client,
    )
    try:
        run = get_agent_run(doc_id, client=client)
        assert run["bill_id"] == doc_id
        assert run["serialized_state"]["findings"] == ["f"]
    finally:
        agent_runs_collection(client).document(doc_id).delete()


def test_mark_resumed_sets_timestamp():
    client = get_client()
    doc_id = serialize_run_state(
        bill=None,
        status=PENDING_VENDOR,
        findings=[],
        tool_call_history=[],
        bill_doc_id="ars-test-doc-5",
        client=client,
    )
    try:
        assert get_agent_run(doc_id, client=client)["resumed_at"] is None
        mark_resumed(doc_id, client=client)
        assert get_agent_run(doc_id, client=client)["resumed_at"] is not None
    finally:
        agent_runs_collection(client).document(doc_id).delete()


class _FakeSnapshot:
    def __init__(self, update_time, payload):
        self.update_time = update_time
        self._payload = payload

    def to_dict(self):
        return self._payload


def test_pick_most_recent_returns_none_for_empty_list():
    assert _pick_most_recent([]) is None


def test_pick_most_recent_picks_the_latest_update_time():
    older = _FakeSnapshot(1, {"bill_id": "older"})
    newer = _FakeSnapshot(2, {"bill_id": "newer"})
    assert _pick_most_recent([older, newer]) is newer
    assert _pick_most_recent([newer, older]) is newer


def test_find_resumable_run_returns_the_freshly_parked_pending_pharmacist_run():
    # This queries the live agent_runs collection with no scoping beyond
    # resumed_at == None (plus the status filter), so in principle a
    # concurrent write elsewhere in this shared Firestore project could
    # race it -- same tradeoff test_agent_loop.py's T55 notes already
    # accept for this project's live-infra test style.
    client = get_client()
    doc_id = serialize_run_state(
        bill=None,
        status=PENDING_PHARMACIST,
        findings=["freshly parked"],
        tool_call_history=[],
        bill_doc_id="ars-test-doc-6",
        client=client,
    )
    try:
        run = find_resumable_run(client=client)
        assert run is not None
        assert run["bill_id"] == doc_id
    finally:
        agent_runs_collection(client).document(doc_id).delete()


def test_find_resumable_run_excludes_pending_vendor_by_default():
    # A pending_vendor run needs a corrected file over Gmail, not a
    # WhatsApp text reply -- resuming it is T46's job, not this one.
    client = get_client()
    doc_id = serialize_run_state(
        bill=None,
        status=PENDING_VENDOR,
        findings=["file unreadable"],
        tool_call_history=[],
        bill_doc_id="ars-test-doc-7",
        client=client,
    )
    try:
        run = find_resumable_run(client=client)
        assert run is None or run["bill_id"] != doc_id
    finally:
        agent_runs_collection(client).document(doc_id).delete()
