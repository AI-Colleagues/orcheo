"""Sandbox-backed CodeNode runnable and IR graph-builder wiring.

In restricted mode the trusted IR graph builder binds each ``CodeNodeSpec`` to a
:class:`SandboxCodeNode`. At run time the node resolves ``{{state}}`` templates in
its injected config (host-side), marshals the JSON inputs envelope, executes the
body in the MicroPython-WASM sandbox off the event loop, and wraps the returned
payload under ``results.<node_id>`` like ``TaskNode``. Limit breaches, non-JSON
outputs, and in-sandbox exceptions surface as structured, node-attributed errors
and are counted in :class:`SandboxMetrics`.
"""

from __future__ import annotations
import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from langchain_core.runnables import RunnableConfig
from orcheo.graph.ir.builder import CodeNodeFactory, build_state_graph_from_ir
from orcheo.graph.ir.models import CodeNodeSpec, GraphIR
from orcheo.graph.state import State
from orcheo.nodes.base import BaseRunnable, build_task_state_update
from orcheo.sandbox.exceptions import (
    SandboxError,
    SandboxExecutionError,
    SandboxLimitError,
    SandboxOutputError,
)
from orcheo.sandbox.marshalling import NonSerialisablePolicy, build_inputs_envelope
from orcheo.sandbox.runner import MicroPythonSandboxRunner


logger = logging.getLogger(__name__)


@dataclass
class SandboxMetrics:
    """Counters for sandboxed CodeNode execution outcomes."""

    invocations: int = 0
    successes: int = 0
    limit_errors: int = 0
    output_errors: int = 0
    execution_errors: int = 0


class SandboxCodeNode:
    """A LangGraph node that runs a ``CodeNodeSpec`` body in the sandbox."""

    def __init__(
        self,
        spec: CodeNodeSpec,
        runner: MicroPythonSandboxRunner,
        *,
        on_nonserialisable: NonSerialisablePolicy = "drop",
        metrics: SandboxMetrics | None = None,
    ) -> None:
        """Bind the spec, runner, marshalling policy, and metrics sink."""
        self.spec = spec
        self.runner = runner
        self.on_nonserialisable = on_nonserialisable
        self.metrics = metrics if metrics is not None else SandboxMetrics()
        self.name = spec.id

    async def __call__(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Execute the CodeNode body in the sandbox and return its state update."""
        self.metrics.invocations += 1
        inputs = self._marshal_inputs(state, config)
        try:
            outputs = await asyncio.to_thread(
                self.runner.run, self.spec.body, inputs, node_id=self.spec.id
            )
        except SandboxLimitError:
            self.metrics.limit_errors += 1
            logger.error("CodeNode %s exceeded sandbox resource limits", self.spec.id)
            raise
        except SandboxOutputError:
            self.metrics.output_errors += 1
            logger.error("CodeNode %s produced invalid sandbox output", self.spec.id)
            raise
        except SandboxError:
            self.metrics.execution_errors += 1
            raise
        return self._interpret_outputs(outputs)

    def _marshal_inputs(
        self, state: State, config: RunnableConfig | None
    ) -> dict[str, Any]:
        """Resolve injected-config templates and build the inputs envelope."""
        resolver = BaseRunnable(name=self.spec.id)
        configurable = {
            field: resolver._decode_value(self.spec.config[field], state)
            for field in self.spec.injected
            if field in self.spec.config
        }
        return build_inputs_envelope(
            state,
            config,
            configurable,
            on_nonserialisable=self.on_nonserialisable,
            node_id=self.spec.id,
        )

    def _interpret_outputs(self, outputs: Mapping[str, Any]) -> dict[str, Any]:
        """Translate the outputs envelope into a TaskNode-shaped state update."""
        if "error" in outputs:
            self.metrics.execution_errors += 1
            error = outputs["error"]
            message = (
                error.get("message", "error in CodeNode body")
                if isinstance(error, Mapping)
                else "error in CodeNode body"
            )
            error_type = error.get("type") if isinstance(error, Mapping) else None
            logger.error(
                "CodeNode %s raised %s in sandbox: %s",
                self.spec.id,
                error_type,
                message,
            )
            raise SandboxExecutionError(
                str(message), node_id=self.spec.id, error_type=error_type
            )
        payload = outputs.get("update")
        if not isinstance(payload, Mapping):
            self.metrics.output_errors += 1
            raise SandboxOutputError(
                "sandbox 'update' must be a JSON object", node_id=self.spec.id
            )
        self.metrics.successes += 1
        return build_task_state_update(self.spec.id, dict(payload))


def make_code_node_factory(
    runner: MicroPythonSandboxRunner | None = None,
    *,
    on_nonserialisable: NonSerialisablePolicy = "drop",
    metrics: SandboxMetrics | None = None,
) -> CodeNodeFactory:
    """Return a factory binding ``CodeNodeSpec`` instances to the sandbox.

    The returned callable matches the IR builder's ``code_node_factory`` contract
    so restricted-mode execution invokes the sandbox for every ``CodeNode``.
    """
    active_runner = runner if runner is not None else MicroPythonSandboxRunner()

    def factory(spec: CodeNodeSpec) -> SandboxCodeNode:
        return SandboxCodeNode(
            spec,
            active_runner,
            on_nonserialisable=on_nonserialisable,
            metrics=metrics,
        )

    return factory


def build_sandboxed_state_graph(
    ir: GraphIR | Mapping[str, Any],
    *,
    runner: MicroPythonSandboxRunner | None = None,
    on_nonserialisable: NonSerialisablePolicy = "drop",
    metrics: SandboxMetrics | None = None,
) -> Any:
    """Rebuild a runnable graph from an IR with CodeNodes wired to the sandbox."""
    factory = make_code_node_factory(
        runner, on_nonserialisable=on_nonserialisable, metrics=metrics
    )
    return build_state_graph_from_ir(ir, code_node_factory=factory)


__all__ = [
    "SandboxCodeNode",
    "SandboxMetrics",
    "build_sandboxed_state_graph",
    "make_code_node_factory",
]
