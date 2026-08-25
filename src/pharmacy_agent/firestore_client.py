"""Firestore client and collection accessors (PRD S10 / T21).

Firestore is schemaless -- a collection exists only once it holds a
document, so there is no separate "create collection" API call. What this
module pins down instead is the one place every future writer (record_purchase,
the agent loop's pause/resume state, etc.) gets the collection names and a
client from, rather than hardcoding collection-name strings at each call
site.
"""
from __future__ import annotations

import os

from google.cloud import firestore

BILLS_COLLECTION = "bills"
PURCHASE_LEDGER_COLLECTION = "purchase_ledger"
AGENT_RUNS_COLLECTION = "agent_runs"

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "pharmacy-bill-agent")
_DATABASE = os.environ.get("FIRESTORE_DATABASE", "(default)")


def get_client() -> firestore.Client:
    # Passing database="(default)" explicitly triggers a client-library
    # code path that percent-encodes the parens and gets rejected server-
    # side ("Invalid database id %28default%29") -- reproduced only under
    # Cloud Run's compute-engine-metadata credentials, not local user ADC
    # (confirmed during T52's deploy). Omitting the kwarg lets the SDK's
    # own default take over, which works under both credential types.
    if _DATABASE == "(default)":
        return firestore.Client(project=_PROJECT)
    return firestore.Client(project=_PROJECT, database=_DATABASE)


def bills_collection(client: firestore.Client | None = None) -> firestore.CollectionReference:
    return (client or get_client()).collection(BILLS_COLLECTION)


def purchase_ledger_collection(client: firestore.Client | None = None) -> firestore.CollectionReference:
    return (client or get_client()).collection(PURCHASE_LEDGER_COLLECTION)


def agent_runs_collection(client: firestore.Client | None = None) -> firestore.CollectionReference:
    return (client or get_client()).collection(AGENT_RUNS_COLLECTION)
