"""Tests covering ingestion size limits, timeouts, and caching."""

from __future__ import annotations
import sys
import textwrap
from pathlib import Path
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
        ingest_langgraph_script(script, execution_timeout_seconds=0.1)


def test_compile_langgraph_script_is_cached() -> None:
    script = textwrap.dedent(
        """
        from langgraph.graph import StateGraph
        from orcheo.graph.state import State

        graph = StateGraph(State)
        graph.set_entry_point("first")
        graph.set_finish_point("first")
        """
    )

    _compile_langgraph_script.cache_clear()
    try:
        result1 = _compile_langgraph_script(script)
        result2 = _compile_langgraph_script(script)
        assert result1 is result2
    finally:
        _compile_langgraph_script.cache_clear()


def test_ingest_loads_plugin_site_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    site_packages = (
        tmp_path
        / "plugins"
        / "venv"
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )
    module_dir = site_packages / "orcheo_plugin_fixture_runtime"
    module_dir.mkdir(parents=True)
    (module_dir / "__init__.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
    monkeypatch.setenv("ORCHEO_PLUGIN_DIR", str(tmp_path / "plugins"))

    # Ensure the path insertion in _ensure_plugin_sys_path is triggered
    from orcheo.graph.ingestion.loader import _ensure_plugin_sys_path

    before = list(sys.path)
    try:
        _ensure_plugin_sys_path()
        assert str(site_packages) in sys.path
    finally:
        sys.path[:] = before
        sys.modules.pop("orcheo_plugin_fixture_runtime", None)
