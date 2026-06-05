"""Trusted workflow policy for validating declarative workflow graphs."""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from enum import Enum
from orcheo.nodes.registry import registry
from orcheo.workflow.trust.modes import WorkflowTrustMode
from orcheo.workflow.trust.schema import DeclarativeWorkflowGraph


logger = logging.getLogger(__name__)

DECLARATIVE_FORMAT = "orcheo-declarative-graph"

_BLOCKED_NODE_TYPES: frozenset[str] = frozenset(
    {
        "ExternalAgentNode",
        "ClaudeCodeNode",
        "CodexNode",
        "GeminiNode",
        "JavaScriptSandboxNode",
        "CodeNode",
    }
)


class PolicyRejectionReason(str, Enum):
    """Reason why a workflow was rejected by the trusted policy."""

    WRONG_FORMAT = "wrong_format"
    UNKNOWN_NODE_TYPE = "unknown_node_type"
    BLOCKED_NODE_TYPE = "blocked_node_type"
    NON_SERIALIZABLE_CONFIG = "non_serializable_config"
    EXTERNAL_AGENT_NODE = "external_agent_node"


@dataclass
class PolicyViolation:
    """One policy violation found during graph validation."""

    reason: PolicyRejectionReason
    detail: str
    node_id: str | None = None
    node_type: str | None = None


@dataclass
class PolicyResult:
    """Result of running the trusted workflow policy over a graph."""

    allowed: bool
    violations: list[PolicyViolation] = field(default_factory=list)

    def add_violation(self, violation: PolicyViolation) -> None:
        """Append a violation and mark result as not allowed."""
        self.violations.append(violation)
        self.allowed = False


class TrustedWorkflowPolicy:
    """Validates declarative workflow graphs against the production trust policy.

    In production mode only registered first-party nodes are allowed and
    blocked node types (external agents, code nodes, JS sandbox) are rejected.
    In self_host_unsafe/developer modes validation is skipped entirely.
    """

    def validate(
        self,
        graph: DeclarativeWorkflowGraph,
        mode: WorkflowTrustMode = WorkflowTrustMode.PRODUCTION,
    ) -> PolicyResult:
        """Validate a declarative graph against the given trust mode."""
        if mode != WorkflowTrustMode.PRODUCTION:
            return PolicyResult(allowed=True)

        result = PolicyResult(allowed=True)

        if graph.format != DECLARATIVE_FORMAT:
            result.add_violation(
                PolicyViolation(
                    reason=PolicyRejectionReason.WRONG_FORMAT,
                    detail=(
                        f"Expected format '{DECLARATIVE_FORMAT}', got '{graph.format}'."
                    ),
                )
            )
            return result

        for node in graph.nodes:
            self._validate_node(node.type, node.id, result)

        return result

    def _validate_node(
        self,
        node_type: str,
        node_id: str,
        result: PolicyResult,
    ) -> None:
        """Check one node type against the policy allowlist."""
        if node_type in _BLOCKED_NODE_TYPES:
            result.add_violation(
                PolicyViolation(
                    reason=PolicyRejectionReason.BLOCKED_NODE_TYPE,
                    detail=(
                        f"Node type '{node_type}' is not permitted in production mode."
                    ),
                    node_id=node_id,
                    node_type=node_type,
                )
            )
            return

        if registry.get_node(node_type) is None:
            result.add_violation(
                PolicyViolation(
                    reason=PolicyRejectionReason.UNKNOWN_NODE_TYPE,
                    detail=(
                        f"Node type '{node_type}' is not registered in the trusted "
                        "node registry."
                    ),
                    node_id=node_id,
                    node_type=node_type,
                )
            )


_default_policy = TrustedWorkflowPolicy()


def get_trusted_workflow_policy() -> TrustedWorkflowPolicy:
    """Return the default trusted workflow policy instance."""
    return _default_policy


__all__ = [
    "DECLARATIVE_FORMAT",
    "PolicyRejectionReason",
    "PolicyResult",
    "PolicyViolation",
    "TrustedWorkflowPolicy",
    "get_trusted_workflow_policy",
]
