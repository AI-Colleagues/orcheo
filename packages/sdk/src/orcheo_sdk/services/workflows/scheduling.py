"""Workflow cron scheduling helpers."""

from __future__ import annotations
import re
from collections.abc import Mapping
from typing import Any
from orcheo.graph.ingestion.config import LANGGRAPH_SCRIPT_FORMAT
from orcheo.triggers.cron import CronTriggerConfig
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.cli.http import ApiClient
from orcheo_sdk.services.workflows.versions import get_latest_workflow_version_data


# Matches a value that is exactly a single ``{{config.configurable.<name>}}``
# placeholder, optionally padded with whitespace inside the braces. Anchoring is
# handled by ``re.fullmatch`` at the call site, so no ``^``/``$`` here.
_CONFIGURABLE_TEMPLATE = re.compile(r"\{\{\s*config\.configurable\.([^}\s.]+)\s*\}\}")


def schedule_workflow_cron(
    client: ApiClient,
    workflow_id: str,
) -> dict[str, Any]:
    """Configure cron scheduling for the workflow based on its latest version."""
    version = get_latest_workflow_version_data(client, workflow_id)
    graph = version.get("graph")
    if not isinstance(graph, Mapping):
        raise CLIError("Latest workflow version is missing graph data.")

    cron_config = _extract_cron_config(graph, _extract_configurable(version))
    if cron_config is None:
        return {
            "status": "noop",
            "message": f"Workflow '{workflow_id}' has no cron trigger to schedule.",
        }

    payload = cron_config.model_dump(mode="json")
    response = client.put(
        f"/api/workflows/{workflow_id}/triggers/cron/config",
        json_body=payload,
    )
    return {
        "status": "scheduled",
        "message": f"Cron trigger scheduled for workflow '{workflow_id}'.",
        "config": response or payload,
    }


def sync_cron_schedule_if_changed(
    client: ApiClient,
    workflow_id: str,
) -> dict[str, Any]:
    """Update the cron schedule when one exists and the config changed."""
    try:
        existing = client.get(f"/api/workflows/{workflow_id}/triggers/cron/config")
    except APICallError as exc:
        if exc.status_code == 404:
            return {"status": "noop", "reason": "no_existing_schedule"}
        raise

    version = get_latest_workflow_version_data(client, workflow_id)
    graph = version.get("graph")
    if not isinstance(graph, Mapping):
        return {"status": "noop", "reason": "no_graph"}
    new_config = _extract_cron_config(graph, _extract_configurable(version))
    if new_config is None:
        return {"status": "noop", "reason": "no_cron_trigger"}

    existing_config = CronTriggerConfig(**existing)
    if new_config == existing_config:
        return {"status": "noop", "reason": "unchanged"}

    payload = new_config.model_dump(mode="json")
    response = client.put(
        f"/api/workflows/{workflow_id}/triggers/cron/config",
        json_body=payload,
    )
    return {
        "status": "updated",
        "message": f"Cron schedule updated for workflow '{workflow_id}'.",
        "config": response or payload,
    }


def unschedule_workflow_cron(
    client: ApiClient,
    workflow_id: str,
) -> dict[str, Any]:
    """Remove cron scheduling for the workflow."""
    client.delete(f"/api/workflows/{workflow_id}/triggers/cron/config")
    return {
        "status": "unscheduled",
        "message": f"Cron trigger unscheduled for workflow '{workflow_id}'.",
    }


