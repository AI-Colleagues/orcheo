"""Integration tests for the definition-mode toggle and wiring (Task 4.4)."""

from __future__ import annotations
import textwrap
from collections.abc import Generator
import pytest
from dynaconf import Dynaconf
from orcheo.config import loader as config_loader
from orcheo.graph.builder import (
    UnsupportedWorkflowGraphFormatError,
    build_graph,
)
from orcheo.graph.ingestion import ingest_langgraph_script, ingest_workflow
from orcheo.graph.ir.definition_mode import get_definition_mode, is_restricted_mode


WORKFLOW = textwrap.dedent(
    """
    from orcheo.graph import StateGraph, START, END
    from orcheo.graph.state import State
    from orcheo.nodes.logic import SetVariableNode
    from orcheo.nodes import CodeNode

    class Doubler(CodeNode):
        factor: int = 2

        async def run(self, state, config):
            value = state["results"]["setter"]["value"]
            return {"results": {"doubled": value * self.factor}}

    async def orcheo_workflow() -> StateGraph:
        graph = StateGraph(State)
        graph.add_node("setter", SetVariableNode(name="setter", variables={"value": 10}))
        graph.add_node("doubler", Doubler(name="doubler", factor=5))
        graph.add_edge(START, "setter")
        graph.add_edge("setter", "doubler")
        graph.add_edge("doubler", END)
        return graph
    """
)


def _no_dotenv_loader() -> Dynaconf:
    """Build a Dynaconf loader that ignores .env files for deterministic tests."""
    return Dynaconf(
        envvar_prefix="ORCHEO", settings_files=[], load_dotenv=False, environments=False
    )


@pytest.fixture()
def isolated_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Isolate definition-mode settings from .env and refresh after the test."""
    monkeypatch.setattr(config_loader, "_build_loader", _no_dotenv_loader)
    monkeypatch.delenv("ORCHEO_WORKFLOW_DEFINITION_MODE", raising=False)
    yield
    monkeypatch.delenv("ORCHEO_WORKFLOW_DEFINITION_MODE", raising=False)
    config_loader.get_settings(refresh=True)


def _set_mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """Set the definition mode env var and refresh the cached settings."""
    monkeypatch.setenv("ORCHEO_WORKFLOW_DEFINITION_MODE", mode)
    config_loader.get_settings(refresh=True)


def test_default_mode_is_unrestricted(isolated_settings: None) -> None:
    """With no override the definition mode defaults to unrestricted."""
    config_loader.get_settings(refresh=True)

    assert get_definition_mode() == "unrestricted"
    assert is_restricted_mode() is False


def test_restricted_mode_is_opt_in(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """Setting the env var to ``restricted`` enables restricted mode."""
    _set_mode(monkeypatch, "restricted")

    assert is_restricted_mode() is True


def test_invalid_mode_is_rejected(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """An invalid mode value raises a helpful error."""
    monkeypatch.setenv("ORCHEO_WORKFLOW_DEFINITION_MODE", "bananas")

    with pytest.raises(ValueError, match="WORKFLOW_DEFINITION_MODE"):
        config_loader.get_settings(refresh=True)


def test_restricted_ingest_produces_frozen_ir(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """Restricted ingestion compiles to a stored frozen-IR payload."""
    _set_mode(monkeypatch, "restricted")

    payload = ingest_workflow(WORKFLOW)

    assert payload["format"] == "frozen-ir"
    assert "source" not in payload
    assert [n["id"] for n in payload["ir"]["nodes"]] == ["setter", "doubler"]


@pytest.mark.asyncio
async def test_restricted_ingest_store_run_roundtrip(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """Restricted ingest -> store -> run executes built-in and CodeNode nodes."""
    _set_mode(monkeypatch, "restricted")

    payload = ingest_workflow(WORKFLOW)
    compiled = build_graph(payload).compile()
    result = await compiled.ainvoke({"inputs": {}})

    assert result["results"]["setter"] == {"value": 10}
    assert result["results"]["doubled"] == 50


def test_restricted_ingest_payload_renders_mermaid(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """A restricted-mode frozen-IR payload renders a Mermaid diagram on demand."""
    from orcheo.workflow.mermaid import render_mermaid_from_graph_payload

    _set_mode(monkeypatch, "restricted")

    payload = ingest_workflow(WORKFLOW)
    mermaid = render_mermaid_from_graph_payload(payload)

    assert mermaid is not None
    assert "setter" in mermaid
    assert "doubler" in mermaid


def test_unrestricted_ingest_matches_script_path(
    monkeypatch: pytest.MonkeyPatch, isolated_settings: None
) -> None:
    """Unrestricted ingestion preserves today's langgraph-script payload."""
    _set_mode(monkeypatch, "unrestricted")

    payload = ingest_workflow(WORKFLOW)
    reference = ingest_langgraph_script(WORKFLOW)

    assert payload["format"] == "langgraph-script"
    assert payload["source"] == reference["source"] == WORKFLOW


def test_build_graph_rejects_unknown_format_lists_both() -> None:
    """The unsupported-format error mentions both executable formats."""
    with pytest.raises(UnsupportedWorkflowGraphFormatError, match="frozen-ir"):
        build_graph({"format": "mystery"})


def test_build_graph_frozen_ir_requires_ir_mapping() -> None:
    """A frozen-IR payload without an 'ir' mapping is rejected."""
    with pytest.raises(ValueError, match="'ir' mapping"):
        build_graph({"format": "frozen-ir"})
