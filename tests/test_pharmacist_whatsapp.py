from pharmacy_agent.firestore_client import get_client
from pharmacy_agent.pharmacist_whatsapp import (
    ask_pharmacist,
    notify_pharmacist,
    whatsapp_log_doc_id,
)
from pharmacy_agent.vendor_directory import config_collection, set_pharmacist_whatsapp

TEST_NUMBER = "+15550009999"


class _FakeResponse:
    def __init__(self, message_id="wamid.fake"):
        self._message_id = message_id

    def raise_for_status(self):
        pass

    def json(self):
        return {"messages": [{"id": self._message_id}]}


class _FakeHttpClient:
    def __init__(self):
        self.calls = []

    def post(self, url, headers, json):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse()


def test_missing_pharmacist_config_does_not_send():
    client = get_client()
    vendor = "PW No Pharmacist Vendor"
    log_doc_id = whatsapp_log_doc_id(vendor, "PW-INV-1", "notify")
    config_doc = config_collection(client).document("pharmacist")
    previous = config_doc.get()
    previous_data = previous.to_dict() if previous.exists else None
    try:
        config_doc.delete()

        result = notify_pharmacist(
            vendor=vendor,
            reference="PW-INV-1",
            message="m",
            client=client,
            messaging_client=_FakeHttpClient(),
        )
        assert result == {
            "sent": False,
            "reason": "pharmacist_not_configured",
            "mode": "notify",
            "log_id": log_doc_id,
        }
    finally:
        client.collection("whatsapp_log").document(log_doc_id).delete()
        if previous_data is not None:
            config_doc.set(previous_data)


def test_notify_and_ask_send_and_dedupe_on_repeat(monkeypatch):
    monkeypatch.setenv("META_WHATSAPP_PHONE_NUMBER_ID", "1350166468169393")
    client = get_client()
    vendor = "PW Live Vendor"
    log_doc_ids = [
        whatsapp_log_doc_id(vendor, "PW-INV-2", "notify"),
        whatsapp_log_doc_id(vendor, "PW-INV-3", "ask"),
    ]
    config_doc = config_collection(client).document("pharmacist")
    previous = config_doc.get()
    previous_data = previous.to_dict() if previous.exists else None
    try:
        set_pharmacist_whatsapp(TEST_NUMBER, client=client)

        fake_client = _FakeHttpClient()
        notify_result = notify_pharmacist(
            vendor=vendor,
            reference="PW-INV-2",
            message="Bill processed cleanly.",
            client=client,
            messaging_client=fake_client,
        )
        assert notify_result["sent"] is True
        assert notify_result["to"] == TEST_NUMBER
        assert notify_result["message_id"] == "wamid.fake"

        ask_result = ask_pharmacist(
            vendor=vendor,
            reference="PW-INV-3",
            question="Is a 27% rate rise on item X expected?",
            client=client,
            messaging_client=fake_client,
        )
        assert ask_result["sent"] is True
        assert len(fake_client.calls) == 2
        assert fake_client.calls[0]["json"]["to"] == TEST_NUMBER.lstrip("+")
        assert fake_client.calls[0]["json"]["text"]["body"] == "Bill processed cleanly."

        repeat_result = notify_pharmacist(
            vendor=vendor,
            reference="PW-INV-2",
            message="This should never be sent -- dedup should short-circuit first.",
            client=client,
            messaging_client=fake_client,
        )
        assert repeat_result == {
            "sent": False,
            "reason": "already_sent",
            "mode": "notify",
            "log_id": log_doc_ids[0],
        }
        assert len(fake_client.calls) == 2
    finally:
        for log_doc_id in log_doc_ids:
            client.collection("whatsapp_log").document(log_doc_id).delete()
        if previous_data is not None:
            config_doc.set(previous_data)
        else:
            config_doc.delete()
