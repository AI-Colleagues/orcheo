"""Unit tests for backend logging helpers."""

from __future__ import annotations
import importlib
from types import SimpleNamespace
from typing import Any
import pytest
from orcheo.tracing.model_metadata import NAMESPACE_METADATA_KEY


backend_app = importlib.import_module("orcheo_backend.app")


def _capture_logger() -> tuple[list[tuple[str, tuple[Any, ...]]], SimpleNamespace]:
    """Return a simple logger that records debug messages."""

    captured: list[tuple[str, tuple[Any, ...]]] = []

    def debug(message: str, *args: Any) -> None:
        captured.append((message, args))

    return captured, SimpleNamespace(debug=debug)


def test_log_sensitive_debug_emits_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_log_sensitive_debug delegates to logger.debug when flag enabled."""

    captured, logger = _capture_logger()

    monkeypatch.setattr(backend_app, "_should_log_sensitive_debug", True)
    monkeypatch.setattr(backend_app, "logger", logger)

    backend_app._log_sensitive_debug("secret %s", "value")

    assert captured == [("secret %s", ("value",))]


def test_log_step_debug_emits_for_each_node(monkeypatch: pytest.MonkeyPatch) -> None:
    """_log_step_debug logs boundaries and payload for each node."""

    captured, logger = _capture_logger()

    monkeypatch.setattr(backend_app, "_should_log_sensitive_debug", True)
    monkeypatch.setattr(backend_app, "logger", logger)

    backend_app._log_step_debug({"alpha": {"status": "ok"}})

    assert len(captured) == 4
    assert captured[0] == ("=" * 80, ())
    assert captured[1][0] == "Node executed: %s"
    assert captured[1][1] == ("alpha",)
    assert captured[2][0] == "Node output: %s"
    assert captured[2][1] == ({"status": "ok"},)
    assert captured[3] == ("=" * 80, ())


def test_log_step_debug_skips_namespace_metadata_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The namespace metadata entry is ignored while logging step payloads."""

    captured, logger = _capture_logger()

    monkeypatch.setattr(backend_app, "_should_log_sensitive_debug", True)
    monkeypatch.setattr(backend_app, "logger", logger)

    backend_app._log_step_debug(
        {NAMESPACE_METADATA_KEY: {"status": "meta"}, "alpha": {"status": "ok"}}
    )

    assert len(captured) == 4
    assert all(NAMESPACE_METADATA_KEY not in message for message, _args in captured)


def test_log_final_state_debug_emits_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_log_final_state_debug logs final state when flag enabled."""

    captured, logger = _capture_logger()

    monkeypatch.setattr(backend_app, "_should_log_sensitive_debug", True)
    monkeypatch.setattr(backend_app, "logger", logger)

    backend_app._log_final_state_debug({"result": "done"})

    assert captured[0] == ("=" * 80, ())
    assert captured[1] == ("Final state values: %s", ({"result": "done"},))
    assert captured[2] == ("=" * 80, ())
