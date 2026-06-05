"""Tests for script-only graph construction."""

from __future__ import annotations
from unittest.mock import sentinel
import pytest
from orcheo.graph import builder


def test_build_graph_rejects_legacy_json_payload() -> None:
    """Legacy JSON graph payloads are rejected with guidance."""

    with pytest.raises(
        builder.UnsupportedWorkflowGraphFormatError,
        match="unknown",
    ):
        builder.build_graph({"nodes": [{"name": "foo", "type": "missing"}]})


def test_build_graph_rejects_unknown_format() -> None:
    """Unknown graph formats are rejected with explicit format label."""

    with pytest.raises(
        builder.UnsupportedWorkflowGraphFormatError,
        match="unsupported-format",
    ):
        builder.build_graph({"format": "unsupported-format"})


def test_build_graph_rejects_missing_format_as_unknown() -> None:
    """Missing format and graph keys are classified as unknown."""

    with pytest.raises(
        builder.UnsupportedWorkflowGraphFormatError,
        match="unknown",
    ):
        builder.build_graph({})


def test_build_graph_script_format_empty_source() -> None:
    """Script format with empty source raises ValueError."""

    with pytest.raises(ValueError, match="non-empty source"):
        builder.build_graph({"format": "langgraph-script", "source": ""})

    with pytest.raises(ValueError, match="non-empty source"):
        builder.build_graph({"format": "langgraph-script", "source": "   "})


def test_build_graph_script_format_invalid_entrypoint_type() -> None:
    """Script format with non-string entrypoint raises ValueError."""

    with pytest.raises(ValueError, match="Entrypoint must be a string"):
        builder.build_graph(
            {"format": "langgraph-script", "source": "valid_code", "entrypoint": 123}
        )


def test_build_graph_script_format_delegates_to_ingestion_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Script payloads are delegated to ``load_graph_from_script``."""

    captured: dict[str, object] = {}

    def fake_loader(
        source: str,
        *,
        entrypoint: str | None = None,
        max_script_bytes: int | None = None,
    ):
        captured["source"] = source
        captured["entrypoint"] = entrypoint
        return sentinel.graph

    monkeypatch.setattr(builder, "load_graph_from_script", fake_loader)
    result = builder.build_graph(
        {
            "format": "langgraph-script",
            "source": "from langgraph.graph import StateGraph",
            "entrypoint": "build_graph",
        }
    )

    assert result is sentinel.graph
    assert captured == {
        "source": "from langgraph.graph import StateGraph",
        "entrypoint": "build_graph",
    }
