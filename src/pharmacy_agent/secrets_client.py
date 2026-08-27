"""Shared Secret Manager access.

Factored out of google_oauth.py's private _access_secret (T08/T48) when T41
needed the identical pattern for the Twilio credentials -- same client
construction, same `projects/{project}/secrets/{id}/versions/latest` path.
"""
from __future__ import annotations

import os

from google.cloud import secretmanager

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "pharmacy-bill-agent")


def access_secret(secret_id: str, project: str = _PROJECT) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project}/secrets/{secret_id}/versions/latest"
    raw = client.access_secret_version(name=name).payload.data.decode("utf-8")
    # PowerShell's `'value' | gcloud secrets versions add ... --data-file=-`
    # pipes a trailing CRLF into stdin on Windows -- stripping here means a
    # secret stored that way still works, instead of silently breaking
    # whatever consumes it (e.g. an HTTP header value, as happened with the
    # Twilio and Meta credentials during T41).
    return raw.strip()
