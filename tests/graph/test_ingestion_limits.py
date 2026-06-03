"""Tests covering ingestion size limits, timeouts, and caching."""

from __future__ import annotations
import textwrap
import pytest
from orcheo.graph.ingestion import (
    DEFAULT_SCRIPT_SIZE_LIMIT,
    ScriptIngestionError,
    _compile_langgraph_script,
    _validate_script_size,
    ingest_langgraph_script,
)


def test_default_script_size_limit_is_512_kib() -> None:
    assert DEFAULT_SCRIPT_SIZE_LIMIT == 512 * 1024


def test_ingest_script_exceeding_size_limit() -> None:
    oversized = "a" * (DEFAULT_SCRIPT_SIZE_LIMIT + 1)

    with pytest.raises(ScriptIngestionError, match="exceeds the permitted size"):
        ingest_langgraph_script(oversized)


def test_validate_script_size_without_limit() -> None:
    assert _validate_script_size("payload", None) is None


def test_validate_script_size_rejects_non_positive_limits() -> None:
    with pytest.raises(ScriptIngestionError, match="must be a positive integer"):
        _validate_script_size("payload", 0)


def test_ingest_script_enforces_execution_timeout() -> None:
    script = "while True:\n    pass\n"

    with pytest.raises(
        ScriptIngestionError, match="execution exceeded the configured timeout"
    ):
        ingest_langgraph_script(script, execution_timeout_seconds=0.1)


def test_compile_langgraph_script_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        graph = StateGraph(State)
        graph.set_entry_point("first")
        graph.set_finish_point("first")
        """
    )

    call_count = 0

    def _fake_compile(source: str, filename: str, mode: str, **kwargs):
        nonlocal call_count
        call_count += 1
        # Extract flags if provided, pass to compile
        flags = kwargs.get("flags", 0)
        return compile(source, filename, mode, flags=flags)

    _compile_langgraph_script.cache_clear()
    monkeypatch.setattr("orcheo.graph.ingestion.compile_restricted", _fake_compile)

    try:
        ingest_langgraph_script(script)
        ingest_langgraph_script(script)
    finally:
        _compile_langgraph_script.cache_clear()

    assert call_count == 1
