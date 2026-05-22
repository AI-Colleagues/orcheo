"""Tests for the Celery app configuration and worker process init signal."""

from __future__ import annotations
import importlib
import sys
import types
from typing import Any
import pytest


def _get_celery_app_module() -> Any:
    """Return the orcheo_backend.worker.celery_app *module* (not the Celery instance)."""
    return importlib.import_module("orcheo_backend.worker.celery_app")


def test_configure_sandbox_for_worker_calls_ensure_sandbox_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_configure_sandbox_for_worker calls ensure_sandbox_configured on success."""
    celery_module = _get_celery_app_module()

    calls: list[str] = []

    def _fake_ensure() -> None:
        calls.append("called")

    fake_sandbox = types.ModuleType("orcheo_backend.app.sandbox")
    fake_sandbox.ensure_sandbox_configured = _fake_ensure  # type: ignore[attr-defined]

    old = sys.modules.get("orcheo_backend.app.sandbox")
    sys.modules["orcheo_backend.app.sandbox"] = fake_sandbox
    try:
        celery_module._configure_sandbox_for_worker()
    finally:
        if old is None:
            sys.modules.pop("orcheo_backend.app.sandbox", None)
        else:
            sys.modules["orcheo_backend.app.sandbox"] = old

    assert calls == ["called"]


def test_configure_sandbox_for_worker_reraises_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_configure_sandbox_for_worker re-raises exceptions after logging."""
    celery_module = _get_celery_app_module()

    logged: list[str] = []

    def _fail_ensure() -> None:
        raise RuntimeError("sandbox config failed")

    class _StubLogger:
        def exception(self, *args: Any, **kwargs: Any) -> None:
            logged.append(args[0] if args else "")

    fake_sandbox = types.ModuleType("orcheo_backend.app.sandbox")
    fake_sandbox.ensure_sandbox_configured = _fail_ensure  # type: ignore[attr-defined]

    old = sys.modules.get("orcheo_backend.app.sandbox")
    sys.modules["orcheo_backend.app.sandbox"] = fake_sandbox
    original_logger = celery_module.logger
    celery_module.logger = _StubLogger()
    try:
        with pytest.raises(RuntimeError, match="sandbox config failed"):
            celery_module._configure_sandbox_for_worker()
    finally:
        celery_module.logger = original_logger
        if old is None:
            sys.modules.pop("orcheo_backend.app.sandbox", None)
        else:
            sys.modules["orcheo_backend.app.sandbox"] = old

    assert logged  # the exception was logged before re-raising
