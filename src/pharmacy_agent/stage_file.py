"""stage_file (PRD S7.2/S7.9/S7.10 / T49): copy a verified vendor file,
byte-for-byte, into its per-vendor Drive folder (T48).

The original is never modified -- the pharmacist's billing software
expects the vendor's native container, so exact bytes matter (S7.9).
Idempotent: keyed on vendor+reference (invoice_no), same deterministic-hash
pattern as purchase_ledger/bills/email_log, so a retried or resumed run
(S7.6) never re-uploads (S7.10: "stage_file succeeds and a later step
fails... re-running does not re-copy the file").
"""
from __future__ import annotations

import hashlib

from google.cloud import firestore
from googleapiclient.discovery import Resource
from googleapiclient.http import MediaInMemoryUpload

from .drive_client import DEFAULT_ROOT_FOLDER_NAME, get_service, get_vendor_folder_id
from .firestore_client import get_client

STAGED_FILES_COLLECTION = "staged_files"


def staged_file_doc_id(vendor: str, reference: str) -> str:
    composite = f"{vendor}::{reference}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def stage_file(
    vendor: str,
    reference: str,
    filename: str,
    file_bytes: bytes,
    client: firestore.Client | None = None,
    drive_service: Resource | None = None,
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
) -> dict:
    client = client or get_client()
    doc_id = staged_file_doc_id(vendor, reference)
    collection = client.collection(STAGED_FILES_COLLECTION)

    existing = collection.document(doc_id).get()
    if existing.exists:
        data = existing.to_dict()
        return {
            "staged": False,
            "reason": "already_staged",
            "drive_file_id": data.get("drive_file_id"),
            "log_id": doc_id,
        }

    drive_service = drive_service or get_service()
    folder_id = get_vendor_folder_id(vendor, service=drive_service, root_folder_name=root_folder_name)
    media = MediaInMemoryUpload(file_bytes, mimetype="application/octet-stream", resumable=False)
    file_metadata = {"name": filename, "parents": [folder_id]}
    result = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    drive_file_id = result["id"]

    collection.document(doc_id).set(
        {
            "vendor": vendor,
            "reference": reference,
            "filename": filename,
            "drive_file_id": drive_file_id,
        }
    )

    return {"staged": True, "drive_file_id": drive_file_id, "log_id": doc_id}
