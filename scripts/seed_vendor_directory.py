"""Seed script (PRD S7.10 / T38): populate the trusted vendor directory and
pharmacist contact config that email_vendor (and, later, notify_pharmacist/
ask_pharmacist) resolve recipients from -- never from parsed document
content.

Real vendor email addresses aren't known yet (PRD S14 open items), so every
entry here points at [redacted-personal-email] -- the same account authorized for
Gmail send/read in T08/T09 -- as a placeholder that keeps the send path
real and testable without mailing an actual vendor. Swap in real addresses
once available; nothing else in email_vendor needs to change.

Safe to re-run: set_vendor_email/set_pharmacist_email doc IDs are
deterministic, so re-seeding overwrites rather than duplicates.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pharmacy_agent.firestore_client import get_client  # noqa: E402
from pharmacy_agent.vendor_directory import set_pharmacist_email, set_vendor_email  # noqa: E402

PLACEHOLDER_EMAIL = "[redacted-personal-email]"

VENDORS = ["Northfield Associates", "Harbor Medicare Solutions", "SUMMIT PHARMA"]


def main() -> None:
    client = get_client()
    for vendor in VENDORS:
        set_vendor_email(vendor, PLACEHOLDER_EMAIL, client=client)
        print(f"vendor_directory: {vendor} -> {PLACEHOLDER_EMAIL}")

    set_pharmacist_email(PLACEHOLDER_EMAIL, client=client)
    print(f"config/pharmacist: email -> {PLACEHOLDER_EMAIL}")


if __name__ == "__main__":
    main()
