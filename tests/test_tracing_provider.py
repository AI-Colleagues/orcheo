"""Tests for tracing provider configuration helpers."""

from __future__ import annotations
from typing import Any
from unittest.mock import Mock
import pytest
from orcheo.tracing import provider


def test_configure_tracing_skips_when_already_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_tracing should return early when a provider is already set."""

    monkeypatch.setattr(provider, "_configured", True)
    get_settings = Mock()
    monkeypatch.setattr(provider, "get_settings", get_settings)

    provider.configure_tracing()

    get_settings.assert_not_called()


def test_configure_tracing_installs_console_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_tracing wires the requested exporter into the provider."""

    monkeypatch.setattr(provider, "_configured", False)
    settings = {
        "TRACING_EXPORTER": "console",
        "TRACING_SAMPLE_RATIO": "0.25",
        "TRACING_SERVICE_NAME": "custom",
    }
    monkeypatch.setattr(provider, "get_settings", lambda: settings)
    span_processors: list[Any] = []

    class FakeBatchSpanProcessor:
        def __init__(self, exporter: Any) -> None:
            span_processors.append(exporter)

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int | None = None) -> bool:
            return True

    monkeypatch.setattr(provider, "BatchSpanProcessor", FakeBatchSpanProcessor)
    monkeypatch.setattr(provider, "ConsoleSpanExporter", lambda: "console-exporter")
    monkeypatch.setattr(
        provider.trace, "set_tracer_provider", lambda provider_obj: None
    )

    provider.configure_tracing(force=True)

    assert span_processors == ["console-exporter"]


def test_configure_tracing_includes_workspace_resource_attribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """configure_tracing should surface the active workspace in resource metadata."""

    monkeypatch.setattr(provider, "_configured", False)
    monkeypatch.setenv("ORCHEO_WORKSPACE", "workspace-1")
    settings = {
        "TRACING_EXPORTER": "none",
        "TRACING_SAMPLE_RATIO": "1.0",
        "TRACING_SERVICE_NAME": "custom",
    }
    monkeypatch.setattr(provider, "get_settings", lambda: settings)
    captured: dict[str, Any] = {}

    class FakeResource:
        @staticmethod
        def create(attrs: dict[str, Any]) -> object:
            captured.update(attrs)
            return object()

    monkeypatch.setattr(provider, "Resource", FakeResource)
    monkeypatch.setattr(provider, "TracerProvider", lambda **_: object())
    monkeypatch.setattr(
        provider.trace, "set_tracer_provider", lambda provider_obj: None
    )

    provider.configure_tracing(force=True)

    assert captured["orcheo.workspace"] == "workspace-1"


def test_get_tracer_invokes_configure_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_tracer should trigger configuration the first time it is called."""

    monkeypatch.setattr(provider, "_configured", False)
    configured: dict[str, bool] = {"called": False}

    def _fake_configure() -> None:
        configured["called"] = True
        monkeypatch.setattr(provider, "_configured", True)

    monkeypatch.setattr(provider, "configure_tracing", _fake_configure)
    monkeypatch.setattr(provider.trace, "get_tracer", lambda name: f"tracer:{name}")

    tracer_instance = provider.get_tracer("workflow")

    assert tracer_instance == "tracer:workflow"
    assert configured["called"] is True


def test_build_exporter_handles_console_and_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_build_exporter should support console exporters and warn on unknown types."""

    monkeypatch.setattr(provider, "ConsoleSpanExporter", lambda: "console-exporter")
    console = provider._build_exporter("console", {})
    assert console == "console-exporter"

    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        provider.logger,
        "warning",
        lambda message, name: warnings.append((message, name)),
    )
    assert provider._build_exporter("custom", {}) is None
    assert warnings


def test_build_exporter_configures_otlp_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OTLP exporter should be instantiated with endpoint and insecure options."""

    class FakeOTLPExporter:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(provider, "OTLPSpanExporter", FakeOTLPExporter)
    settings = {"TRACING_ENDPOINT": "https://otel", "TRACING_INSECURE": True}

    exporter = provider._build_exporter("otlp", settings)

    assert isinstance(exporter, FakeOTLPExporter)
    assert exporter.kwargs["endpoint"] == "https://otel"
    assert exporter.kwargs["insecure"] is True


def test_build_exporter_excludes_optional_otlp_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OTLP exporter should skip optional kwargs when not configured."""

    class FakeOTLPExporter:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

    monkeypatch.setattr(provider, "OTLPSpanExporter", FakeOTLPExporter)

    exporter = provider._build_exporter("otlp", {})

    assert exporter.kwargs == {}


def test_build_exporter_requires_otlp_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requesting OTLP without the dependency should raise a helpful error."""

    monkeypatch.setattr(provider, "OTLPSpanExporter", None)
    errors: list[str] = []
    monkeypatch.setattr(provider.logger, "error", lambda msg: errors.append(msg))

    with pytest.raises(RuntimeError):
        provider._build_exporter("otlp", {})

    assert errors
