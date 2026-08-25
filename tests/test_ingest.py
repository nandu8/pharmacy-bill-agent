import base64
import json

import pytest

from pharmacy_agent.firestore_client import get_client
from pharmacy_agent.gmail_history import GMAIL_WATCH_CONFIG_DOC
from pharmacy_agent.ingest import decode_pubsub_message, handle_pubsub_push


def _envelope(email_address="pharmacist@example.com", history_id="999"):
    payload = json.dumps({"emailAddress": email_address, "historyId": history_id}).encode("utf-8")
    return {
        "message": {
            "data": base64.b64encode(payload).decode("utf-8"),
            "messageId": "test-message-id",
            "publishTime": "2026-08-25T00:00:00Z",
        },
        "subscription": "projects/pharmacy-bill-agent/subscriptions/gmail-notifications-push",
    }


def test_decode_pubsub_message_parses_base64_json_data():
    result = decode_pubsub_message(_envelope(history_id="12345"))
    assert result == {"emailAddress": "pharmacist@example.com", "historyId": "12345"}


class _FakeGmailService:
    """Stands in for gmail_history.fetch_new_attachments's `service` arg --
    ingest.py's own logic (watermark handling, calling run_bill_fn) is what
    these tests exercise; gmail_history's real Gmail-API behavior is
    already verified live in test_gmail_history.py."""


def test_first_notification_establishes_baseline_without_processing(monkeypatch):
    client = get_client()
    config_doc = client.collection("config").document(GMAIL_WATCH_CONFIG_DOC)
    config_doc.delete()

    calls = []

    def fake_fetch(start_history_id, service=None):
        calls.append(start_history_id)
        return [{"message_id": "m1", "sender": "x", "filename": "f.csv", "bytes": b"x"}]

    monkeypatch.setattr("pharmacy_agent.ingest.fetch_new_attachments", fake_fetch)

    try:
        results = handle_pubsub_push(_envelope(history_id="1000"), firestore_client=client)
        assert results == []
        assert calls == []  # never fetched -- nothing to diff against yet
        assert config_doc.get().to_dict()["last_history_id"] == "1000"
    finally:
        config_doc.delete()


def test_new_attachments_are_run_and_watermark_advances(monkeypatch):
    client = get_client()
    config_doc = client.collection("config").document(GMAIL_WATCH_CONFIG_DOC)
    config_doc.set({"last_history_id": "500"})

    fetch_calls = []

    def fake_fetch(start_history_id, service=None):
        fetch_calls.append(start_history_id)
        return [
            {"message_id": "m1", "sender": "vendor@example.com", "filename": "bill.csv", "bytes": b"csv-bytes"},
        ]

    monkeypatch.setattr("pharmacy_agent.ingest.fetch_new_attachments", fake_fetch)

    run_calls = []

    def fake_run_bill(file_bytes, vendor_hint=""):
        run_calls.append((file_bytes, vendor_hint))
        return {"status": "resolved"}

    try:
        results = handle_pubsub_push(
            _envelope(history_id="600"),
            firestore_client=client,
            run_bill_fn=fake_run_bill,
        )
        assert fetch_calls == ["500"]
        assert run_calls == [(b"csv-bytes", "vendor@example.com")]
        assert results == [
            {
                "message_id": "m1",
                "sender": "vendor@example.com",
                "filename": "bill.csv",
                "run_result": {"status": "resolved"},
            }
        ]
        assert config_doc.get().to_dict()["last_history_id"] == "600"
    finally:
        config_doc.delete()


def test_no_new_attachments_still_advances_watermark(monkeypatch):
    client = get_client()
    config_doc = client.collection("config").document(GMAIL_WATCH_CONFIG_DOC)
    config_doc.set({"last_history_id": "700"})

    monkeypatch.setattr("pharmacy_agent.ingest.fetch_new_attachments", lambda start_history_id, service=None: [])

    try:
        results = handle_pubsub_push(_envelope(history_id="701"), firestore_client=client)
        assert results == []
        assert config_doc.get().to_dict()["last_history_id"] == "701"
    finally:
        config_doc.delete()


def test_malformed_envelope_raises():
    with pytest.raises(KeyError):
        decode_pubsub_message({"not_a_message": True})
