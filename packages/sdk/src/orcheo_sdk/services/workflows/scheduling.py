"""Workflow cron scheduling helpers."""

from __future__ import annotations
from collections.abc import Mapping
from typing import Any
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.cron_extraction import (
    CronExtractionError,
    extract_version_cron_config,
)
from orcheo_sdk.cli.errors import APICallError, CLIError
from orcheo_sdk.cli.http import ApiClient
from orcheo_sdk.services.workflows.versions import get_latest_workflow_version_data


def _version_cron_config(version: Mapping[str, Any]) -> CronTriggerConfig | None:
    """Return the version's cron config, surfacing problems as CLI errors."""
    try:
        return extract_version_cron_config(version)
    except CronExtractionError as exc:
        raise CLIError(str(exc)) from exc


def schedule_workflow_cron(
    client: ApiClient,
    workflow_id: str,
) -> dict[str, Any]:
    """Configure cron scheduling for the workflow based on its latest version."""
    version = get_latest_workflow_version_data(client, workflow_id)
    if not isinstance(version.get("graph"), Mapping):
        raise CLIError("Latest workflow version is missing graph data.")

    cron_config = _version_cron_config(version)
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
    if not isinstance(version.get("graph"), Mapping):
        return {"status": "noop", "reason": "no_graph"}
    new_config = _version_cron_config(version)
    if new_config is None:
        return {"status": "noop", "reason": "no_cron_trigger"}

    existing_config = CronTriggerConfig.model_validate(existing)
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


__all__ = [
    "schedule_workflow_cron",
    "sync_cron_schedule_if_changed",
    "unschedule_workflow_cron",
]
