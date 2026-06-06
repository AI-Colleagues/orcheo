"""Tests for workflow trust policy enforcement helpers."""

from __future__ import annotations
import pytest
from orcheo.workflow.trust.enforcement import (
    WorkflowPolicyViolationError,
    enforce_execution_policy,
    enforce_ingestion_policy,
)
from orcheo.workflow.trust.modes import WorkflowTrustMode
from orcheo.workflow.trust.policy import (
    PolicyResult,
    PolicyViolation,
    PolicyRejectionReason,
)
from orcheo.workflow.trust.schema import (
    DeclarativeNodeDef,
    DeclarativeWorkflowGraph,
)


def _blocked_graph() -> DeclarativeWorkflowGraph:
    return DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id="bad", type="CodeNode")]
    )


def _valid_graph() -> DeclarativeWorkflowGraph:
    return DeclarativeWorkflowGraph()


def test_workflow_policy_violation_error_message() -> None:
    violation = PolicyViolation(
        reason=PolicyRejectionReason.BLOCKED_NODE_TYPE,
        detail="CodeNode is not permitted.",
    )
    result = PolicyResult(allowed=False, violations=[violation])

    exc = WorkflowPolicyViolationError(result)

    assert "CodeNode is not permitted." in str(exc)
    assert exc.result is result


def test_workflow_policy_violation_error_multiple_violations() -> None:
    violations = [
        PolicyViolation(reason=PolicyRejectionReason.WRONG_FORMAT, detail="Bad format"),
        PolicyViolation(reason=PolicyRejectionReason.BLOCKED_NODE_TYPE, detail="Code"),
    ]
    result = PolicyResult(allowed=False, violations=violations)

    exc = WorkflowPolicyViolationError(result)

    assert "Bad format" in str(exc)
    assert "Code" in str(exc)


def test_enforce_ingestion_policy_passes_for_valid_graph() -> None:
    enforce_ingestion_policy(_valid_graph(), mode=WorkflowTrustMode.PRODUCTION)


def test_enforce_ingestion_policy_raises_for_blocked_node() -> None:
    with pytest.raises(WorkflowPolicyViolationError) as exc_info:
        enforce_ingestion_policy(_blocked_graph(), mode=WorkflowTrustMode.PRODUCTION)

    assert not exc_info.value.result.allowed


def test_enforce_ingestion_policy_uses_env_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "developer")
    enforce_ingestion_policy(_blocked_graph())


def test_enforce_ingestion_policy_uses_default_policy_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "production")
    with pytest.raises(WorkflowPolicyViolationError):
        enforce_ingestion_policy(_blocked_graph())


def test_enforce_execution_policy_passes_for_valid_graph() -> None:
    enforce_execution_policy(_valid_graph(), mode=WorkflowTrustMode.PRODUCTION)


def test_enforce_execution_policy_raises_for_blocked_node() -> None:
    with pytest.raises(WorkflowPolicyViolationError) as exc_info:
        enforce_execution_policy(_blocked_graph(), mode=WorkflowTrustMode.PRODUCTION)

    assert not exc_info.value.result.allowed


def test_enforce_execution_policy_uses_env_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "self_host_unsafe")
    enforce_execution_policy(_blocked_graph())


def test_enforce_execution_policy_uses_default_policy_when_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_WORKFLOW_TRUST_MODE", "production")
    with pytest.raises(WorkflowPolicyViolationError):
        enforce_execution_policy(_blocked_graph())


def test_enforce_ingestion_policy_with_explicit_policy() -> None:
    """Passing an explicit policy bypasses the None check (line 36->38 false branch)."""
    from orcheo.workflow.trust.policy import get_trusted_workflow_policy

    policy = get_trusted_workflow_policy()
    enforce_ingestion_policy(
        _valid_graph(), mode=WorkflowTrustMode.PRODUCTION, policy=policy
    )


def test_enforce_execution_policy_with_explicit_policy() -> None:
    """Passing an explicit policy bypasses the None check (line 58->60 false branch)."""
    from orcheo.workflow.trust.policy import get_trusted_workflow_policy

    policy = get_trusted_workflow_policy()
    enforce_execution_policy(
        _valid_graph(), mode=WorkflowTrustMode.PRODUCTION, policy=policy
    )
