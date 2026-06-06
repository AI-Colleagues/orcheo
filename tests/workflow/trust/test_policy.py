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


def test_production_rejects_non_serializable_config(
    policy: TrustedWorkflowPolicy,
) -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[
            DeclarativeNodeDef(
                id="rss",
                type="RSSNode",
                config={"fn": lambda: None},
            )
        ]
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.NON_SERIALIZABLE_CONFIG
        for v in result.violations
    )


def test_production_rejects_unregistered_trusted_node(
    policy: TrustedWorkflowPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orcheo.workflow.trust.policy as policy_module

    monkeypatch.setattr(policy_module.registry, "get_node", lambda node_type: None)
    graph = DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id="cron1", type="CronTriggerNode")]
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.UNKNOWN_NODE_TYPE for v in result.violations
    )


def test_production_allows_unregistered_abstract_trusted_node(
    policy: TrustedWorkflowPolicy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import orcheo.workflow.trust.policy as policy_module

    monkeypatch.setattr(policy_module.registry, "get_node", lambda node_type: None)
    graph = DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id="ai1", type="AINode")]
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert result.allowed


def test_production_allows_valid_edges(policy: TrustedWorkflowPolicy) -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id="rss", type="RSSNode")],
        edges=[
            DeclarativeEdgeDef(source="START", target="rss"),
            DeclarativeEdgeDef(source="rss", target="END"),
        ],
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert result.allowed


def test_production_rejects_conditional_edge_invalid_source(
    policy: TrustedWorkflowPolicy,
) -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id="rss", type="RSSNode")],
        conditional_edges=[
            DeclarativeConditionalEdgeDef(
                source="undeclared_node",
                branch="route",
                mapping={"ok": "rss"},
            )
        ],
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.INVALID_EDGE for v in result.violations
    )


def test_production_rejects_conditional_edge_default_to_undeclared(
    policy: TrustedWorkflowPolicy,
) -> None:
    graph = DeclarativeWorkflowGraph(
        nodes=[DeclarativeNodeDef(id="rss", type="RSSNode")],
        conditional_edges=[
            DeclarativeConditionalEdgeDef(
                source="rss",
                branch="route",
                mapping={},
                default="missing_target",
            )
        ],
    )

    result = policy.validate(graph, mode=WorkflowTrustMode.PRODUCTION)

    assert not result.allowed
    assert any(
        v.reason == PolicyRejectionReason.INVALID_EDGE for v in result.violations
    )


def test_find_executable_config_paths_in_list() -> None:
    from orcheo.workflow.trust.policy import _find_executable_config_paths

    config = {"steps": [{"code": "print('hi')"}]}
    paths = list(_find_executable_config_paths(config))

    assert any("code" in p for p in paths)


def test_validate_production_node_types_returns_offenders() -> None:
    from orcheo.workflow.trust.policy import validate_production_node_types

    offenders = validate_production_node_types(["RSSNode", "ClaudeCodeNode", "Mystery"])
    assert "ClaudeCodeNode" in offenders
    assert "Mystery" in offenders
    assert "RSSNode" not in offenders


def test_is_production_trusted_node_type() -> None:
    from orcheo.workflow.trust.policy import is_production_trusted_node_type

    assert is_production_trusted_node_type("RSSNode") is True
    assert is_production_trusted_node_type("ClaudeCodeNode") is False
    assert is_production_trusted_node_type("SomeMadeUpNode") is False
