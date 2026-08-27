"""Meta WhatsApp Cloud API send client (PRD S9 / T41).

Replaces an earlier Twilio-based implementation: Twilio's Sandbox for
WhatsApp turned out to require, in order, a paid account, a Trust Hub KYC
review, and finally a full WhatsApp Sender registration through a Meta
Business Manager anyway -- at which point going straight to Meta's own
Cloud API was strictly less work. Meta's free Test WhatsApp Business
Account (no business verification, unlimited free messages to up to 5
allow-listed test recipients) is what this project actually needs.

The access token is held in Secret Manager (`meta-whatsapp-access-token`),
never in source or env vars. The phone number id is not sensitive -- it's
an opaque routing id, not a credential -- so it's plain deploy-time config
via an env var, same pattern as config.py's DISPUTE_REQUIRES_APPROVAL.

Session window: like Twilio's sandbox, Meta only allows freeform text
within 24 hours of the recipient's last message. Reopening it needs the
free, pre-approved `hello_world` template -- not implemented here (a manual
Console send was enough to unblock T41's own testing); revisit if the
deployed agent needs to reopen the window unattended.
"""
from __future__ import annotations

import os

import requests

from .secrets_client import access_secret

META_WHATSAPP_PHONE_NUMBER_ID_ENV_VAR = "META_WHATSAPP_PHONE_NUMBER_ID"
_GRAPH_API_VERSION = "v21.0"


def _phone_number_id() -> str:
    phone_number_id = os.environ.get(META_WHATSAPP_PHONE_NUMBER_ID_ENV_VAR)
    if not phone_number_id:
        raise RuntimeError(f"{META_WHATSAPP_PHONE_NUMBER_ID_ENV_VAR} is not set")
    return phone_number_id


def _access_token() -> str:
    return access_secret("meta-whatsapp-access-token")


def send_whatsapp(to: str, body: str, client: requests.Session | None = None) -> dict:
    client = client or requests
    phone_number_id = _phone_number_id()
    url = f"https://graph.facebook.com/{_GRAPH_API_VERSION}/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"body": body},
    }
    response = client.post(url, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    message_id = data["messages"][0]["id"]
    return {"sid": message_id, "status": "sent"}
