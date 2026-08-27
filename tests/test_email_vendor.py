from pharmacy_agent.email_vendor import email_log_doc_id, email_vendor
from pharmacy_agent.firestore_client import get_client
from pharmacy_agent.vendor_directory import (
    set_pharmacist_email,
    set_vendor_email,
    vendor_directory_collection,
    vendor_directory_doc_id,
)

TEST_ADDRESS = "[redacted-personal-email]"


def test_unknown_vendor_does_not_send_and_is_not_logged():
    client = get_client()
    log_doc_id = email_log_doc_id("EV Unknown Vendor", "INV-1", "resend")
    try:
        result = email_vendor(
            vendor="EV Unknown Vendor",
            reference="INV-1",
            mode="resend",
            subject="s",
            body="b",
            client=client,
        )
        assert result == {
            "sent": False,
            "reason": "vendor_not_in_directory",
            "mode": "resend",
            "log_id": log_doc_id,
        }
    finally:
        client.collection("email_log").document(log_doc_id).delete()


def test_resend_and_dispute_send_live_and_dedupe_on_repeat():
    client = get_client()
    vendor = "EV Live Vendor"
    vendor_doc_id = vendor_directory_doc_id(vendor)
    log_doc_ids = [
        email_log_doc_id(vendor, "EV-INV-1", "resend"),
        email_log_doc_id(vendor, "EV-INV-2", "dispute"),
    ]
    try:
        set_vendor_email(vendor, TEST_ADDRESS, client=client)
        set_pharmacist_email(TEST_ADDRESS, client=client)

        resend_result = email_vendor(
            vendor=vendor,
            reference="EV-INV-1",
            mode="resend",
            subject="[pharmacy-agent test] resend request",
            body="Live test: please resend EV-INV-1 -- safe to ignore.",
            client=client,
        )
        assert resend_result["sent"] is True
        assert resend_result["to"] == TEST_ADDRESS
        assert resend_result["cc"] == TEST_ADDRESS
        assert resend_result["gmail_message_id"]

        dispute_result = email_vendor(
            vendor=vendor,
            reference="EV-INV-2",
            mode="dispute",
            subject="[pharmacy-agent test] dispute",
            body="Live test: pricing discrepancy on EV-INV-2 -- safe to ignore.",
            client=client,
        )
        assert dispute_result["sent"] is True

        repeat_result = email_vendor(
            vendor=vendor,
            reference="EV-INV-1",
            mode="resend",
            subject="[pharmacy-agent test] resend request",
            body="This should never be sent -- dedup should short-circuit first.",
            client=client,
        )
        assert repeat_result == {
            "sent": False,
            "reason": "already_sent",
            "mode": "resend",
            "log_id": log_doc_ids[0],
        }
    finally:
        for log_doc_id in log_doc_ids:
            client.collection("email_log").document(log_doc_id).delete()
        vendor_directory_collection(client).document(vendor_doc_id).delete()


def test_missing_pharmacist_config_does_not_send():
    client = get_client()
    vendor = "EV No Pharmacist Vendor"
    vendor_doc_id = vendor_directory_doc_id(vendor)
    log_doc_id = email_log_doc_id(vendor, "EV-INV-3", "resend")
    config_doc = client.collection("config").document("pharmacist")
    previous_pharmacist = config_doc.get()
    previous_data = previous_pharmacist.to_dict() if previous_pharmacist.exists else None
    try:
        set_vendor_email(vendor, TEST_ADDRESS, client=client)
        config_doc.delete()

        result = email_vendor(
            vendor=vendor,
            reference="EV-INV-3",
            mode="resend",
            subject="s",
            body="b",
            client=client,
        )
        assert result == {
            "sent": False,
            "reason": "pharmacist_not_configured",
            "mode": "resend",
            "log_id": log_doc_id,
        }
    finally:
        vendor_directory_collection(client).document(vendor_doc_id).delete()
        client.collection("email_log").document(log_doc_id).delete()
        if previous_data is not None:
            config_doc.set(previous_data)
