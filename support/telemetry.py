"""One Phoenix process receives OTLP traces. The provider is installed before any agent is built (O-1..O-3)."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from openinference.instrumentation.google_genai import GoogleGenAIInstrumentor

from support.config import PHOENIX_URL

_READY = False


def setup_telemetry() -> None:
    global _READY
    if _READY:
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "customer-support"}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{PHOENIX_URL}/v1/traces"))
    )
    trace.set_tracer_provider(provider)
    GoogleGenAIInstrumentor().instrument()
    _READY = True


def tracer() -> trace.Tracer:
    return trace.get_tracer("customer-support")


def force_flush() -> None:
    provider = trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if flush:
        flush()
