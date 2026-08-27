import os
import time

import pytest

from pharmacy_agent.firestore_client import get_client
from pharmacy_agent.gmail_client import get_service as get_send_service
from pharmacy_agent.gmail_client import send_email_with_attachment
from pharmacy_agent.gmail_history import (
    GMAIL_WATCH_CONFIG_DOC,
    fetch_new_attachments,
    get_last_history_id,
    get_service,
    set_last_history_id,
)

# The account's own address (same one authorized via OAuth, T08/T09) -- kept
# out of source since it's the developer's personal email; set it locally
# to run the live-send test below.
TEST_ADDRESS = os.environ.get("TEST_GMAIL_ADDRESS")


def test_set_then_get_last_history_id_round_trips():
    client = get_client()
    config_doc = client.collection("config").document(GMAIL_WATCH_CONFIG_DOC)
    previous = config_doc.get()
    previous_value = previous.to_dict().get("last_history_id") if previous.exists else None
    try:
        set_last_history_id("123456", client=client)
        assert get_last_history_id(client=client) == "123456"
    finally:
        if previous_value is None:
            config_doc.delete()
        else:
            set_last_history_id(previous_value, client=client)


@pytest.mark.skipif(not TEST_ADDRESS, reason="TEST_GMAIL_ADDRESS not set")
def test_fetch_new_attachments_finds_a_real_sent_attachment():
    service = get_service()
    profile = service.users().getProfile(userId="me").execute()
    baseline_history_id = profile["historyId"]

    attachment_bytes = b"col1,col2\r\ngmail_history test,value\r\n"
    filename = "gh_test_attachment.csv"
    send_email_with_attachment(
        to=TEST_ADDRESS,
        cc=TEST_ADDRESS,
        subject="[pharmacy-agent test] gmail_history fetch_new_attachments",
        body="Live test from tests/test_gmail_history.py -- safe to ignore.",
        filename=filename,
        file_bytes=attachment_bytes,
        service=get_send_service(),
    )

    found = []
    for _ in range(15):
        found = fetch_new_attachments(baseline_history_id, service=service)
        if any(a["filename"] == filename for a in found):
            break
        time.sleep(2)

    matches = [a for a in found if a["filename"] == filename]
    assert len(matches) == 1
    assert matches[0]["bytes"] == attachment_bytes
    assert TEST_ADDRESS in matches[0]["sender"]
