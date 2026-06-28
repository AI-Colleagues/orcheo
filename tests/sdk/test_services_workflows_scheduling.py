"""Workflow cron scheduling service tests."""

from __future__ import annotations
from datetime import UTC, datetime
from types import SimpleNamespace
import pytest
from orcheo.graph.ingestion.config import LANGGRAPH_SCRIPT_FORMAT
from orcheo.triggers.cron import CronTriggerConfig
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.services.workflows import scheduling


def test_schedule_workflow_cron_noop_without_cron_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling is a no-op when the workflow has no cron trigger."""

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": {"nodes": []}},
    )

    result = scheduling.schedule_workflow_cron(
        SimpleNamespace(),
        workflow_id="wf-123",
    )

    assert result["status"] == "noop"


def test_schedule_workflow_cron_configures_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling uses the cron trigger configuration from the workflow."""

    graph = {
        "format": "langgraph_script",
        "summary": {
            "nodes": [
                {
                    "name": "cron_trigger",
                    "type": "CronTriggerNode",
                    "expression": "*/5 * * * *",
                    "timezone": "UTC",
                    "allow_overlapping": False,
                    "start_at": None,
                    "end_at": None,
                }
            ]
        },
    }

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": graph},
    )

    captured: dict[str, object] = {}

    def fake_put(
        path: str, *, json_body: dict[str, object] | None = None
    ) -> dict[str, object]:
        captured["path"] = path
        captured["json_body"] = json_body
        return json_body or {}

    client = SimpleNamespace(put=fake_put)

    result = scheduling.schedule_workflow_cron(client, workflow_id="wf-123")

    assert result["status"] == "scheduled"
    assert captured["path"] == "/api/workflows/wf-123/triggers/cron/config"
    assert captured["json_body"]["expression"] == "*/5 * * * *"


def test_schedule_workflow_cron_uses_index_cron_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling prefers graph.index.cron for langgraph payloads."""

    graph = {
        "format": "langgraph-script",
        "index": {
            "cron": [
                {
                    "expression": "0 * * * *",
                    "timezone": "UTC",
                    "allow_overlapping": False,
                    "start_at": None,
                    "end_at": None,
                }
            ]
        },
        "summary": {"nodes": []},
    }

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": graph},
    )

    captured: dict[str, object] = {}

    def fake_put(
        path: str, *, json_body: dict[str, object] | None = None
    ) -> dict[str, object]:
        captured["path"] = path
        captured["json_body"] = json_body
        return json_body or {}

    client = SimpleNamespace(put=fake_put)

    result = scheduling.schedule_workflow_cron(client, workflow_id="wf-idx")

    assert result["status"] == "scheduled"
    assert captured["path"] == "/api/workflows/wf-idx/triggers/cron/config"
    assert captured["json_body"]["expression"] == "0 * * * *"


def test_schedule_workflow_cron_rejects_multiple_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling fails when multiple cron triggers are present."""

    graph = {
        "nodes": [
            {"name": "cron_1", "type": "CronTriggerNode", "expression": "* * * * *"},
            {"name": "cron_2", "type": "CronTriggerNode", "expression": "0 * * * *"},
        ]
    }

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": graph},
    )

    with pytest.raises(CLIError, match="multiple cron triggers"):
        scheduling.schedule_workflow_cron(SimpleNamespace(), workflow_id="wf-123")


def test_schedule_workflow_cron_rejects_multiple_index_triggers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling fails when multiple cron entries are present in graph.index."""
    graph = {
        "format": "langgraph-script",
        "index": {
            "cron": [
                {"expression": "*/5 * * * *", "timezone": "UTC"},
                {"expression": "0 * * * *", "timezone": "UTC"},
            ]
        },
    }

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": graph},
    )

    with pytest.raises(CLIError, match="multiple cron triggers"):
        scheduling.schedule_workflow_cron(SimpleNamespace(), workflow_id="wf-123")


def test_unschedule_workflow_cron_calls_delete() -> None:
    """Unscheduling calls the API delete endpoint."""

    captured: dict[str, object] = {}

    def fake_delete(path: str) -> None:
        captured["path"] = path

    client = SimpleNamespace(delete=fake_delete)

    result = scheduling.unschedule_workflow_cron(client, workflow_id="wf-123")

    assert result["status"] == "unscheduled"
    assert captured["path"] == "/api/workflows/wf-123/triggers/cron/config"


def test_schedule_workflow_cron_requires_graph_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scheduling fails when the latest workflow version lacks graph data."""

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": "not-a-map"},
    )

    with pytest.raises(CLIError, match="missing graph data"):
        scheduling.schedule_workflow_cron(SimpleNamespace(), workflow_id="wf-123")


