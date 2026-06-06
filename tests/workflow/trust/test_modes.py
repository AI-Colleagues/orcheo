"""Tests for workflow trust mode helpers."""

from __future__ import annotations
import pytest
from orcheo.workflow.trust.modes import (
    WorkflowTrustMode,
    get_workflow_trust_mode,
    is_production_trust_mode,
)


def test_get_workflow_trust_mode_returns_production_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORCHEO_WORKFLOW_TRUST_MODE", raising=False)
    assert get_workflow_trust_mode() == WorkflowTrustMode.PRODUCTION


def test_get_workflow_trust_mode_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "developer")
    assert get_workflow_trust_mode() == WorkflowTrustMode.DEVELOPER


def test_get_workflow_trust_mode_falls_back_on_invalid_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "totally_unknown_mode")
    assert get_workflow_trust_mode() == WorkflowTrustMode.PRODUCTION


def test_is_production_trust_mode_returns_true_for_production() -> None:
    assert is_production_trust_mode(WorkflowTrustMode.PRODUCTION) is True


def test_is_production_trust_mode_returns_false_for_developer() -> None:
    assert is_production_trust_mode(WorkflowTrustMode.DEVELOPER) is False


def test_is_production_trust_mode_uses_env_when_mode_is_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "production")
    assert is_production_trust_mode() is True

    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "self_host_unsafe")
    assert is_production_trust_mode() is False
