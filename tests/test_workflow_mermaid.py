"""Tests for workflow Mermaid rendering helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orcheo.workflow.mermaid import (
    _render_mermaid_from_script,
    _render_mermaid_from_script_full_env,
    render_mermaid_from_graph_payload,
    render_mermaid_from_graph_payload_full_env,
)


def test_render_mermaid_from_graph_payload_rejects_wrong_format() -> None:
    """Non-langgraph-script payloads return None immediately."""
    payload = {"format": "unknown", "source": "anything"}
    assert render_mermaid_from_graph_payload(payload) is None


def test_render_mermaid_from_graph_payload_rejects_blank_source() -> None:
    """Blank script sources should be rejected before any rendering work."""
    payload = {"format": "langgraph-script", "source": "   "}

    assert render_mermaid_from_graph_payload(payload) is None


def test_render_mermaid_from_graph_payload_renders_valid_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid payload is forwarded to the sandboxed renderer."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": []},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: False,
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary._render_compact_mermaid",
        lambda graph: "compact-mermaid",
    )

    payload = {"format": "langgraph-script", "source": "x = 1", "entrypoint": "build"}
    assert render_mermaid_from_graph_payload(payload) == "compact-mermaid"


def test_render_mermaid_from_script_returns_summary_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow-tool subgraphs should route through the summary renderer."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": [{"name": "agent", "type": "AgentNode"}]},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: True,
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.render_summary_mermaid",
        lambda summary: "summary-mermaid",
    )

    assert _render_mermaid_from_script("print('hello')") == "summary-mermaid"


def test_render_mermaid_from_script_returns_none_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions during sandboxed rendering are caught and None is returned."""

    def _failing_loader(source: str, entrypoint: str | None = None) -> None:
        raise RuntimeError("sandbox error")

    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script",
        _failing_loader,
    )

    assert _render_mermaid_from_script("bad script") is None


def test_render_mermaid_from_graph_payload_full_env_rejects_wrong_format() -> None:
    """Non-langgraph-script payloads return None without attempting execution."""
    payload = {"format": "unknown", "source": "anything"}
    assert render_mermaid_from_graph_payload_full_env(payload) is None


def test_render_mermaid_from_graph_payload_full_env_rejects_blank_source() -> None:
    """Blank sources return None without attempting execution."""
    payload = {"format": "langgraph-script", "source": "   "}
    assert render_mermaid_from_graph_payload_full_env(payload) is None


def test_render_mermaid_from_graph_payload_full_env_returns_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid payload renders mermaid using the full-env loader."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": []},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: False,
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary._render_compact_mermaid",
        lambda graph: "compact-mermaid",
    )

    payload = {"format": "langgraph-script", "source": "x = 1", "entrypoint": None}
    assert render_mermaid_from_graph_payload_full_env(payload) == "compact-mermaid"


def test_render_mermaid_from_script_full_env_returns_summary_mermaid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workflow-tool subgraphs route through the summary renderer in the full_env path."""
    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        lambda source, entrypoint=None: SimpleNamespace(
            source=source,
            entrypoint=entrypoint,
        ),
    )
    monkeypatch.setattr(
        "orcheo.graph.ingestion.summary.summarise_state_graph",
        lambda graph: {"nodes": [{"name": "agent", "type": "AgentNode"}]},
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.has_workflow_tool_subgraphs",
        lambda summary: True,
    )
    monkeypatch.setattr(
        "orcheo.graph.mermaid.render_summary_mermaid",
        lambda summary: "summary-mermaid-full-env",
    )

    assert (
        _render_mermaid_from_script_full_env("print('hello')")
        == "summary-mermaid-full-env"
    )


def test_render_mermaid_from_script_full_env_returns_none_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exceptions raised during full_env rendering are caught and None is returned."""

    def _failing_loader(source: str, entrypoint: str | None = None) -> None:
        raise RuntimeError("loader failed")

    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        _failing_loader,
    )

    assert _render_mermaid_from_script_full_env("raise_error()") is None


def test_render_mermaid_from_script_full_env_returns_none_on_script_ingestion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ScriptIngestionError during full_env rendering is caught and None is returned."""
    from orcheo.graph.ingestion.exceptions import ScriptIngestionError

    def _failing_loader(source: str, entrypoint: str | None = None) -> None:
        raise ScriptIngestionError("script failed to ingest")

    monkeypatch.setattr(
        "orcheo.graph.ingestion.loader.load_graph_from_script_full_env",
        _failing_loader,
    )

    assert _render_mermaid_from_script_full_env("bad_script()") is None
