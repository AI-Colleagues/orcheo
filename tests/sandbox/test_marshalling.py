"""Tests for sandbox input marshalling (Task 3.2)."""

from __future__ import annotations
import pytest
from orcheo.sandbox.exceptions import SandboxMarshallingError
from orcheo.sandbox.marshalling import build_inputs_envelope, project_mapping


def test_drops_non_serialisable_values_by_default() -> None:
    """Non-JSON-serialisable values are dropped under the default policy."""
    projected = project_mapping(
        {"keep": {"a": 1}, "drop": object()},
        label="state",
        on_nonserialisable="drop",
        node_id="x",
    )

    assert projected == {"keep": {"a": 1}}


def test_raise_policy_rejects_non_serialisable() -> None:
    """The ``raise`` policy surfaces a marshalling error."""
    with pytest.raises(SandboxMarshallingError, match="not JSON-serialisable"):
        project_mapping(
            {"bad": object()},
            label="state",
            on_nonserialisable="raise",
            node_id="x",
        )


def test_envelope_shape_and_internal_config_filtering() -> None:
    """The envelope has state/config/configurable, with internals filtered out."""
    envelope = build_inputs_envelope(
        {"results": {"a": 1}, "messages": [object()]},
        {"configurable": {"thread_id": "t1", "__pregel_send": object()}},
        {"threshold": 8},
        node_id="x",
    )

    assert envelope["state"] == {"results": {"a": 1}}  # messages dropped
    assert envelope["config"] == {"configurable": {"thread_id": "t1"}}
    assert envelope["configurable"] == {"threshold": 8}


def test_injected_config_must_be_serialisable() -> None:
    """Injected configurable fields must always be JSON-serialisable."""
    with pytest.raises(SandboxMarshallingError):
        build_inputs_envelope({}, None, {"bad": object()}, node_id="x")
