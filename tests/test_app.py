import base64
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


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
