"""Tests covering ingestion size limits and timeouts."""

from __future__ import annotations
import pytest
from orcheo.graph.ingestion import (
    DEFAULT_SCRIPT_SIZE_LIMIT,
    ScriptIngestionError,
    ingest_langgraph_script,
)
from orcheo.graph.ingestion.sandbox import validate_script_size as _validate_script_size
from orcheo.graph.ingestion.loader import load_graph_from_script


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


def test_validate_script_size_accepts_equal_limit() -> None:
    source = "hello"
    _validate_script_size(source, max_script_bytes=len(source.encode("utf-8")))


def test_validate_script_size_rejects_payload_above_limit() -> None:
    with pytest.raises(
        ScriptIngestionError,
        match="LangGraph script exceeds the permitted size of 1 bytes",
    ):
        _validate_script_size("ab", max_script_bytes=1)


def test_ingest_script_enforces_execution_timeout() -> None:
    script = "while True:\n    pass\n"

    with pytest.raises(
        ScriptIngestionError, match="execution exceeded the configured timeout"
    ):
        load_graph_from_script(script, execution_timeout_seconds=0.1)
