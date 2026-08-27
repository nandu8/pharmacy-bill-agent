import os

import pytest

from pharmacy_agent.gmail_client import get_service, load_credentials, send_email, send_email_with_attachment

# The account's own address (same one authorized via OAuth, T08/T09) -- kept
# out of source since it's the developer's personal email; set it locally
# to run these live-send tests.
TEST_ADDRESS = os.environ.get("TEST_GMAIL_ADDRESS")
pytestmark = pytest.mark.skipif(not TEST_ADDRESS, reason="TEST_GMAIL_ADDRESS not set")


def test_load_credentials_refreshes_a_usable_access_token():
    creds = load_credentials()
    assert creds.valid
    assert creds.token


def test_send_email_delivers_via_real_gmail_api():
    service = get_service()
    result = send_email(
        to=TEST_ADDRESS,
        cc=TEST_ADDRESS,
        subject="[pharmacy-agent test] gmail_client send_email",
        body="Live test from tests/test_gmail_client.py -- safe to ignore.",
        service=service,
    )
    assert "id" in result


def test_send_email_with_attachment_delivers_via_real_gmail_api():
    service = get_service()
    result = send_email_with_attachment(
        to=TEST_ADDRESS,
        cc=TEST_ADDRESS,
        subject="[pharmacy-agent test] gmail_client send_email_with_attachment",
        body="Live test from tests/test_gmail_client.py -- safe to ignore.",
        filename="test_attachment.csv",
        file_bytes=b"col1,col2\r\nval1,val2\r\n",
        service=service,
    )
    assert "id" in result
