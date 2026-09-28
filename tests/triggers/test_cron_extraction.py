"""Tests for deriving cron trigger configs from workflow versions."""

from __future__ import annotations
from datetime import UTC, datetime
from typing import Any
import pytest
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.cron_extraction import (
    CronExtractionError,
    extract_cron_config,
    extract_version_cron_config,
)


def _index_graph(*entries: Any) -> dict[str, Any]:
    return {"format": "langgraph-script", "index": {"cron": list(entries)}}


def test_extracts_config_from_graph_index() -> None:
    graph = _index_graph(
        {"expression": "*/5 * * * *", "timezone": "UTC", "allow_overlapping": True}
    )

    config = extract_cron_config(graph)

    assert config == CronTriggerConfig(
        expression="*/5 * * * *", timezone="UTC", allow_overlapping=True
    )


def test_returns_none_without_cron_declaration() -> None:
    assert extract_cron_config({"format": "langgraph-script", "index": {}}) is None
    assert extract_cron_config(_index_graph("not-a-mapping")) is None
    assert extract_cron_config({"nodes": "not-a-list"}) is None


def test_rejects_multiple_cron_triggers() -> None:
    graph = _index_graph({"expression": "0 * * * *"}, {"expression": "5 * * * *"})

    with pytest.raises(CronExtractionError, match="multiple cron triggers"):
        extract_cron_config(graph)


def test_falls_back_to_script_summary_nodes() -> None:
    graph = {
        "format": "langgraph-script",
        "summary": {
            "nodes": [
                {"type": "PythonCode"},
                "ignored",
                {
                    "type": "CronTriggerNode",
                    "expression": "0 9 * * 1",
                    "timezone": " ",
                },
            ]
        },
    }

    config = extract_cron_config(graph)

    assert config is not None
    assert config.expression == "0 9 * * 1"
    # Blank strings fall back to the model defaults.
    assert config.timezone == CronTriggerConfig().timezone


def test_script_without_summary_has_no_cron() -> None:
    assert extract_cron_config({"format": "langgraph_script"}) is None


def test_reads_top_level_nodes_for_non_script_graphs() -> None:
    graph = {"nodes": [{"type": "CronTriggerNode", "expression": "0 0 * * *"}]}

    config = extract_cron_config(graph)

    assert config is not None
    assert config.expression == "0 0 * * *"


def test_resolves_configurable_placeholders_from_version() -> None:
    version = {
        "graph": _index_graph(
            {"expression": "{{ config.configurable.schedule }}", "timezone": "UTC"}
        ),
        "runnable_config": {"configurable": {"schedule": "15 * * * *"}},
    }

    config = extract_version_cron_config(version)

    assert config is not None
    assert config.expression == "15 * * * *"


@pytest.mark.parametrize(
    "runnable_config",
    [None, {"configurable": "invalid"}, {"configurable": {"other": "x"}}],
)
def test_unresolved_placeholder_is_rejected(runnable_config: Any) -> None:
    version = {
        "graph": _index_graph({"expression": "{{config.configurable.schedule}}"}),
        "runnable_config": runnable_config,
    }

    with pytest.raises(CronExtractionError, match="'schedule'"):
        extract_version_cron_config(version)


def test_version_without_graph_is_rejected() -> None:
    with pytest.raises(CronExtractionError, match="missing graph"):
        extract_version_cron_config({"graph": None})


def test_copies_optional_window_fields() -> None:
    start_at = datetime(2025, 1, 1, tzinfo=UTC)
    end_at = datetime(2025, 1, 2, tzinfo=UTC)
    graph = {
        "nodes": [
            {
                "type": "CronTriggerNode",
                "expression": "0 1 * * *",
                "timezone": "America/New_York",
                "allow_overlapping": True,
                "start_at": start_at,
                "end_at": end_at,
            }
        ]
    }

    config = extract_cron_config(graph)

    assert config == CronTriggerConfig(
        expression="0 1 * * *",
        timezone="America/New_York",
        allow_overlapping=True,
        start_at=start_at,
        end_at=end_at,
    )


def test_script_summary_with_invalid_nodes_has_no_cron() -> None:
    graph = {"format": "langgraph-script", "summary": {"nodes": "invalid"}}

    assert extract_cron_config(graph) is None
