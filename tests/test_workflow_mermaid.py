"""Tests for workflow Mermaid rendering helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from orcheo.workflow.mermaid import (
    _render_mermaid_from_script,
    render_mermaid_from_graph_payload,
)


def test_render_mermaid_from_graph_payload_rejects_blank_source() -> None:
    """Blank script sources should be rejected before any rendering work."""
    payload = {"format": "langgraph-script", "source": "   "}

    assert render_mermaid_from_graph_payload(payload) is None


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
