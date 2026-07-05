"""Additional coverage tests for base runnable and node primitives."""

from __future__ import annotations
from typing import Any, cast
import pytest
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, computed_field
from orcheo.graph.state import State
from orcheo.nodes.base import BaseNode, BaseRunnable, NoOpTaskNode
from orcheo.runtime.credentials import (
    CredentialReference,
    CredentialResolverUnavailableError,
)


class MinimalRunnable(BaseRunnable):
    """Minimal runnable for testing protected helper methods."""

    payload: Any = None


class SerializationNode(BaseNode):
    """Concrete node for exercising BaseNode serialization helpers."""


class FakeResolver:
    """Simple credential resolver used in tests."""

    def resolve(self, reference: CredentialReference) -> str:
        return f"resolved:{reference.identifier}:{'.'.join(reference.payload_path)}"


def test_decode_string_value_credential_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runnable = MinimalRunnable(name="demo")
    state = cast(State, {"node_results": {}})
    reference = CredentialReference(identifier="service", payload_path=("secret",))

    monkeypatch.setattr(
        "orcheo.nodes.base.parse_credential_reference",
        lambda value: reference if value == "[[service]]" else None,
    )
    monkeypatch.setattr(runnable, "_resolve_credential_reference", lambda _: "token")

    assert runnable._decode_string_value("[[service]]", state) == "token"


def test_decode_value_credential_reference_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runnable = MinimalRunnable(name="demo")
    reference = CredentialReference(identifier="db-key", payload_path=("secret",))

    monkeypatch.setattr(
        "orcheo.nodes.base.get_active_credential_resolver", lambda: FakeResolver()
    )

    assert (
        runnable._decode_value(reference, cast(State, {})) == "resolved:db-key:secret"
    )


def test_resolve_credential_reference_requires_active_resolver() -> None:
    runnable = MinimalRunnable(name="demo")
    reference = CredentialReference(identifier="missing", payload_path=("secret",))

    with pytest.raises(CredentialResolverUnavailableError, match="active resolver"):
        runnable._resolve_credential_reference(reference)


def test_decode_variables_ignores_config_for_non_mapping_state() -> None:
    runnable = MinimalRunnable(name="demo", payload="plain-text")

    runnable.decode_variables(cast(State, object()), config={"threshold": 0.9})

    assert runnable.payload == "plain-text"


def test_decode_value_returns_plain_values_unchanged() -> None:
    runnable = MinimalRunnable(name="demo")

    assert runnable._decode_value(123, cast(State, {"node_results": {}})) == 123


def test_decode_string_value_traverses_base_model_attributes() -> None:
    class NestedModel(BaseModel):
        value: str

    runnable = MinimalRunnable(name="demo")
    state = cast(State, {"model": NestedModel(value="from-model"), "node_results": {}})

    decoded = runnable._decode_string_value("{{model.value}}", state)

    assert decoded == "from-model"


def test_fallback_to_node_results_skips_explicit_node_results_path() -> None:
    state = cast(State, {"node_results": {"node1": "value"}})

    fallback = BaseNode._fallback_to_node_results(["node_results", "node1"], 0, state)

    assert fallback is None


def test_serialize_result_handles_computed_fields_and_collections() -> None:
    class ExampleModel(BaseModel):
        value: int

        @computed_field
        @property
        def doubled(self) -> int:
            return self.value * 2

    node = SerializationNode(name="serialize")
    payload = {
        "entry": ExampleModel(value=2),
        "items": (
            ExampleModel(value=1),
            [ExampleModel(value=3), "text", b"bytes", bytearray(b"data")],
        ),
    }

    serialized = node._serialize_result(payload)

    assert serialized == {
        "entry": {"value": 2},
        "items": (
            {"value": 1},
            [{"value": 3}, "text", b"bytes", bytearray(b"data")],
        ),
    }


@pytest.mark.asyncio
async def test_noop_task_node_run_returns_empty_payload() -> None:
    node = NoOpTaskNode(name="noop")

    result = await node.run(cast(State, {"node_results": {}}), RunnableConfig())

    assert result == {}
