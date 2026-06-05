"""Ingestion-time and execution-time policy enforcement for trusted workflows."""

from __future__ import annotations
import logging
from orcheo.workflow.trust.modes import WorkflowTrustMode, get_workflow_trust_mode
from orcheo.workflow.trust.policy import (
    PolicyResult,
    TrustedWorkflowPolicy,
    get_trusted_workflow_policy,
)
from orcheo.workflow.trust.schema import DeclarativeWorkflowGraph


logger = logging.getLogger(__name__)


class WorkflowPolicyViolationError(ValueError):
    """Raised when a workflow graph fails the trusted policy check."""

    def __init__(self, result: PolicyResult) -> None:
        """Initialize with the failed policy result."""
        self.result = result
        violations_text = "; ".join(v.detail for v in result.violations)
        super().__init__(f"Workflow policy violation: {violations_text}")


def enforce_ingestion_policy(
    graph: DeclarativeWorkflowGraph,
    *,
    mode: WorkflowTrustMode | None = None,
    policy: TrustedWorkflowPolicy | None = None,
) -> None:
    """Validate a graph at ingestion time; raise on policy failure."""
    if mode is None:
        mode = get_workflow_trust_mode()
    if policy is None:
        policy = get_trusted_workflow_policy()
    result = policy.validate(graph, mode=mode)
    if not result.allowed:
        logger.warning(
            "Workflow graph rejected at ingestion: %s",
            [v.detail for v in result.violations],
        )
        raise WorkflowPolicyViolationError(result)


def enforce_execution_policy(
    graph: DeclarativeWorkflowGraph,
    *,
    mode: WorkflowTrustMode | None = None,
    policy: TrustedWorkflowPolicy | None = None,
) -> None:
    """Validate a graph at execution time; raise on policy failure."""
    if mode is None:
        mode = get_workflow_trust_mode()
    if policy is None:
        policy = get_trusted_workflow_policy()
    result = policy.validate(graph, mode=mode)
    if not result.allowed:
        logger.warning(
            "Workflow graph rejected at execution: %s",
            [v.detail for v in result.violations],
        )
        raise WorkflowPolicyViolationError(result)


__all__ = [
    "WorkflowPolicyViolationError",
    "enforce_execution_policy",
    "enforce_ingestion_policy",
]
