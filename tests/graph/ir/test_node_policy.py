"""Tests for the restricted-mode node capability policy."""

from __future__ import annotations
import textwrap
from collections.abc import Generator
import pytest
from dynaconf import Dynaconf
import orcheo.nodes  # noqa: F401  (populate the node registry)
from orcheo.config import loader as config_loader
from orcheo.graph.ingestion import ingest_workflow
from orcheo.graph.ir.exceptions import WorkflowValidationError
from orcheo.graph.ir.interpreter import compile_workflow_to_ir
from orcheo.graph.ir.node_policy import (
    check_node_type_allowed,
    restricted_mode_rejection_reason,
)


def _workflow_with(node_ctor: str, imports: str) -> str:
    """Return a minimal single-node workflow source using ``node_ctor``."""
    return textwrap.dedent(
        f"""
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        {imports}

        async def orcheo_workflow() -> StateGraph:
            graph = StateGraph(State)
            graph.add_node("n", {node_ctor})
            graph.add_edge(START, "n")
            graph.add_edge("n", END)
            return graph
        """
    )


# --- policy function unit tests -------------------------------------------------


def test_postgres_node_blocked_by_metadata() -> None:
    """PostgresNode opts into restricted-mode rejection through its metadata."""
    reason = restricted_mode_rejection_reason("PostgresNode")
    assert reason is not None and "marked restricted" in reason


def test_browser_nodes_blocked_by_metadata() -> None:
    """Browser nodes opt into restricted-mode rejection through metadata."""
    assert restricted_mode_rejection_reason("BrowserNavigateNode") is not None
    assert restricted_mode_rejection_reason("BrowserScriptNode") is not None


@pytest.mark.parametrize(
    "node_type",
    [
        "DatasetNode",
        "DocumentLoaderNode",
        "MultiDoc2DialCorpusLoaderNode",
        "MultiDoc2DialDatasetNode",
        "QReCCDatasetNode",
    ],
)
def test_file_backed_loaders_blocked(node_type: str) -> None:
    """File-backed loaders cannot read author-selected worker paths."""
    reason = restricted_mode_rejection_reason(node_type)
    assert reason is not None and "marked restricted" in reason


def test_benign_nodes_allowed() -> None:
    """Ordinary nodes are not blocked."""
    assert restricted_mode_rejection_reason("SetVariableNode") is None
    assert restricted_mode_rejection_reason("AgentNode") is None
    assert restricted_mode_rejection_reason("HttpRequestNode") is None
    assert restricted_mode_rejection_reason("MongoDBFindNode") is None
    assert restricted_mode_rejection_reason("EmailNode") is None
    assert restricted_mode_rejection_reason("DiscordWebhookNode") is None


def test_unknown_node_type_is_not_blocked_by_policy() -> None:
    """Unknown types are not the policy's concern (registry check handles them)."""
    assert restricted_mode_rejection_reason("NotARealNode") is None


def test_check_node_type_allowed_carries_lineno() -> None:
    """The raised validation error surfaces the provided source line."""
    with pytest.raises(WorkflowValidationError) as exc_info:
        check_node_type_allowed("PostgresNode", "db", lineno=7)
    assert exc_info.value.lineno == 7
    assert str(exc_info.value).startswith("line 7:")


# --- interpreter-level enforcement ---------------------------------------------


def test_compile_blocks_disallowed_node_by_default() -> None:
    """compile_workflow_to_ir enforces the node policy by default."""
    source = _workflow_with(
        'PostgresNode(name="n", query="SELECT 1")',
        "from orcheo.nodes.storage import PostgresNode",
    )
    with pytest.raises(WorkflowValidationError, match="PostgresNode"):
        compile_workflow_to_ir(source)


@pytest.mark.parametrize(
    ("node_ctor", "imports"),
    [
        (
            'BrowserNavigateNode(name="n", url="https://example.com")',
            "from orcheo.nodes.browser import BrowserNavigateNode",
        ),
        (
            'DocumentLoaderNode(name="n")',
            "from orcheo.nodes.rag.ingestion import DocumentLoaderNode",
        ),
        (
            'MultiDoc2DialCorpusLoaderNode(name="n")',
            (
                "from orcheo.nodes.evaluation.datasets import "
                "MultiDoc2DialCorpusLoaderNode"
            ),
        ),
    ],
)
def test_compile_blocks_reviewed_unsafe_nodes(node_ctor: str, imports: str) -> None:
    """Compilation rejects nodes whose registry metadata marks them restricted."""
    with pytest.raises(WorkflowValidationError, match="restricted mode"):
        compile_workflow_to_ir(_workflow_with(node_ctor, imports))


