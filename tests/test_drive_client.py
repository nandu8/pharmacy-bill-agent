from pharmacy_agent.drive_client import get_root_folder_id, get_service, get_vendor_folder_id

TEST_ROOT_FOLDER_NAME = "Pharmacy Bill Agent TEST T48"


def _trash(service, folder_id):
    service.files().update(fileId=folder_id, body={"trashed": True}).execute()


def test_root_folder_is_created_then_reused():
    service = get_service()
    first_id = get_root_folder_id(service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
    try:
        second_id = get_root_folder_id(service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
        assert first_id == second_id

        folder = service.files().get(fileId=first_id, fields="name,mimeType").execute()
        assert folder["name"] == TEST_ROOT_FOLDER_NAME
        assert folder["mimeType"] == "application/vnd.google-apps.folder"
    finally:
        _trash(service, first_id)


def test_vendor_folder_is_created_under_root_and_reused():
    service = get_service()
    root_id = get_root_folder_id(service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
    try:
        first_id = get_vendor_folder_id("DC Test Vendor", service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
        second_id = get_vendor_folder_id("DC Test Vendor", service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
        assert first_id == second_id

        folder = service.files().get(fileId=first_id, fields="name,parents").execute()
        assert folder["name"] == "DC Test Vendor"
        assert root_id in folder["parents"]
    finally:
        _trash(service, root_id)


def test_different_vendors_get_different_folders():
    service = get_service()
    root_id = get_root_folder_id(service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
    try:
        vendor_a_id = get_vendor_folder_id("DC Vendor A", service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
        vendor_b_id = get_vendor_folder_id("DC Vendor B", service=service, root_folder_name=TEST_ROOT_FOLDER_NAME)
        assert vendor_a_id != vendor_b_id
    finally:
        _trash(service, root_id)
