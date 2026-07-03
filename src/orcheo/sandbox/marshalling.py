"""Marshal CodeNode sandbox inputs and outputs.

The sandbox receives a JSON-coercible envelope ``{state, config, configurable}``:

* ``state`` — a JSON projection of the node state. Non-serialisable top-level
  values are dropped (and logged) by default, or raise when the contract is set
  to ``"raise"``.
* ``config`` — the run config's ``configurable`` mapping, JSON-projected.
* ``configurable`` — the node's injected ``self.<field>`` values (credential-free
  and expected to be JSON-coercible).

The returned payload is wrapped by the sandbox ``CodeNode`` runnable under
``results.<node_id>``, matching the unrestricted ``TaskNode`` execution path.
"""

from __future__ import annotations
import json
import logging
from collections.abc import Mapping
from typing import Any, Literal
from orcheo.sandbox.exceptions import SandboxMarshallingError


logger = logging.getLogger(__name__)

# How to handle non-JSON-serialisable state values: drop them or raise.
NonSerialisablePolicy = Literal["drop", "raise"]


def _is_json_serialisable(value: Any) -> bool:
    """Return ``True`` when ``value`` can be serialised to JSON as-is."""
    try:
        json.dumps(value)
    except (TypeError, ValueError):
        return False
    return True


def project_mapping(
    data: Mapping[str, Any],
    *,
    label: str,
    on_nonserialisable: NonSerialisablePolicy,
    node_id: str | None,
) -> dict[str, Any]:
    """Return a JSON-coercible projection of ``data``, dropping bad values.

    Args:
        data: Mapping to project (state or run configurable).
        label: Human-readable label for log/error messages.
        on_nonserialisable: ``"drop"`` (default contract) or ``"raise"``.
        node_id: Node id for error attribution.

    Raises:
        SandboxMarshallingError: When ``on_nonserialisable="raise"`` and a value
            is not JSON-serialisable.
    """
    projected: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in data.items():
        if _is_json_serialisable(value):
            projected[str(key)] = value
            continue
        if on_nonserialisable == "raise":
            raise SandboxMarshallingError(
                f"{label} field '{key}' is not JSON-serialisable", node_id=node_id
            )
        dropped.append(str(key))
    if dropped:
        logger.warning(
            "CodeNode %s: dropped non-serialisable %s fields from sandbox input: %s",
            node_id,
            label,
            ", ".join(sorted(dropped)),
        )
    return projected


def build_inputs_envelope(
    state: Mapping[str, Any],
    config: Mapping[str, Any] | None,
    configurable: Mapping[str, Any],
    *,
    on_nonserialisable: NonSerialisablePolicy = "drop",
    node_id: str | None = None,
) -> dict[str, Any]:
    """Assemble the JSON inputs envelope for the sandbox.

    Raises:
        SandboxMarshallingError: When injected configurable fields are not
            JSON-serialisable (these must always be clean), or under the
            ``"raise"`` policy for state/config.
    """
    state_projection = project_mapping(
        state, label="state", on_nonserialisable=on_nonserialisable, node_id=node_id
    )
    run_configurable = _run_configurable(config)
    config_projection = project_mapping(
        run_configurable,
        label="config",
        on_nonserialisable=on_nonserialisable,
        node_id=node_id,
    )
    injected_projection = project_mapping(
        configurable,
        label="configurable",
        on_nonserialisable="raise",
        node_id=node_id,
    )
    return {
        "state": state_projection,
        "config": {"configurable": config_projection},
        "configurable": injected_projection,
    }


def _run_configurable(config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the run config's user ``configurable`` mapping (or an empty one).

    LangGraph injects internal, non-serialisable callables under dunder-prefixed
    keys (``__pregel_*``); these are filtered out so they neither reach the
    sandbox nor trigger spurious drop warnings.
    """
    if not isinstance(config, Mapping):
        return {}
    configurable = config.get("configurable")
    if not isinstance(configurable, Mapping):
        return {}
    return {
        key: value
        for key, value in configurable.items()
        if not str(key).startswith("__")
    }


__all__ = [
    "NonSerialisablePolicy",
    "build_inputs_envelope",
    "project_mapping",
]
