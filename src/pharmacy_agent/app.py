"""Cloud Run push endpoint (PRD S7.1/S9 / T52): receives Gmail Pub/Sub push
notifications and starts an agent run for each new attachment.

The endpoint is internet-reachable (Pub/Sub push requires that), so every
request must be verified as genuinely Pub/Sub-originated -- otherwise
anyone could POST a forged notification and trigger the agent. Verification
checks the OIDC bearer token Pub/Sub attaches against the service's own URL
as audience (`PUBSUB_PUSH_AUDIENCE`, set at deploy time) -- the standard
Cloud Run push-auth pattern, not a bespoke scheme. Unset locally, so local
testing isn't locked out; every real deploy sets it.
"""
from __future__ import annotations

import asyncio
import os

from fastapi import FastAPI, HTTPException, Request
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from .ingest import handle_pubsub_push

app = FastAPI()


def _verify_pubsub_push(request: Request) -> None:
    audience = os.environ.get("PUBSUB_PUSH_AUDIENCE")
    if not audience:
        return
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth_header.removeprefix("Bearer ")
    try:
        id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"invalid push token: {exc}") from exc


@app.post("/pubsub/push")
async def pubsub_push(request: Request) -> dict:
    _verify_pubsub_push(request)
    envelope = await request.json()
    if "message" not in envelope:
        raise HTTPException(status_code=400, detail="malformed Pub/Sub envelope")
    # handle_pubsub_push (via run_bill) does its own asyncio.run() internally
    # -- offload to a thread so it doesn't collide with this request's
    # already-running event loop.
    results = await asyncio.to_thread(handle_pubsub_push, envelope)
    return {"processed": len(results)}


@app.get("/health")
def health() -> dict:
    # Not "/healthz" -- that path is intercepted by Google Front End on
    # *.run.app default domains before it ever reaches the container
    # (confirmed empirically during T52 deploy: every other path reached
    # FastAPI fine, /healthz alone came back as a GFE-branded 404).
    return {"status": "ok"}
