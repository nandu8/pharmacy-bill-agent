from pharmacy_agent.firestore_client import get_client
from pharmacy_agent.vendor_directory import (
    config_collection,
    get_pharmacist_email,
    get_vendor_email,
    set_pharmacist_email,
    set_vendor_email,
    vendor_directory_collection,
    vendor_directory_doc_id,
)


def test_unknown_vendor_returns_none():
    client = get_client()
    result = get_vendor_email("VD No Such Vendor", client=client)
    assert result is None


def test_set_then_get_vendor_email_round_trips():
    client = get_client()
    doc_id = vendor_directory_doc_id("VD Round Trip Vendor")
    try:
        set_vendor_email("VD Round Trip Vendor", "vendor@example.com", client=client)
        assert get_vendor_email("VD Round Trip Vendor", client=client) == "vendor@example.com"
    finally:
        vendor_directory_collection(client).document(doc_id).delete()


def test_vendor_lookup_is_case_and_whitespace_insensitive():
    client = get_client()
    doc_id = vendor_directory_doc_id("  VD Mixed Case Vendor  ")
    try:
        set_vendor_email("  VD Mixed Case Vendor  ", "mixed@example.com", client=client)
        assert get_vendor_email("vd mixed case vendor", client=client) == "mixed@example.com"
    finally:
        vendor_directory_collection(client).document(doc_id).delete()


def test_pharmacist_email_round_trips():
    client = get_client()
    previous = get_pharmacist_email(client=client)
    try:
        set_pharmacist_email("pharmacist@example.com", client=client)
        assert get_pharmacist_email(client=client) == "pharmacist@example.com"
    finally:
        if previous is None:
            config_collection(client).document("pharmacist").delete()
        else:
            set_pharmacist_email(previous, client=client)
