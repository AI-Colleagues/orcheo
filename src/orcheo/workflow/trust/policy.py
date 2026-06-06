"""Trusted workflow policy for validating declarative workflow graphs."""

from __future__ import annotations
import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
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
        "CodeNode",
    }
)

PRODUCTION_TRUSTED_NODE_TYPES: frozenset[str] = frozenset(
    {
        "AgentNode",
        "AgentReplyExtractorNode",
        "AgentensorNode",
        "AINode",
        "BrowserActionNode",
        "BrowserCloseNode",
        "BrowserExtractNode",
        "BrowserNavigateNode",
        "BrowserNode",
        "BrowserWaitNode",
        "ChunkEmbeddingNode",
        "ChunkingStrategyNode",
        "CronTriggerNode",
        "DataTransformNode",
        "DelayNode",
        "DiscordBotListenerNode",
        "DiscordEventsParserNode",
        "DocumentLoaderNode",
        "EmailNode",
        "ForLoopNode",
        "GraphStoreAppendMessageNode",
        "HttpPollingTriggerNode",
        "HttpRequestNode",
        "JsonProcessingNode",
        "LLMNode",
        "ManualTriggerNode",
        "MergeNode",
        "MetadataExtractorNode",
        "MongoDBAggregateNode",
        "MongoDBEnsureSearchIndexNode",
        "MongoDBEnsureVectorIndexNode",
        "MongoDBFindNode",
        "MongoDBHybridSearchNode",
        "MongoDBInsertManyNode",
        "MongoDBNode",
        "MongoDBUpdateManyNode",
        "MongoDBUpsertManyNode",
        "NoOpTaskNode",
        "PostgresNode",
        "QQBotListenerNode",
        "RSSNode",
        "SetVariableNode",
        "SlackEventsParserNode",
        "SlackNode",
        "TelegramBotListenerNode",
        "TelegramEventsParserNode",
        "TelegramNode",
        "TextEmbeddingNode",
        "VectorStoreUpsertNode",
        "WebhookTriggerNode",
        "WeComEventsParserNode",
    }
)

_UNREGISTERED_ABSTRACT_TRUSTED_NODE_TYPES: frozenset[str] = frozenset(
    {
        "AINode",
        "BrowserNode",
        "DiscordEventsParserNode",
        "TelegramNode",
    }
)

_EXECUTABLE_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "code",
        "command",
        "entrypoint",
        "python",
        "script",
        "shell",
        "source",
    }
)


