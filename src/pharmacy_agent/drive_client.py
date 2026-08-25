"""Google Drive per-vendor folder structure (PRD S7.9 / T48).

Verified files get staged (T49, not built here) into a per-vendor Drive
folder so Drive Desktop sync puts them on the pharmacist's machine with no
dashboard or download step. This module only resolves/creates that folder
tree -- get_root_folder_id and get_vendor_folder_id are both
find-or-create, so re-running ingestion never creates duplicate folders
for the same vendor.
"""
from __future__ import annotations

from googleapiclient.discovery import Resource, build

from .google_oauth import load_credentials

SCOPES = ["https://www.googleapis.com/auth/drive"]

DEFAULT_ROOT_FOLDER_NAME = "Pharmacy Bill Agent"
_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


def get_service() -> Resource:
    return build("drive", "v3", credentials=load_credentials(SCOPES))


def _escape_query_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _find_folder(service: Resource, name: str, parent_id: str | None) -> str | None:
    query = f"name = '{_escape_query_value(name)}' and mimeType = '{_FOLDER_MIME_TYPE}' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, spaces="drive", fields="files(id)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def _create_folder(service: Resource, name: str, parent_id: str | None) -> str:
    metadata = {"name": name, "mimeType": _FOLDER_MIME_TYPE}
    if parent_id:
        metadata["parents"] = [parent_id]
    folder = service.files().create(body=metadata, fields="id").execute()
    return folder["id"]


def _get_or_create_folder(service: Resource, name: str, parent_id: str | None) -> str:
    existing = _find_folder(service, name, parent_id)
    if existing:
        return existing
    return _create_folder(service, name, parent_id)


def get_root_folder_id(service: Resource | None = None, root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME) -> str:
    service = service or get_service()
    return _get_or_create_folder(service, root_folder_name, parent_id=None)


def get_vendor_folder_id(
    vendor: str,
    service: Resource | None = None,
    root_folder_name: str = DEFAULT_ROOT_FOLDER_NAME,
) -> str:
    service = service or get_service()
    root_id = get_root_folder_id(service, root_folder_name)
    return _get_or_create_folder(service, vendor, parent_id=root_id)
