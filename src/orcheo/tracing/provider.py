"""Tracer provider configuration utilities."""

from __future__ import annotations
import logging
import os
import threading
from typing import Any
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
from opentelemetry.trace import Tracer
from orcheo.config import get_settings


try:  # pragma: no cover - import guard
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
except Exception:  # pragma: no cover - optional dependency safety
    OTLPSpanExporter = None  # type: ignore[misc, assignment]


logger = logging.getLogger(__name__)
_lock = threading.Lock()
_configured = False


def configure_tracing(*, force: bool = False) -> None:
    """Configure the global tracer provider based on environment settings."""
    global _configured  # noqa: PLW0603
    with _lock:
        if _configured and not force:
            return

        settings = get_settings()
        exporter_name = str(settings.get("TRACING_EXPORTER", "none")).lower()
        sample_ratio = float(settings.get("TRACING_SAMPLE_RATIO", 1.0))
        resource_attributes = {
            "service.name": settings.get("TRACING_SERVICE_NAME", "orcheo-backend"),
        }
        workspace_id = os.environ.get("ORCHEO_WORKSPACE")
        if workspace_id and workspace_id.strip():
            resource_attributes["orcheo.workspace"] = workspace_id.strip()
        resource = Resource.create(resource_attributes)

        provider = TracerProvider(
            sampler=TraceIdRatioBased(sample_ratio),
            resource=resource,
        )

        exporter = _build_exporter(exporter_name, settings)
        if exporter is not None:
            provider.add_span_processor(BatchSpanProcessor(exporter))
        else:
            logger.debug("Tracing exporter '%s' disabled or unavailable", exporter_name)

        trace.set_tracer_provider(provider)
        _configured = True


def get_tracer(name: str) -> Tracer:
    """Return a tracer instance using the configured provider."""
    if not _configured:
        configure_tracing()
    return trace.get_tracer(name)


def _build_exporter(exporter_name: str, settings: Any) -> SpanExporter | None:
    """Instantiate the configured span exporter if available."""
    if exporter_name in {"", "none", "disabled"}:
        return None
    if exporter_name == "console":
        return ConsoleSpanExporter()
    if exporter_name == "otlp":
        if OTLPSpanExporter is None:  # pragma: no cover - dependency missing
            msg = (
                "OTLP exporter requested but dependency unavailable. "
                "Install 'opentelemetry-exporter-otlp' to enable OTLP tracing."
            )
            logger.error(msg)
            raise RuntimeError(msg)
        endpoint = settings.get("TRACING_ENDPOINT")
        insecure = bool(settings.get("TRACING_INSECURE", False))
        exporter_kwargs: dict[str, Any] = {}
        if endpoint:
            exporter_kwargs["endpoint"] = str(endpoint)
        if insecure:
            exporter_kwargs["insecure"] = True
        return OTLPSpanExporter(**exporter_kwargs)

    logger.warning('Unknown tracing exporter: "%s"', exporter_name)
    return None


__all__ = ["configure_tracing", "get_tracer"]
