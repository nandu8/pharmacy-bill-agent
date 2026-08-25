"""Operational script (PRD S9 / T51/T52): register the Gmail push watch
and seed the Firestore history-id baseline in one step.

Run this once after deploying (and again whenever the watch needs
renewing -- it expires after ~7 days, PRD S9). Without seeding the
baseline here, the very first real push notification would just establish
it silently (ingest.py's defensive fallback) and skip processing whatever
triggered that notification -- seeding it now means the first real email
after this script runs is actually processed, not swallowed as a
baseline-only event.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from pharmacy_agent.gmail_history import set_last_history_id  # noqa: E402
from pharmacy_agent.gmail_watch import start_watch  # noqa: E402


def main() -> None:
    result = start_watch()
    history_id = result["historyId"]
    set_last_history_id(history_id)
    print(f"Gmail watch registered. historyId={history_id} expiration={result.get('expiration')}")
    print("Baseline seeded in Firestore config/gmail_watch -- ready for real pushes.")


if __name__ == "__main__":
    main()
