import hashlib
import hmac

from pharmacy_agent.firestore_client import whatsapp_inbound_collection
from pharmacy_agent.whatsapp_webhook import (
    extract_messages,
    record_inbound_message,
    verify_signature,
    verify_subscription_token,
)


def _text_message_payload(message_id="wamid.test1", from_number="15550001111", body="yes, approved"):
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


def test_verify_subscription_token_matches():
    assert verify_subscription_token("subscribe", "secret123", expected_token="secret123") is True


def test_verify_subscription_token_rejects_wrong_mode_or_token():
    assert verify_subscription_token("unsubscribe", "secret123", expected_token="secret123") is False
    assert verify_subscription_token("subscribe", "wrong", expected_token="secret123") is False


def test_verify_signature_accepts_valid_hmac():
    body = b'{"object":"whatsapp_business_account"}'
    app_secret = "test-app-secret"
    signature = "sha256=" + hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_signature(app_secret, body, signature) is True


def test_verify_signature_rejects_tampered_body():
    body = b'{"object":"whatsapp_business_account"}'
    app_secret = "test-app-secret"
    signature = "sha256=" + hmac.new(app_secret.encode("utf-8"), b"tampered", hashlib.sha256).hexdigest()
    assert verify_signature(app_secret, body, signature) is False


def test_verify_signature_rejects_missing_or_malformed_header():
    assert verify_signature("secret", b"{}", None) is False
    assert verify_signature("secret", b"{}", "not-sha256=abc") is False


def test_extract_messages_returns_text_messages():
    messages = extract_messages(_text_message_payload())
    assert messages == [
        {
            "message_id": "wamid.test1",
            "from": "15550001111",
            "body": "yes, approved",
            "timestamp": "1700000000",
        }
    ]


def test_extract_messages_skips_status_updates_and_non_text():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {"value": {"statuses": [{"id": "wamid.status1", "status": "delivered"}]}},
                    {"value": {"messages": [{"from": "1", "id": "wamid.img", "type": "image"}]}},
                ]
            }
        ],
    }
    assert extract_messages(payload) == []


def test_record_inbound_message_is_idempotent():
    from pharmacy_agent.firestore_client import get_client

    client = get_client()
    message = {
        "message_id": "wamid.dedup-test",
        "from": "15550002222",
        "body": "hi",
        "timestamp": "1700000001",
    }
    doc_ref = whatsapp_inbound_collection(client).document(message["message_id"])
    try:
        first = record_inbound_message(message, client=client)
        second = record_inbound_message(message, client=client)
        assert first is True
        assert second is False
        assert doc_ref.get().to_dict() == message
    finally:
        doc_ref.delete()
