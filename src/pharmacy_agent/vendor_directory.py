"""Trusted vendor/pharmacist directory (PRD S7.10 / T38).

Recipient addresses for `email_vendor` and `notify_pharmacist`/`ask_pharmacist`
must always come from here, never from a parsed document or email body -- a
malformed or adversarial vendor attachment can then never redirect where the
agent sends mail. Doc IDs are a sha256 of the normalized vendor name, same
deterministic-key pattern as purchase_ledger/bills, so re-seeding a vendor's
entry overwrites rather than duplicates.
"""
from __future__ import annotations

import hashlib

from google.cloud import firestore

from .firestore_client import get_client

VENDOR_DIRECTORY_COLLECTION = "vendor_directory"
CONFIG_COLLECTION = "config"
PHARMACIST_CONFIG_DOC = "pharmacist"


def vendor_directory_doc_id(vendor: str) -> str:
    normalized = vendor.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def vendor_directory_collection(client: firestore.Client | None = None) -> firestore.CollectionReference:
    return (client or get_client()).collection(VENDOR_DIRECTORY_COLLECTION)


def config_collection(client: firestore.Client | None = None) -> firestore.CollectionReference:
    return (client or get_client()).collection(CONFIG_COLLECTION)


def set_vendor_email(vendor: str, email: str, client: firestore.Client | None = None) -> str:
    client = client or get_client()
    doc_id = vendor_directory_doc_id(vendor)
    vendor_directory_collection(client).document(doc_id).set(
        {"vendor": vendor, "email": email}, merge=True
    )
    return doc_id


def get_vendor_email(vendor: str, client: firestore.Client | None = None) -> str | None:
    client = client or get_client()
    doc = vendor_directory_collection(client).document(vendor_directory_doc_id(vendor)).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("email")


def set_pharmacist_email(email: str, client: firestore.Client | None = None) -> None:
    client = client or get_client()
    config_collection(client).document(PHARMACIST_CONFIG_DOC).set({"email": email}, merge=True)


def get_pharmacist_email(client: firestore.Client | None = None) -> str | None:
    client = client or get_client()
    doc = config_collection(client).document(PHARMACIST_CONFIG_DOC).get()
    if not doc.exists:
        return None
    return doc.to_dict().get("email")
