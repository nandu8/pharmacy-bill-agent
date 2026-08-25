"""Gmail watch registration (PRD S9/S7.9-adjacent / T51): event-driven
ingestion via Gmail `watch` -> Pub/Sub push, replacing polling.

`start_watch` registers (or renews -- Gmail watch expires after ~7 days,
same trap as the OAuth token in T10) a push subscription: every mailbox
change publishes a notification to the `gmail-notifications` Pub/Sub topic
(created for T51, with `gmail-api-push@system.gserviceaccount.com` granted
`pubsub.publisher` on it -- a Google-documented requirement, not this
project's service account). Wiring an actual Cloud Run endpoint to receive
those pushes is T52 -- this module only registers the subscription and
proves Gmail will publish to it; nothing consumes the topic yet.
"""
from __future__ import annotations

import os

from googleapiclient.discovery import Resource, build

from .google_oauth import load_credentials

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "pharmacy-bill-agent")
TOPIC_NAME = f"projects/{_PROJECT}/topics/gmail-notifications"


def get_service() -> Resource:
    return build("gmail", "v1", credentials=load_credentials(SCOPES))


def start_watch(service: Resource | None = None, label_ids: list[str] | None = None) -> dict:
    service = service or get_service()
    body = {"topicName": TOPIC_NAME, "labelIds": label_ids or ["INBOX"]}
    return service.users().watch(userId="me", body=body).execute()


def stop_watch(service: Resource | None = None) -> None:
    service = service or get_service()
    service.users().stop(userId="me").execute()
