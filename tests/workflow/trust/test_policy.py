"""Tests for the trusted workflow policy."""

from __future__ import annotations
import pytest
from orcheo.workflow.trust.modes import WorkflowTrustMode
from orcheo.workflow.trust.policy import (
    PolicyRejectionReason,
    TrustedWorkflowPolicy,
)
from orcheo.workflow.trust.schema import (
    DeclarativeConditionalEdgeDef,
    DeclarativeEdgeDef,
    DeclarativeNodeDef,
    DeclarativeWorkflowGraph,
)


@pytest.fixture()
def policy() -> TrustedWorkflowPolicy:
    """Return a fresh policy instance."""
    return TrustedWorkflowPolicy()


def _graph(*node_types: str) -> DeclarativeWorkflowGraph:
    return DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id=t.lower(), type=t) for t in node_types],
    )


def test_production_rejects_wrong_format(policy: TrustedWorkflowPolicy) -> None:
    graph = DeclarativeWorkflowGraph(format="python-script")
    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)
    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.WRONG_FORMAT for v in result.violations
    )


@pytest.mark.parametrize(
    "node_type",
    ["ClaudeCodeNode", "CodexNode", "GeminiNode", "ExternalAgentNode"],
)
def test_production_rejects_external_agent_nodes(
    policy: TrustedWorkflowPolicy, node_type: str
) -> None:
    result = policy.validate(_graph(node_type), mode=WorkflowTrustMode.PRODUCTION)
    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.BLOCKED_NODE_TYPE for v in result.violations
    )


def test_production_rejects_code_node(policy: TrustedWorkflowPolicy) -> None:
    result = policy.validate(_graph("CodeNode"), mode=WorkflowTrustMode.PRODUCTION)
    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.BLOCKED_NODE_TYPE for v in result.violations
    )


def test_production_rejects_unknown_node(policy: TrustedWorkflowPolicy) -> None:
    result = policy.validate(
        _graph("SomeFancyPluginNode"), mode=WorkflowTrustMode.PRODUCTION
    )
    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.UNTRUSTED_NODE_TYPE for v in result.violations
    )


def test_self_host_unsafe_allows_anything(policy: TrustedWorkflowPolicy) -> None:
    result = policy.validate(
        _graph("ClaudeCodeNode"), mode=WorkflowTrustMode.SELF_HOST_UNSAFE
    )
    assert result.allowed


def test_developer_allows_anything(policy: TrustedWorkflowPolicy) -> None:
    result = policy.validate(_graph("CodexNode"), mode=WorkflowTrustMode.DEVELOPER)
    assert result.allowed


def test_empty_graph_passes_production(policy: TrustedWorkflowPolicy) -> None:
    result = policy.validate(
        DeclarativeWorkflowGraph(), mode=WorkflowTrustMode.PRODUCTION
    )
    assert result.allowed


def test_production_rejects_executable_config_keys(
    policy: TrustedWorkflowPolicy,
) -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(
                id="rss",
                type="RSSNode",
                config={"nested": {"script": "print('unsafe')"}},
            )
        ]
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.EXECUTABLE_CONFIG for v in result.violations
    )


def test_production_rejects_edges_to_undeclared_nodes(
    policy: TrustedWorkflowPolicy,
) -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id="rss", type="RSSNode")],
        edges=[DeclarativeEdgeDef(source="START", target="missing")],
        conditional_edges=[
            DeclarativeConditionalEdgeDef(
                source="rss", branch="route", mapping={"ok": "also_missing"}
            )
        ],
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.INVALID_EDGE for v in result.violations
    )
