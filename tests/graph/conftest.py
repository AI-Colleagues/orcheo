"""Conftest for graph ingestion tests — disables RP sandbox so scripts can import orcheo.graph.state."""

from __future__ import annotations
import pytest


@pytest.fixture(autouse=True)
def _unsafe_execution_for_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable the RestrictedPython sandbox so tests can use orcheo.graph.state imports."""
    monkeypatch.setenv("ORCHEO_WORKFLOW_UNSAFE_EXECUTION", "true")
