from pharmacy_agent.firestore_client import get_client, pharmacist_resolutions_collection
from pharmacy_agent.pharmacist_resolutions import (
    PharmacistDecision,
    lookup_pharmacist_resolution,
    record_pharmacist_resolution,
)


def _cleanup(doc_ids, client):
    for doc_id in doc_ids:
        pharmacist_resolutions_collection(client).document(doc_id).delete()


def test_lookup_pharmacist_resolution_returns_none_when_no_resolution():
    client = get_client()
    result = lookup_pharmacist_resolution("No Such Vendor", "NOTHING HERE", client=client)
    assert result is None


def test_record_and_lookup_pharmacist_resolution_approved():
    client = get_client()
    doc_ids = []
    try:
        doc_id = record_pharmacist_resolution(
            "PR Vendor",
            "AMLODIPINE 5MG",
            rate=27.0,
            decision=PharmacistDecision.APPROVED,
            note="pharmacist confirmed the rise is legitimate",
            invoice_no="PR-INV-001",
            client=client,
        )
        doc_ids.append(doc_id)

        result = lookup_pharmacist_resolution("PR Vendor", "AMLODIPINE 5MG", client=client)
        assert result is not None
        assert result.vendor == "PR Vendor"
        assert result.decision == PharmacistDecision.APPROVED
        assert result.rate == 27.0
        assert result.invoice_no == "PR-INV-001"
    finally:
        _cleanup(doc_ids, client)


def test_record_pharmacist_resolution_is_keyed_on_vendor_and_item_not_invoice():
    # PRD S7.7: the decision must be queryable per vendor+item and apply to
    # *future* bills, not just the one invoice it was raised on -- so a
    # second resolution for the same vendor+item overwrites the first
    # rather than accumulating a growing history the lookup has to sort.
    client = get_client()
    doc_ids = []
    try:
        doc_ids.append(
            record_pharmacist_resolution(
                "PR Overwrite Vendor",
                "PARACETAMOL 500MG",
                rate=10.0,
                decision=PharmacistDecision.REJECTED,
                invoice_no="PR-INV-002",
                client=client,
            )
        )
        second_id = record_pharmacist_resolution(
            "PR Overwrite Vendor",
            "PARACETAMOL 500MG",
            rate=12.0,
            decision=PharmacistDecision.APPROVED,
            invoice_no="PR-INV-003",
            client=client,
        )
        assert second_id == doc_ids[0]

        result = lookup_pharmacist_resolution("PR Overwrite Vendor", "PARACETAMOL 500MG", client=client)
        assert result.decision == PharmacistDecision.APPROVED
        assert result.rate == 12.0
        assert result.invoice_no == "PR-INV-003"
    finally:
        _cleanup(doc_ids, client)


def test_lookup_pharmacist_resolution_matches_item_key_not_exact_text():
    client = get_client()
    doc_ids = []
    try:
        doc_ids.append(
            record_pharmacist_resolution(
                "PR Fuzzy Vendor",
                "Amlodipine 5mg",
                rate=27.0,
                decision=PharmacistDecision.APPROVED,
                invoice_no="PR-INV-004",
                client=client,
            )
        )
        result = lookup_pharmacist_resolution("PR Fuzzy Vendor", "AMLODIPINE   5MG", client=client)
        assert result is not None
        assert result.decision == PharmacistDecision.APPROVED
    finally:
        _cleanup(doc_ids, client)
