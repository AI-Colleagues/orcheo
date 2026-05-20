"""Workflow Sandbox entrypoint — the long-lived process inside a sandbox.

The Workflow Sandbox image runs ``python -m orcheo.sandbox.workflow_runner``.
This module listens on stdin for ``WorkflowRunSpec`` payloads, forks a fresh
child process per run, executes the workflow, and writes the result back to
stdout. The fresh child is mandatory: it gives fault isolation between runs
even when the sandbox is being reused via the warm pool.

The runner intentionally has no Celery, Redis, or Postgres dependencies — it
only knows how to execute a workflow graph for the workspace pinned by its
broker token. Credentials are resolved live by calling the Credential
Broker.

This module is also exercised by unit tests through ``run_in_subprocess``,
which bypasses the stdin loop for simpler testability.
"""

from __future__ import annotations
import json
import multiprocessing as mp
import sys
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, cast


def _execute_workflow_in_child(
    queue: mp.Queue[Mapping[str, Any]],
    workflow_definition: Mapping[str, Any],
    inputs: Mapping[str, Any],
) -> None:
    """Execute the workflow in a forked child and send the result back."""
    try:
        # In a real deployment this calls the graph builder + runner.
        # The runner module ships with the workflow-sandbox image, so the
        # heavy imports stay out of the worker.
        outputs = _run_graph(workflow_definition, inputs)
        queue.put({"status": "succeeded", "outputs": dict(outputs), "error": None})
    except Exception as exc:  # noqa: BLE001 — propagate failure to parent
        queue.put({"status": "failed", "outputs": {}, "error": str(exc)})


def _run_graph(
    workflow_definition: Mapping[str, Any], inputs: Mapping[str, Any]
) -> Mapping[str, Any]:
    """Execute the workflow graph and return its outputs.

    The real implementation imports the graph builder; this module-level
    function is patched in tests to avoid the heavy graph runtime.
    """
    # Imported lazily so unit tests of the runner can stub _run_graph without
    # paying the langgraph import cost.
    from orcheo.graph.builder import build_graph

    graph = build_graph(dict(workflow_definition))
    compiled = graph.compile()
    result = compiled.invoke(cast(Any, dict(inputs)))
    return cast("Mapping[str, Any]", result)


def run_in_subprocess(
    workflow_definition: Mapping[str, Any],
    inputs: Mapping[str, Any],
    *,
    spawn: bool = True,
) -> Mapping[str, Any]:
    """Run a workflow definition in a fresh child process and return its result.

    Args:
        workflow_definition: Serialized workflow graph.
        inputs: Workflow inputs.
        spawn: When True (default), use ``multiprocessing`` to fork a fresh
            child. Set to False in unit tests to execute in-process.

    Returns:
        The result dict produced by ``_execute_workflow_in_child``.
    """
    if not spawn:
        queue: list[Mapping[str, Any]] = []

        class _ListQueue:
            def put(self, item: Mapping[str, Any]) -> None:
                queue.append(item)

        _execute_workflow_in_child(_ListQueue(), workflow_definition, inputs)  # type: ignore[arg-type]
        return queue[0]
    ctx = mp.get_context("spawn")
    q: mp.Queue[Mapping[str, Any]] = ctx.Queue()
    process = ctx.Process(
        target=_execute_workflow_in_child,
        args=(q, workflow_definition, inputs),
        daemon=True,
    )
    process.start()
    process.join()
    return q.get_nowait()


def serve_stdin_loop() -> None:  # pragma: no cover - long-running entrypoint
    """Read newline-delimited JSON specs from stdin until EOF."""
    for line in sys.stdin:
        payload = json.loads(line)
        result = run_in_subprocess(payload["workflow_definition"], payload["inputs"])
        if is_dataclass(result) and not isinstance(result, type):
            serialized: Mapping[str, Any] = asdict(cast(Any, result))
        else:
            serialized = dict(result)
        sys.stdout.write(json.dumps(serialized) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    serve_stdin_loop()
