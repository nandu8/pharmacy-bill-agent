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
import json
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from .ingest import handle_pubsub_push
from .status_page import list_bills, render_status_page
from .telemetry import project_id as gcp_project_id
from .telemetry import setup_cloud_logging, setup_tracing
from .whatsapp_webhook import (
    extract_messages,
    record_inbound_message,
    verify_signature,
    verify_subscription_token,
    webhook_app_secret,
    webhook_verify_token,
)

setup_tracing()
setup_cloud_logging()

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


@app.get("/whatsapp/webhook")
def whatsapp_webhook_verify(request: Request) -> PlainTextResponse:
    # T42/PRD S7.6: Meta's one-time subscribe handshake -- echo the challenge
    # back only if the token matches the one configured in the Meta App
    # Dashboard's webhook setup.
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge", "")
    if not verify_subscription_token(mode, token, webhook_verify_token()):
        raise HTTPException(status_code=403, detail="webhook verification failed")
    return PlainTextResponse(challenge)


@app.post("/whatsapp/webhook")
async def whatsapp_webhook_receive(request: Request) -> dict:
    # Every inbound delivery is signed with the app secret (T42) -- same
    # "verify the caller" guardrail as /pubsub/push, Meta's scheme instead
    # of Google's OIDC one.
    raw_body = await request.body()
    if not verify_signature(webhook_app_secret(), raw_body, request.headers.get("x-hub-signature-256")):
        raise HTTPException(status_code=401, detail="invalid webhook signature")
    payload = json.loads(raw_body)
    messages = extract_messages(payload)
    recorded = 0
    for message in messages:
        if await asyncio.to_thread(record_inbound_message, message):
            recorded += 1
    return {"received": len(messages), "recorded": recorded}


@app.get("/health")
def health() -> dict:
    # Not "/healthz" -- that path is intercepted by Google Front End on
    # *.run.app default domains before it ever reaches the container
    # (confirmed empirically during T52 deploy: every other path reached
    # FastAPI fine, /healthz alone came back as a GFE-branded 404).
    return {"status": "ok"}


@app.get("/status", response_class=HTMLResponse)
def status_page() -> str:
    # PRD S7.11/T56: judge-facing, read-only, deployed to allow
    # unauthenticated reads (T57) -- unlike /pubsub/push, this route needs
    # no verification, since it exposes nothing but bill status/metadata
    # already meant to be independently checkable without GCP IAM.
    bills = list_bills()
    return render_status_page(bills, project_id=gcp_project_id())
