"""Tests for the ``CodeNode`` customisation base class (in-process path)."""

from __future__ import annotations
from typing import Any
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes.data.code import CodeNode


class _Doubler(CodeNode):
    """A CodeNode that doubles a stored value by an injected factor."""

    factor: int = 2

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        value = state["results"]["setter"]["value"]
        return {"doubled": value * self.factor}


@pytest.mark.asyncio
async def test_call_wraps_run_payload_under_results_node_name() -> None:
    """Calling a CodeNode stores ``run()``'s payload under results.<name>."""
    node = _Doubler(name="doubler", factor=3)
    state = State({"results": {"setter": {"value": 21}}})

    result = await node(state, {})

    assert result == {"results": {"doubler": {"doubled": 63}}}


@pytest.mark.asyncio
async def test_call_resolves_templated_injected_config() -> None:
    """``{{state}}`` templates in injected config resolve before ``run``."""

    class _Echo(CodeNode):
        label: str = "x"

        async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
            return {"echo": self.label}

    node = _Echo(name="echo", label="{{results.setter.who}}")
    state = State({"results": {"setter": {"who": "world"}}})

    result = await node(state, {})

    assert result == {"results": {"echo": {"echo": "world"}}}


@pytest.mark.asyncio
async def test_default_run_raises_not_implemented() -> None:
    """The default ``run`` raises ``NotImplementedError`` naming the subclass."""

    class _Unimplemented(CodeNode):
        pass

    node = _Unimplemented(name="bare")

    with pytest.raises(NotImplementedError, match="_Unimplemented.*must implement run"):
        await node.run(State({"results": {}}), {})