class PolicyRejectionReason(str, Enum):
    """Reason why a workflow was rejected by the trusted policy."""

    WRONG_FORMAT = "wrong_format"
    UNKNOWN_NODE_TYPE = "unknown_node_type"
    BLOCKED_NODE_TYPE = "blocked_node_type"
    NON_SERIALIZABLE_CONFIG = "non_serializable_config"
    EXTERNAL_AGENT_NODE = "external_agent_node"
    UNTRUSTED_NODE_TYPE = "untrusted_node_type"
    EXECUTABLE_CONFIG = "executable_config"
    INVALID_EDGE = "invalid_edge"


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
    blocked node types (external agents and code nodes) are rejected.
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

        node_ids = {node.id for node in graph.nodes}
        for node in graph.nodes:
            self._validate_node(node.type, node.id, result)
            self._validate_config(node.config, node.id, node.type, result)

        self._validate_edges(graph, node_ids, result)

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

        if node_type not in PRODUCTION_TRUSTED_NODE_TYPES:
            result.add_violation(
                PolicyViolation(
                    reason=PolicyRejectionReason.UNTRUSTED_NODE_TYPE,
                    detail=(
                        f"Node type '{node_type}' is not in the production trusted "
                        "node allowlist."
                    ),
                    node_id=node_id,
                    node_type=node_type,
                )
            )
            return

        if (
            registry.get_node(node_type) is None
            and node_type not in _UNREGISTERED_ABSTRACT_TRUSTED_NODE_TYPES
        ):
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

    def _validate_config(
        self,
        config: Mapping[str, Any],
        node_id: str,
        node_type: str,
        result: PolicyResult,
    ) -> None:
        """Reject non-serializable or executable-looking node configuration."""
        try:
            json.dumps(config)
        except (TypeError, ValueError):
            result.add_violation(
                PolicyViolation(
                    reason=PolicyRejectionReason.NON_SERIALIZABLE_CONFIG,
                    detail=f"Node '{node_id}' config must be JSON serializable.",
                    node_id=node_id,
                    node_type=node_type,
                )
            )
            return

        for path in _find_executable_config_paths(config):
            result.add_violation(
                PolicyViolation(
                    reason=PolicyRejectionReason.EXECUTABLE_CONFIG,
                    detail=(
                        f"Node '{node_id}' config contains executable-looking "
                        f"field '{path}', which is not permitted in production mode."
                    ),
                    node_id=node_id,
                    node_type=node_type,
                )
            )

    def _validate_edges(
        self,
        graph: DeclarativeWorkflowGraph,
        node_ids: set[str],
        result: PolicyResult,
    ) -> None:
        """Validate that edges only reference declared nodes or graph sentinels."""
        valid_sources = node_ids | {"START"}
        valid_targets = node_ids | {"END"}

        for edge in graph.edges:
            if edge.source not in valid_sources or edge.target not in valid_targets:
                result.add_violation(
                    PolicyViolation(
                        reason=PolicyRejectionReason.INVALID_EDGE,
                        detail=(
                            f"Edge '{edge.source}' -> '{edge.target}' references "
                            "an undeclared node."
                        ),
                    )
                )

        for edge in graph.conditional_edges:
            if edge.source not in node_ids:
                result.add_violation(
                    PolicyViolation(
                        reason=PolicyRejectionReason.INVALID_EDGE,
                        detail=(
                            f"Conditional edge source '{edge.source}' references "
                            "an undeclared node."
                        ),
                    )
                )
            for label, target in edge.mapping.items():
                if target not in valid_targets:
                    result.add_violation(
                        PolicyViolation(
                            reason=PolicyRejectionReason.INVALID_EDGE,
                            detail=(
                                f"Conditional edge branch '{label}' from "
                                f"'{edge.source}' targets undeclared node '{target}'."
                            ),
                        )
                    )
            if edge.default is not None and edge.default not in valid_targets:
                result.add_violation(
                    PolicyViolation(
                        reason=PolicyRejectionReason.INVALID_EDGE,
                        detail=(
                            f"Conditional edge default from '{edge.source}' targets "
                            f"undeclared node '{edge.default}'."
                        ),
                    )
                )


_default_policy = TrustedWorkflowPolicy()


def is_production_trusted_node_type(node_type: str) -> bool:
    """Return whether a node type is trusted for in-process production use."""
    return (
        node_type in PRODUCTION_TRUSTED_NODE_TYPES
        and node_type not in _BLOCKED_NODE_TYPES
    )


def validate_production_node_types(node_types: Iterable[str]) -> list[str]:
    """Return sorted node types that fall outside the production trusted set."""
    return sorted(
        {
            node_type
            for node_type in node_types
            if not is_production_trusted_node_type(node_type)
        }
    )


def _find_executable_config_paths(value: Any, prefix: str = "config") -> Iterable[str]:
    """Yield paths with keys reserved for executable tenant code."""
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}"
            if key_text.lower() in _EXECUTABLE_CONFIG_KEYS:
                yield path
            yield from _find_executable_config_paths(nested_value, path)
    elif isinstance(value, list):
        for index, nested_value in enumerate(value):
            yield from _find_executable_config_paths(nested_value, f"{prefix}[{index}]")


def get_trusted_workflow_policy() -> TrustedWorkflowPolicy:
    """Return the default trusted workflow policy instance."""
    return _default_policy


__all__ = [
    "DECLARATIVE_FORMAT",
    "PRODUCTION_TRUSTED_NODE_TYPES",
    "PolicyRejectionReason",
    "PolicyResult",
    "PolicyViolation",
    "TrustedWorkflowPolicy",
    "get_trusted_workflow_policy",
    "is_production_trusted_node_type",
    "validate_production_node_types",
]