def test_extract_cron_config_populates_optional_fields() -> None:
    """Cron trigger config copies optional fields when they are provided."""

    start_at = datetime(2025, 1, 1, 0, 0, tzinfo=UTC)
    end_at = datetime(2025, 1, 2, 0, 0, tzinfo=UTC)
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

    config = scheduling._extract_cron_config(graph)
    assert config is not None
    assert config.expression == "0 1 * * *"
    assert config.timezone == "America/New_York"
    assert config.allow_overlapping is True
    assert config.start_at == start_at
    assert config.end_at == end_at


def test_schedule_workflow_cron_resolves_configurable_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``{{config.configurable.X}}`` expression resolves to its config value."""

    version = {
        "graph": {
            "nodes": [
                {
                    "type": "CronTriggerNode",
                    "expression": "{{config.configurable.cron_expression}}",
                    "timezone": "{{config.configurable.cron_timezone}}",
                }
            ]
        },
        "runnable_config": {
            "configurable": {
                "cron_expression": "*/30 * * * *",
                "cron_timezone": "America/New_York",
            }
        },
    }

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: version,
    )

    captured: dict[str, object] = {}

    def fake_put(
        path: str, *, json_body: dict[str, object] | None = None
    ) -> dict[str, object]:
        captured["json_body"] = json_body
        return json_body or {}

    client = SimpleNamespace(put=fake_put)

    result = scheduling.schedule_workflow_cron(client, workflow_id="wf-tmpl")

    assert result["status"] == "scheduled"
    assert captured["json_body"]["expression"] == "*/30 * * * *"  # type: ignore[index]
    assert captured["json_body"]["timezone"] == "America/New_York"  # type: ignore[index]


def test_schedule_workflow_cron_raises_for_unresolvable_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unresolvable configurable placeholder raises a clear error."""

    version = {
        "graph": {
            "nodes": [
                {
                    "type": "CronTriggerNode",
                    "expression": "{{config.configurable.cron_expression}}",
                }
            ]
        },
        "runnable_config": {"configurable": {}},
    }

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: version,
    )

    with pytest.raises(CLIError, match="no configurable value named 'cron_expression'"):
        scheduling.schedule_workflow_cron(SimpleNamespace(), workflow_id="wf-tmpl")


def test_extract_nodes_returns_empty_for_summary_without_nodes() -> None:
    graph = {"format": "langgraph_script", "summary": {"nodes": "invalid"}}
    assert scheduling._extract_nodes(graph) == []


def test_extract_nodes_returns_empty_when_nodes_missing() -> None:
    graph = {"nodes": "not-a-list"}
    assert scheduling._extract_nodes(graph) == []


def test_extract_nodes_returns_summary_nodes_for_langgraph_format() -> None:
    graph = {
        "format": LANGGRAPH_SCRIPT_FORMAT,
        "summary": {"nodes": [{"id": "cron_node"}, 123]},
    }

    assert scheduling._extract_nodes(graph) == [{"id": "cron_node"}]


def test_extract_configurable_returns_empty_when_configurable_not_mapping() -> None:
    version = {"runnable_config": {"configurable": "not-a-map"}}
    assert scheduling._extract_configurable(version) == {}


def test_extract_cron_config_from_index_requires_list_entries() -> None:
    graph = {"index": {"cron": "not-a-list"}}

    assert scheduling._extract_cron_config_from_index(graph) is None


def test_extract_cron_config_from_index_ignores_non_mapping_entries() -> None:
    graph = {"index": {"cron": [1, "cron"]}}

    assert scheduling._extract_cron_config_from_index(graph) is None


