import base64
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from pharmacy_agent.app import app


def _envelope(history_id="42"):
    payload = json.dumps({"emailAddress": "pharmacist@example.com", "historyId": history_id}).encode("utf-8")
    return {
        "message": {
            "data": base64.b64encode(payload).decode("utf-8"),
            "messageId": "test-message-id",
            "publishTime": "2026-08-25T00:00:00Z",
        },
        "subscription": "projects/pharmacy-bill-agent/subscriptions/gmail-notifications-push",
    }


def _whatsapp_payload(message_id="wamid.test1", from_number="15550001111", body="yes, approved"):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-id",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": from_number,
                                    "id": message_id,
                                    "timestamp": "1700000000",
                                    "type": "text",
                                    "text": {"body": body},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_page_is_reachable_and_returns_html():
    # T56/T57: this route must need no auth at all -- a judge without GCP
    # IAM has to be able to open it.
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "Pharmacy Bill Agent" in response.text


def test_push_without_audience_configured_skips_auth_and_processes(monkeypatch):
    # No PUBSUB_PUSH_AUDIENCE set (local dev) -- verification is skipped
    # rather than locking out local testing; production always sets it.
    monkeypatch.delenv("PUBSUB_PUSH_AUDIENCE", raising=False)
    monkeypatch.setattr("pharmacy_agent.app.handle_pubsub_push", lambda envelope: [{"ok": True}])

    client = TestClient(app)
    response = client.post("/pubsub/push", json=_envelope())
    assert response.status_code == 200
    assert response.json() == {"processed": 1}


def test_push_rejects_malformed_envelope(monkeypatch):
    monkeypatch.delenv("PUBSUB_PUSH_AUDIENCE", raising=False)
    client = TestClient(app)
    response = client.post("/pubsub/push", json={"not_a_message": True})
    assert response.status_code == 400


def test_push_rejects_missing_bearer_token_when_audience_configured(monkeypatch):
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://pharmacy-bill-agent.example.run.app/pubsub/push")
    client = TestClient(app)
    response = client.post("/pubsub/push", json=_envelope())
    assert response.status_code == 401


def test_push_rejects_invalid_bearer_token_when_audience_configured(monkeypatch):
    monkeypatch.setenv("PUBSUB_PUSH_AUDIENCE", "https://pharmacy-bill-agent.example.run.app/pubsub/push")
    client = TestClient(app)
    response = client.post(
        "/pubsub/push",
        json=_envelope(),
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert response.status_code == 401


def test_whatsapp_webhook_verification_succeeds_with_matching_token(monkeypatch):
    monkeypatch.setattr("pharmacy_agent.app.webhook_verify_token", lambda: "expected-token")
    client = TestClient(app)
    response = client.get(
        "/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "expected-token", "hub.challenge": "12345"},
    )
    assert response.status_code == 200
    assert response.text == "12345"


def test_whatsapp_webhook_verification_rejects_wrong_token(monkeypatch):
    monkeypatch.setattr("pharmacy_agent.app.webhook_verify_token", lambda: "expected-token")
    client = TestClient(app)
    response = client.get(
        "/whatsapp/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "12345"},
    )
    assert response.status_code == 403


def test_whatsapp_webhook_receive_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr("pharmacy_agent.app.webhook_app_secret", lambda: "test-secret")
    client = TestClient(app)
    response = client.post(
        "/whatsapp/webhook",
        json={"object": "whatsapp_business_account", "entry": []},
        headers={"x-hub-signature-256": "sha256=deadbeef"},
    )
    assert response.status_code == 401


def test_whatsapp_webhook_receive_rejects_missing_signature(monkeypatch):
    monkeypatch.setattr("pharmacy_agent.app.webhook_app_secret", lambda: "test-secret")
    client = TestClient(app)
    response = client.post("/whatsapp/webhook", json={"object": "whatsapp_business_account", "entry": []})
    assert response.status_code == 401


def test_whatsapp_webhook_receive_records_valid_message(monkeypatch):
    monkeypatch.setattr("pharmacy_agent.app.webhook_app_secret", lambda: "test-secret")
    recorded = []
    monkeypatch.setattr(
        "pharmacy_agent.app.record_inbound_message",
        lambda message: recorded.append(message) or True,
    )

    body = json.dumps(_whatsapp_payload()).encode("utf-8")
    signature = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()

    client = TestClient(app)
    response = client.post(
        "/whatsapp/webhook",
        content=body,
        headers={"x-hub-signature-256": signature, "content-type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"received": 1, "recorded": 1}
    assert recorded == [
        {
            "message_id": "wamid.test1",
            "from": "15550001111",
            "body": "yes, approved",
            "timestamp": "1700000000",
        }
    ]
