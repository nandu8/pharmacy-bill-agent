"""Seed script (PRD S7.10 / T38, T41): populate the trusted vendor directory
and pharmacist contact config that email_vendor and notify_pharmacist/
ask_pharmacist resolve recipients from -- never from parsed document
content.

Real vendor email addresses aren't known yet (PRD S14 open items), so every
entry here points at the same account authorized for Gmail send/read in
T08/T09 -- as a placeholder that keeps the send path real and testable
without mailing an actual vendor. Swap in real addresses once available;
nothing else in email_vendor needs to change. That account is the
developer's own personal email, so it's never hardcoded in this (public)
repo -- pass it via the PLACEHOLDER_EMAIL env var when running this script.

The pharmacist's WhatsApp number is real (T05/T41) -- it's the number
registered as a Meta WhatsApp Cloud API test recipient, so a placeholder
here wouldn't actually be reachable. It is a real person's phone number, so
it is never hardcoded in this (public) repo -- pass it via the
PHARMACIST_WHATSAPP_NUMBER env var when running this script. It's already
seeded in Firestore from an earlier run; this script only needs re-running
if that config doc is ever cleared.

Safe to re-run: set_vendor_email/set_pharmacist_email/set_pharmacist_whatsapp
doc IDs are deterministic, so re-seeding overwrites rather than duplicates.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pharmacy_agent.firestore_client import get_client  # noqa: E402
from pharmacy_agent.vendor_directory import (  # noqa: E402
    set_pharmacist_email,
    set_pharmacist_whatsapp,
    set_vendor_email,
)

VENDORS = ["Northfield Associates", "Harbor Medicare Solutions", "SUMMIT PHARMA"]


def main() -> None:
    client = get_client()
    placeholder_email = os.environ.get("PLACEHOLDER_EMAIL")
    if not placeholder_email:
        raise SystemExit("PLACEHOLDER_EMAIL is not set")

    for vendor in VENDORS:
        set_vendor_email(vendor, placeholder_email, client=client)
        print(f"vendor_directory: {vendor} -> (set from PLACEHOLDER_EMAIL)")

    set_pharmacist_email(placeholder_email, client=client)
    print("config/pharmacist: email -> (set from PLACEHOLDER_EMAIL)")

    pharmacist_whatsapp = os.environ.get("PHARMACIST_WHATSAPP_NUMBER")
    if pharmacist_whatsapp:
        set_pharmacist_whatsapp(pharmacist_whatsapp, client=client)
        print("config/pharmacist: whatsapp -> (set from PHARMACIST_WHATSAPP_NUMBER)")
    else:
        print("config/pharmacist: whatsapp -> skipped (PHARMACIST_WHATSAPP_NUMBER not set)")


if __name__ == "__main__":
    main()
