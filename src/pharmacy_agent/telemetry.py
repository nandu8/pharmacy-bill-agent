"""Observability (PRD S8 / T54): exports ADK's OpenTelemetry spans to Cloud
Trace and structures stdout logging for Cloud Logging trace correlation.

ADK creates spans via the global OTel TracerProvider on every LLM call and
tool call (google/adk/telemetry/tracing.py) but ships no exporter of its
own -- without an explicit provider, the OTel API defaults to a no-op
provider and nothing is ever recorded. `setup_tracing` registers a provider
using `CloudTraceSpanExporter` (`opentelemetry-exporter-gcp-trace`), which
writes via the `google-cloud-trace` v2 client rather than OTel's own OTLP
exporter stack.

That choice isn't the currently-recommended one -- the
GoogleCloudPlatform/opentelemetry-operations-python migration guide points
at the standard OTLP exporter pointed to Cloud Trace's native ingestion
endpoint instead, since CloudTraceSpanExporter is deprecated -- but the OTLP
path turned out to be a dead end for this project specifically: its
`opentelemetry-proto` dependency hard-requires `protobuf<7.0`, which
directly conflicts with the exact `protobuf==7.35.1` pin `requirements.txt`
already carries for the T52 Firestore/Cloud-Run compatibility fix. Confirmed
live with a from-scratch venv install (not just an incrementally-mutated
one) -- `pip install -r requirements.txt` hit a hard `ResolutionImpossible`
on that combination. `google-cloud-trace` (what CloudTraceSpanExporter
actually calls) shares its protobuf/google-api-core stack with
`google-cloud-firestore`, so it has no such conflict.

CloudTraceSpanExporter's write path is still fully functional (confirmed
live -- `export()` returns `SpanExportResult.SUCCESS`); only its read
counterpart (v1 `TraceServiceClient.get_trace`, tried during development to
verify this module) turned out to depend on a legacy "trace bucket"
resource this project doesn't have provisioned, 404ing even after a
genuinely successful export. `tests/test_telemetry.py`'s live check
verifies the exporter's own SUCCESS/FAILURE return value instead of trying
to read the trace back.

`agent/loop.py` wraps each bill run in one outer span so a single trace id
covers every turn, which is what `bills.trace_id` (S10 schema) records for
the status page (S7.11) to link to.
"""
from __future__ import annotations

import json
import logging
import os

from opentelemetry import trace
from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_SERVICE_NAME = "pharmacy-bill-agent"
_PROJECT_ENV_VAR = "GOOGLE_CLOUD_PROJECT"
_DEFAULT_PROJECT = "pharmacy-bill-agent"

_tracer_provider: TracerProvider | None = None
_logging_configured = False


def project_id() -> str:
    return os.environ.get(_PROJECT_ENV_VAR, _DEFAULT_PROJECT)


def build_cloud_trace_span_exporter() -> CloudTraceSpanExporter:
    return CloudTraceSpanExporter(project_id=project_id())


def setup_tracing() -> TracerProvider:
    """Registers a Cloud Trace-exporting TracerProvider as the global OTel
    provider. Idempotent -- safe to call from every Cloud Run cold start and
    every test/run_bill invocation without creating duplicate exporters."""
    global _tracer_provider
    if _tracer_provider is not None:
        return _tracer_provider
    resource = Resource.create({"service.name": _SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(build_cloud_trace_span_exporter()))
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
