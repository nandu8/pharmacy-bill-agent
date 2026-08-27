import pytest

from pharmacy_agent.meta_whatsapp_client import (
    META_WHATSAPP_PHONE_NUMBER_ID_ENV_VAR,
    send_whatsapp,
)


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


def test_send_whatsapp_raises_when_phone_number_id_unset(monkeypatch):
    monkeypatch.delenv(META_WHATSAPP_PHONE_NUMBER_ID_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError):
        send_whatsapp(to="+15550002222", body="hi", client=_FakeHttpClient())


def test_send_whatsapp_strips_plus_and_returns_message_id(monkeypatch):
    monkeypatch.setenv(META_WHATSAPP_PHONE_NUMBER_ID_ENV_VAR, "1350166468169393")
    fake_client = _FakeHttpClient()

    result = send_whatsapp(to="+15550002222", body="test message", client=fake_client)

    assert result == {"sid": "wamid.fake", "status": "sent"}
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["url"] == "https://graph.facebook.com/v21.0/1350166468169393/messages"
    assert call["json"] == {
        "messaging_product": "whatsapp",
        "to": "15550002222",
        "type": "text",
        "text": {"body": "test message"},
    }
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Authorization"].startswith("Bearer ")
