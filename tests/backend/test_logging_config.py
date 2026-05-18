"""Tests for Orcheo backend logging helpers."""

from __future__ import annotations
import importlib
import json
import logging
import pytest


logging_config = importlib.import_module("orcheo_backend.app.logging_config")


def _logger_names() -> tuple[str, ...]:
    return (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "fastapi",
        "orcheo",
        "orcheo_backend",
    )


def test_configure_logging_applies_requested_log_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reloading the module applies ORCHEO_LOG_LEVEL to the logger names."""
    monkeypatch.setenv("ORCHEO_LOG_LEVEL", "debug")
    reloaded = importlib.reload(logging_config)

    for name in _logger_names():
        assert logging.getLogger(name).level == logging.DEBUG

    assert reloaded.get_logger().name == "orcheo_backend.app"
    assert reloaded.get_logger("custom.logger").name == "custom.logger"


def test_configure_logging_renders_extra_fields(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Structured logs should include extra fields from stdlib logging."""
    monkeypatch.setenv("ORCHEO_LOG_LEVEL", "info")
    monkeypatch.setenv("ORCHEO_LOG_FORMAT", "json")
    importlib.reload(logging_config)

    logger = logging.getLogger("orcheo.logging_test")
    logger.info(
        "WeCom Customer Service event received",
        extra={
            "event": "wecom_customer_service",
            "status": "received",
            "open_kf_id": "kf_123",
        },
    )

    captured = capsys.readouterr().err.strip().splitlines()
    payload = json.loads(captured[-1])

    assert payload["message"] == "WeCom Customer Service event received"
    assert payload["event"] == "wecom_customer_service"
    assert payload["status"] == "received"
    assert payload["open_kf_id"] == "kf_123"


def test_configure_logging_uses_console_renderer_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reloading with the default format should exercise the console branch."""
    monkeypatch.delenv("ORCHEO_LOG_FORMAT", raising=False)
    monkeypatch.setenv("ORCHEO_LOG_LEVEL", "info")

    reloaded = importlib.reload(logging_config)

    assert reloaded.get_logger().name == "orcheo_backend.app"