def test_extract_cron_config_uses_defaults_when_expression_and_timezone_blank() -> None:
    default_config = CronTriggerConfig()
    graph = {
        "nodes": [
            {
                "type": "CronTriggerNode",
                "expression": "    ",
                "timezone": "",
                "allow_overlapping": False,
            }
        ]
    }

    config = scheduling._extract_cron_config(graph)
    assert config is not None
    assert config.expression == default_config.expression
    assert config.timezone == default_config.timezone
    assert config.allow_overlapping is False


# ---------------------------------------------------------------------------
# sync_cron_schedule_if_changed – lines 50-71
# ---------------------------------------------------------------------------


def test_sync_cron_schedule_noop_when_no_existing_schedule() -> None:
    """Returns noop when no cron schedule exists (404 on GET)."""

    class _Client:
        def get(self, _path: str) -> dict[str, object]:
            raise APICallError("Not Found", status_code=404)

    result = scheduling.sync_cron_schedule_if_changed(_Client(), "wf-123")
    assert result == {"status": "noop", "reason": "no_existing_schedule"}


def test_sync_cron_schedule_reraises_non_404_api_error() -> None:
    """Re-raises APICallError when the status code is not 404."""

    class _Client:
        def get(self, _path: str) -> dict[str, object]:
            raise APICallError("Server Error", status_code=500)

    with pytest.raises(APICallError, match="Server Error"):
        scheduling.sync_cron_schedule_if_changed(_Client(), "wf-123")


def test_sync_cron_schedule_noop_when_graph_not_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns noop with no_graph reason when graph data is not a Mapping."""

    class _Client:
        def get(self, _path: str) -> dict[str, object]:
            return {"expression": "* * * * *"}

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": "not-a-map"},
    )

    result = scheduling.sync_cron_schedule_if_changed(_Client(), "wf-123")
    assert result == {"status": "noop", "reason": "no_graph"}


def test_sync_cron_schedule_noop_when_no_cron_trigger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns noop with no_cron_trigger reason when workflow has no cron node."""

    class _Client:
        def get(self, _path: str) -> dict[str, object]:
            return {"expression": "* * * * *"}

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {"graph": {"nodes": []}},
    )

    result = scheduling.sync_cron_schedule_if_changed(_Client(), "wf-123")
    assert result == {"status": "noop", "reason": "no_cron_trigger"}


def test_sync_cron_schedule_noop_when_config_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns noop with unchanged reason when existing and new configs match."""

    class _Client:
        def get(self, _path: str) -> dict[str, object]:
            return {
                "expression": "0 * * * *",
                "timezone": "UTC",
                "allow_overlapping": False,
            }

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {
            "graph": {
                "nodes": [
                    {
                        "type": "CronTriggerNode",
                        "expression": "0 * * * *",
                        "timezone": "UTC",
                        "allow_overlapping": False,
                    }
                ]
            }
        },
    )

    result = scheduling.sync_cron_schedule_if_changed(_Client(), "wf-123")
    assert result == {"status": "noop", "reason": "unchanged"}


def test_sync_cron_schedule_updates_when_config_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUTs the new config and returns updated status when cron config differs."""
    captured: dict[str, object] = {}

    class _Client:
        def get(self, _path: str) -> dict[str, object]:
            return {
                "expression": "0 * * * *",
                "timezone": "UTC",
                "allow_overlapping": False,
            }

        def put(
            self, path: str, *, json_body: dict[str, object] | None = None
        ) -> dict[str, object]:
            captured["path"] = path
            captured["json_body"] = json_body
            return json_body or {}

    monkeypatch.setattr(
        scheduling,
        "get_latest_workflow_version_data",
        lambda *_args, **_kwargs: {
            "graph": {
                "nodes": [
                    {
                        "type": "CronTriggerNode",
                        "expression": "*/30 * * * *",
                        "timezone": "UTC",
                        "allow_overlapping": False,
                    }
                ]
            }
        },
    )

    result = scheduling.sync_cron_schedule_if_changed(_Client(), "wf-123")

    assert result["status"] == "updated"
    assert "wf-123" in result["message"]
    assert captured["path"] == "/api/workflows/wf-123/triggers/cron/config"
    assert captured["json_body"]["expression"] == "*/30 * * * *"  # type: ignore[index]