def _extract_cron_config(
    graph: Mapping[str, Any],
    configurable: Mapping[str, Any] | None = None,
) -> CronTriggerConfig | None:
    """Return the cron trigger config if the workflow contains one."""
    index_config = _extract_cron_config_from_index(graph, configurable)
    if index_config is not None:
        return index_config

    nodes = _extract_nodes(graph)
    cron_nodes = [node for node in nodes if node.get("type") == "CronTriggerNode"]
    if not cron_nodes:
        return None
    if len(cron_nodes) > 1:
        raise CLIError("Workflow contains multiple cron triggers.")

    node = cron_nodes[0]
    config_payload: dict[str, Any] = {}
    expression = node.get("expression")
    if isinstance(expression, str) and expression.strip():
        config_payload["expression"] = expression
    timezone = node.get("timezone")
    if isinstance(timezone, str) and timezone.strip():
        config_payload["timezone"] = timezone
    if "allow_overlapping" in node:  # pragma: no branch
        config_payload["allow_overlapping"] = bool(node.get("allow_overlapping"))
    if "start_at" in node:
        config_payload["start_at"] = node.get("start_at")
    if "end_at" in node:
        config_payload["end_at"] = node.get("end_at")
    _resolve_configurable_templates(config_payload, configurable)
    return CronTriggerConfig(**config_payload)


def _extract_cron_config_from_index(
    graph: Mapping[str, Any],
    configurable: Mapping[str, Any] | None = None,
) -> CronTriggerConfig | None:
    """Return cron config from ``graph.index.cron`` when present."""
    index = graph.get("index")
    if not isinstance(index, Mapping):
        return None

    cron_entries = index.get("cron")
    if not isinstance(cron_entries, list):
        return None

    resolved = [entry for entry in cron_entries if isinstance(entry, Mapping)]
    if not resolved:
        return None
    if len(resolved) > 1:
        raise CLIError("Workflow contains multiple cron triggers.")

    entry = resolved[0]
    config_payload: dict[str, Any] = {}
    for key in (
        "expression",
        "timezone",
        "allow_overlapping",
        "start_at",
        "end_at",
    ):
        if key in entry:  # pragma: no branch
            config_payload[key] = entry.get(key)
    _resolve_configurable_templates(config_payload, configurable)
    return CronTriggerConfig(**config_payload)


def _extract_configurable(version: Mapping[str, Any]) -> dict[str, Any]:
    """Return the resolved ``configurable`` values for a workflow version."""
    runnable_config = version.get("runnable_config")
    if not isinstance(runnable_config, Mapping):
        return {}
    configurable = runnable_config.get("configurable")
    if not isinstance(configurable, Mapping):
        return {}
    return dict(configurable)


def _resolve_configurable_templates(
    config_payload: dict[str, Any],
    configurable: Mapping[str, Any] | None,
) -> None:
    """Replace ``{{config.configurable.X}}`` placeholders with their values.

    Cron triggers may parametrize fields (e.g. ``expression``) with template
    placeholders that the workflow runtime resolves from its ``configurable``
    config. Scheduling needs the concrete value, so resolve any such
    placeholder against the version's resolved configurable values before the
    payload is validated as a cron expression.
    """
    for key, value in list(config_payload.items()):
        if not isinstance(value, str):
            continue
        match = _CONFIGURABLE_TEMPLATE.fullmatch(value.strip())
        if match is None:
            continue
        name = match.group(1)
        if not configurable or name not in configurable:
            raise CLIError(
                f"Cron trigger references '{{{{config.configurable.{name}}}}}' "
                f"but the workflow has no configurable value named '{name}'. "
                "Set a default for it in the workflow config."
            )
        config_payload[key] = configurable[name]


def _extract_nodes(graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the serialized nodes list from a workflow graph payload."""
    graph_format = graph.get("format")
    if graph_format in {LANGGRAPH_SCRIPT_FORMAT, "langgraph_script"}:
        summary = graph.get("summary")
        if isinstance(summary, Mapping):  # pragma: no branch
            nodes = summary.get("nodes")
            if isinstance(nodes, list):
                return [node for node in nodes if isinstance(node, Mapping)]
        return []

    nodes = graph.get("nodes")
    if isinstance(nodes, list):
        return [node for node in nodes if isinstance(node, Mapping)]
    return []


__all__ = [
    "schedule_workflow_cron",
    "sync_cron_schedule_if_changed",
    "unschedule_workflow_cron",
]
