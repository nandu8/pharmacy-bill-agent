"""Observability (PRD S8 / T54): exports ADK's OpenTelemetry spans to Cloud
Trace and structures stdout logging for Cloud Logging trace correlation.

ADK creates spans via the global OTel TracerProvider on every LLM call and
tool call (google/adk/telemetry/tracing.py) but ships no exporter of its
own -- without an explicit provider, the OTel API defaults to a no-op
provider and nothing is ever recorded. `setup_tracing` registers a provider
that exports via the standard OTLP gRPC exporter pointed at Cloud Trace's
native OTLP ingestion endpoint (`telemetry.googleapis.com`) -- the
GoogleCloudPlatform/opentelemetry-operations-python project's own migration
guide names this as the current path, since the dedicated
`opentelemetry-exporter-gcp-trace` package (CloudTraceSpanExporter) writes
through the legacy v2 REST API and is deprecated; its read counterpart (the
v1 `TraceServiceClient.get_trace` API used to verify this module during
development) turned out to depend on a legacy "trace bucket" resource this
project doesn't have provisioned, returning 404 even after a genuinely
successful export -- see `tests/test_telemetry.py`'s live check, which
verifies the OTLP exporter's own SUCCESS/FAILURE return value instead of
trying to read the trace back. `agent/loop.py` wraps each bill run in one
outer span so a single trace id covers every turn, which is what
`bills.trace_id` (S10 schema) records for the status page (S7.11) to link
to.
"""
from __future__ import annotations

import json
import logging
import os

import google.auth
import google.auth.transport.grpc
import google.auth.transport.requests
import grpc
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_SERVICE_NAME = "pharmacy-bill-agent"
_PROJECT_ENV_VAR = "GOOGLE_CLOUD_PROJECT"
_DEFAULT_PROJECT = "pharmacy-bill-agent"
_OTLP_ENDPOINT = "telemetry.googleapis.com"

_tracer_provider: TracerProvider | None = None
_logging_configured = False


def project_id() -> str:
    return os.environ.get(_PROJECT_ENV_VAR, _DEFAULT_PROJECT)


def build_otlp_span_exporter() -> OTLPSpanExporter:
    """Builds an OTLPSpanExporter authenticated against Cloud Trace's OTLP
    endpoint via the caller's ambient Google credentials (Cloud Run's
    attached service account in production, user ADC locally) -- Cloud
    Trace's OTLP ingestion requires a bearer token on every gRPC call, which
    a plain OTLP exporter has no way to attach on its own."""
    credentials, _ = google.auth.default()
    request = google.auth.transport.requests.Request()
    auth_metadata_plugin = google.auth.transport.grpc.AuthMetadataPlugin(
        credentials=credentials, request=request
    )
    channel_creds = grpc.composite_channel_credentials(
        grpc.ssl_channel_credentials(),
        grpc.metadata_call_credentials(auth_metadata_plugin),
    )
    return OTLPSpanExporter(endpoint=_OTLP_ENDPOINT, credentials=channel_creds)


def setup_tracing() -> TracerProvider:
    """Registers a Cloud Trace-exporting TracerProvider as the global OTel
    provider. Idempotent -- safe to call from every Cloud Run cold start and
    every test/run_bill invocation without creating duplicate exporters."""
    global _tracer_provider
    if _tracer_provider is not None:
        return _tracer_provider
    # gcp.project_id is required by Cloud Trace's OTLP endpoint -- without
    # it, ingestion rejects every span with INVALID_ARGUMENT ("Resource is
    # missing required attribute \"gcp.project_id\"", confirmed live during
    # development). GoogleCloudResourceDetector would supply it under Cloud
    # Run, but not from a local machine, so it's set explicitly here to work
    # in both places.
    resource = Resource.create({"service.name": _SERVICE_NAME, "gcp.project_id": project_id()})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(build_otlp_span_exporter()))
    trace.set_tracer_provider(provider)
    _tracer_provider = provider
    return provider


def current_trace_id() -> str | None:
    """32-hex-char id of the currently active span's trace, or None when no
    span is active (matches the id format Cloud Trace URLs use)."""
    ctx = trace.get_current_span().get_span_context()
    if ctx is None or not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")


class CloudLoggingFormatter(logging.Formatter):
    """One JSON object per line so Cloud Run's log agent parses each record
    as structured Cloud Logging data (severity + message) instead of opaque
    text, stamped with the active span's trace id -- the
    `logging.googleapis.com/trace` field Cloud Logging uses to nest a log
    entry under its matching Cloud Trace span in the console."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        trace_id = current_trace_id()
        if trace_id:
            entry["logging.googleapis.com/trace"] = f"projects/{project_id()}/traces/{trace_id}"
        return json.dumps(entry)


def setup_cloud_logging(level: int = logging.INFO) -> None:
    """Attaches the structured formatter to a root-logger stream handler.
    Idempotent -- calling this more than once (e.g. once from app.py startup,
    once from every run_bill call) never attaches a second handler."""
    global _logging_configured
    if _logging_configured:
        return
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(CloudLoggingFormatter())
    root.addHandler(handler)
    _logging_configured = True
