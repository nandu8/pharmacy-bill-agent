"""Deployment-time config flags (PRD S7.5 / T40).

DISPUTE_REQUIRES_APPROVAL gates whether a dispute that clears T39's four
conditions sends immediately via email_vendor (autonomous) or is first
surfaced to the pharmacist as a one-tap WhatsApp confirmation
(ask_pharmacist, T41 -- not wired yet). Default false: the hackathon demo
runs unsupervised so the autonomous-resolution beat is visible on camera.
A real rollout would start it true for the first few weeks before flipping
it off (PRD S7.5) -- read at call time (not cached at import) so a Cloud
Run redeploy with the env var changed takes effect without a code change.
"""
from __future__ import annotations

import os

DISPUTE_REQUIRES_APPROVAL_ENV_VAR = "DISPUTE_REQUIRES_APPROVAL"
_TRUTHY = {"1", "true", "yes", "on"}


def dispute_requires_approval() -> bool:
    raw = os.environ.get(DISPUTE_REQUIRES_APPROVAL_ENV_VAR)
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY
