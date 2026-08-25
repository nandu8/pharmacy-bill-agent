import logging

from opentelemetry import trace as otel_trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExportResult

from pharmacy_agent.telemetry import (
    CloudLoggingFormatter,
    build_otlp_span_exporter,
    current_trace_id,
    project_id,
    setup_cloud_logging,
    setup_tracing,
)


def test_current_trace_id_is_none_outside_a_span():
    assert current_trace_id() is None


def test_setup_tracing_is_idempotent():
    provider_a = setup_tracing()
    provider_b = setup_tracing()
    assert provider_a is provider_b
    assert otel_trace.get_tracer_provider() is provider_a


def test_current_trace_id_matches_the_active_span_inside_a_span():
    setup_tracing()
    tracer = otel_trace.get_tracer(__name__)
    with tracer.start_as_current_span("test-span") as span:
        trace_id = current_trace_id()
        assert trace_id is not None
        assert len(trace_id) == 32
        int(trace_id, 16)  # must be valid hex
        expected = format(span.get_span_context().trace_id, "032x")
        assert trace_id == expected
    assert current_trace_id() is None


def test_cloud_logging_formatter_emits_json_with_severity_and_message():
    setup_tracing()
    formatter = CloudLoggingFormatter()
    record = logging.LogRecord(
        name="pharmacy_agent.test",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="something happened: %s",
        args=("detail",),
        exc_info=None,
    )
    formatted = formatter.format(record)

    import json

    entry = json.loads(formatted)
    assert entry["severity"] == "WARNING"
    assert entry["message"] == "something happened: detail"
    assert entry["logger"] == "pharmacy_agent.test"
    assert "logging.googleapis.com/trace" not in entry


def test_cloud_logging_formatter_includes_trace_id_inside_a_span():
    setup_tracing()
    tracer = otel_trace.get_tracer(__name__)
    formatter = CloudLoggingFormatter()
    record = logging.LogRecord(
        name="pharmacy_agent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="in a span",
        args=(),
        exc_info=None,
    )
    with tracer.start_as_current_span("test-span-for-logging"):
        import json

        entry = json.loads(formatter.format(record))
        trace_field = entry["logging.googleapis.com/trace"]

    assert trace_field.startswith(f"projects/{project_id()}/traces/")
    assert len(trace_field.rsplit("/", 1)[-1]) == 32


def test_setup_cloud_logging_is_idempotent():
    root = logging.getLogger()
    before = len(root.handlers)
    setup_cloud_logging()
    setup_cloud_logging()
    after = len(root.handlers)
    assert after - before <= 1


def test_otlp_exporter_accepts_a_real_span_from_cloud_trace():
    # Live check: Cloud Trace's v1 read API (`TraceServiceClient.get_trace`)
    # depends on a legacy "trace bucket" resource this project doesn't have
    # provisioned (404 "_Trace bucket not found" even after a genuinely
    # successful export, confirmed manually during development), so reading
    # the trace back isn't a reliable signal here. Exercising the exporter's
    # own synchronous SUCCESS/FAILURE return value is: it's a real network
    # call to telemetry.googleapis.com that only succeeds with valid GCP
    # auth and a project actually allowed to ingest traces.
    provider = TracerProvider(
        resource=Resource.create(
            {"service.name": "telemetry-live-check", "gcp.project_id": project_id()}
        )
    )
    tracer = provider.get_tracer(__name__)
    with tracer.start_as_current_span("telemetry-live-check-span") as span:
        span.set_attribute("check", "test_telemetry.py")
        recorded_span = span

    exporter = build_otlp_span_exporter()
    result = exporter.export([recorded_span])
    assert result == SpanExportResult.SUCCESS