@pytest.mark.parametrize(
    ("node_ctor", "imports"),
    [
        (
            'MongoDBFindNode(name="n", database="db", collection="items")',
            "from orcheo.nodes.storage.mongodb import MongoDBFindNode",
        ),
        (
            'DiscordWebhookNode(name="n", webhook_url="https://discord.com")',
            "from orcheo.nodes.communication import DiscordWebhookNode",
        ),
        (
            'EmailNode(name="n", from_address="sender@example.com")',
            "from orcheo.nodes.communication import EmailNode",
        ),
    ],
)
def test_compile_allows_guarded_or_workspace_scoped_nodes(
    node_ctor: str, imports: str
) -> None:
    """MongoDB and guarded communication nodes remain available."""
    ir = compile_workflow_to_ir(_workflow_with(node_ctor, imports))
    assert [node.id for node in ir.nodes] == ["n"]


def test_compile_allows_disallowed_node_when_policy_disabled() -> None:
    """A trusted caller can bypass the policy with enforce_node_policy=False."""
    source = _workflow_with(
        'BrowserNavigateNode(name="n", url="http://example.com")',
        "from orcheo.nodes.browser import BrowserNavigateNode",
    )
    ir = compile_workflow_to_ir(source, enforce_node_policy=False)
    assert [n.id for n in ir.nodes] == ["n"]


def test_compile_blocks_disallowed_node_in_nested_subgraph() -> None:
    """The policy also covers nodes inside nested subgraphs."""
    source = textwrap.dedent(
        """
        from orcheo.graph import StateGraph, START, END
        from orcheo.graph.state import State
        from orcheo.nodes.browser import BrowserNavigateNode
        from orcheo.nodes.logic import SetVariableNode

        async def orcheo_workflow() -> StateGraph:
            child = StateGraph(State)
            child.add_node(
                "browser",
                BrowserNavigateNode(name="browser", url="https://example.com"),
            )
            child.add_edge(START, "browser")
            child.add_edge("browser", END)

            graph = StateGraph(State)
            graph.add_node("setter", SetVariableNode(name="setter", variables={}))
            graph.add_node("sub", child)
            graph.add_edge(START, "setter")
            graph.add_edge("setter", "sub")
            graph.add_edge("sub", END)
            return graph
        """
    )
    with pytest.raises(WorkflowValidationError, match="BrowserNavigateNode"):
        compile_workflow_to_ir(source)


# --- restricted-mode ingestion integration -------------------------------------


def _no_dotenv_loader() -> Dynaconf:
    return Dynaconf(
        envvar_prefix="ORCHEO",
        settings_files=[],
        load_dotenv=False,
        environments=False,
    )


@pytest.fixture()
def restricted_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Activate restricted definition mode with settings isolated from .env."""
    monkeypatch.setattr(config_loader, "_build_loader", _no_dotenv_loader)
    monkeypatch.setenv("ORCHEO_WORKFLOW_DEFINITION_MODE", "restricted")
    config_loader.get_settings(refresh=True)
    yield
    monkeypatch.delenv("ORCHEO_WORKFLOW_DEFINITION_MODE", raising=False)
    config_loader.get_settings(refresh=True)


def test_untrusted_upload_with_blocked_node_is_rejected(restricted_mode: None) -> None:
    """An untrusted client upload using a blocked node fails ingestion."""
    source = _workflow_with(
        'PostgresNode(name="n", query="SELECT 1")',
        "from orcheo.nodes.storage import PostgresNode",
    )
    with pytest.raises(WorkflowValidationError, match="restricted mode"):
        ingest_workflow(source)


def test_trusted_source_bypasses_node_policy(restricted_mode: None) -> None:
    """A trusted first-party source (candidate) may use the full node set."""
    source = _workflow_with(
        'BrowserNavigateNode(name="n", url="https://example.com")',
        "from orcheo.nodes.browser import BrowserNavigateNode",
    )
    payload = ingest_workflow(source, trusted_source=True)
    assert payload["format"] == "frozen-ir"
    assert [n["id"] for n in payload["ir"]["nodes"]] == ["n"]
