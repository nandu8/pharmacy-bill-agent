from pharmacy_agent.drive_client import get_root_folder_id, get_service
from pharmacy_agent.firestore_client import get_client
from pharmacy_agent.stage_file import STAGED_FILES_COLLECTION, stage_file, staged_file_doc_id

TEST_ROOT_FOLDER_NAME = "Pharmacy Bill Agent TEST T49"


def _trash(service, folder_id):
    service.files().update(fileId=folder_id, body={"trashed": True}).execute()


def test_stage_file_uploads_bytes_unmodified_and_dedupes_on_repeat():
    firestore_client = get_client()
    drive_service = get_service()
    vendor = "SF Test Vendor"
    reference = "SF-INV-1"
    original_bytes = b"\x00\x01byte-for-byte test payload\xffPRD S7.9"
    doc_id = staged_file_doc_id(vendor, reference)
    root_id = get_root_folder_id(service=drive_service, root_folder_name=TEST_ROOT_FOLDER_NAME)

    try:
        first_result = stage_file(
            vendor=vendor,
            reference=reference,
            filename="sf_test_invoice.bin",
            file_bytes=original_bytes,
            client=firestore_client,
            drive_service=drive_service,
            root_folder_name=TEST_ROOT_FOLDER_NAME,
        )
        assert first_result["staged"] is True
        drive_file_id = first_result["drive_file_id"]

        downloaded = drive_service.files().get_media(fileId=drive_file_id).execute()
        assert downloaded == original_bytes

        repeat_result = stage_file(
            vendor=vendor,
            reference=reference,
            filename="sf_test_invoice.bin",
            file_bytes=b"this should never be uploaded",
            client=firestore_client,
            drive_service=drive_service,
            root_folder_name=TEST_ROOT_FOLDER_NAME,
        )
        assert repeat_result == {
            "staged": False,
            "reason": "already_staged",
            "drive_file_id": drive_file_id,
            "log_id": doc_id,
        }

        still_downloaded = drive_service.files().get_media(fileId=drive_file_id).execute()
        assert still_downloaded == original_bytes
    finally:
        firestore_client.collection(STAGED_FILES_COLLECTION).document(doc_id).delete()
        _trash(drive_service, root_id)
