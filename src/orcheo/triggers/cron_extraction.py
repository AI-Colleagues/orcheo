"""Derive a cron trigger configuration from a stored workflow version.

Workflow scripts declare their schedule with a ``CronTriggerNode``; ingestion
records it under ``graph.index.cron`` (or, for older versions, in the node
summary). Scheduling needs that declaration as a concrete
:class:`~orcheo.triggers.cron.CronTriggerConfig`, with any
``{{config.configurable.<name>}}`` placeholders resolved against the version's
runnable config.
"""

from __future__ import annotations
import re
from collections.abc import Mapping
from typing import Any
from orcheo.graph.ingestion.config import LANGGRAPH_SCRIPT_FORMAT
from orcheo.triggers.cron import CronTriggerConfig


# Matches a value that is exactly a single ``{{config.configurable.<name>}}``
# placeholder, optionally padded with whitespace inside the braces. Anchoring is
# handled by ``re.fullmatch`` at the call site, so no ``^``/``$`` here.
_CONFIGURABLE_TEMPLATE = re.compile(r"\{\{\s*config\.configurable\.([^}\s.]+)\s*\}\}")
_CRON_FIELDS = ("expression", "timezone", "allow_overlapping", "start_at", "end_at")


class CronExtractionError(ValueError):
    """Raised when a workflow's cron declaration cannot be turned into a config."""


def extract_version_cron_config(
    version: Mapping[str, Any],
) -> CronTriggerConfig | None:
    """Return the cron trigger config declared by a workflow version payload.

    Args:
        version: A serialized workflow version with ``graph`` and optional
            ``runnable_config`` entries.

    Returns:
        The cron config, or ``None`` when the version declares no cron trigger.

    Raises:
        CronExtractionError: If the version has no graph, declares more than
            one cron trigger, or references an undefined configurable value.
    """
    graph = version.get("graph")
    if not isinstance(graph, Mapping):
        raise CronExtractionError("Workflow version is missing graph data.")
    return extract_cron_config(graph, _extract_configurable(version))


def extract_cron_config(
    graph: Mapping[str, Any],
    configurable: Mapping[str, Any] | None = None,
) -> CronTriggerConfig | None:
    """Return the cron trigger config declared by ``graph``, if any."""
    entries = _index_cron_entries(graph)
    if entries is None:
        entries = [
            node
            for node in _extract_nodes(graph)
            if node.get("type") == "CronTriggerNode"
        ]
    if not entries:
        return None
    if len(entries) > 1:
        raise CronExtractionError("Workflow contains multiple cron triggers.")

    entry = entries[0]
    config_payload = {key: entry.get(key) for key in _CRON_FIELDS if key in entry}
    for key in ("expression", "timezone"):
        value = config_payload.get(key)
        if isinstance(value, str) and not value.strip():
            config_payload.pop(key)
    _resolve_configurable_templates(config_payload, configurable)
    return CronTriggerConfig.model_validate(config_payload)


def _index_cron_entries(graph: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    """Return ``graph.index.cron`` entries, or ``None`` when no index exists."""
    index = graph.get("index")
    if not isinstance(index, Mapping):
        return None
    cron_entries = index.get("cron")
    if not isinstance(cron_entries, list):
        return None
    resolved = [entry for entry in cron_entries if isinstance(entry, Mapping)]
    return resolved or None


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
    """Replace ``{{config.configurable.X}}`` placeholders with their values."""
    for key, value in list(config_payload.items()):
        if not isinstance(value, str):
            continue
        match = _CONFIGURABLE_TEMPLATE.fullmatch(value.strip())
        if match is None:
            continue
        name = match.group(1)
        if not configurable or name not in configurable:
            raise CronExtractionError(
                f"Cron trigger references '{{{{config.configurable.{name}}}}}' "
                f"but the workflow has no configurable value named '{name}'. "
                "Set a default for it in the workflow config."
            )
        config_payload[key] = configurable[name]


def _extract_nodes(graph: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return the serialized nodes list from a workflow graph payload."""
    nodes: Any
    if graph.get("format") in {LANGGRAPH_SCRIPT_FORMAT, "langgraph_script"}:
        summary = graph.get("summary")
        nodes = summary.get("nodes") if isinstance(summary, Mapping) else None
    else:
        nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return []
    return [node for node in nodes if isinstance(node, Mapping)]


__all__ = [
    "CronExtractionError",
    "extract_cron_config",
    "extract_version_cron_config",
]
