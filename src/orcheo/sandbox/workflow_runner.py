"""Workflow Sandbox runner invoked inside a leased sandbox.

The sandbox runtime invokes ``python -m orcheo.sandbox.workflow_runner`` via
``docker exec`` for each workflow dispatch. This module listens on stdin for
``WorkflowRunSpec`` payloads, forks a fresh child process per run, executes the
workflow, and writes the result back to stdout. The fresh child is mandatory:
it gives fault isolation between runs even when the sandbox is being reused
via the warm pool.

The runner intentionally has no Celery, Redis, or Postgres dependencies — it
only knows how to execute a workflow graph for the workspace pinned by its
broker token. Credentials are resolved live by calling the Credential
Broker.

This module is also exercised by unit tests through ``run_in_subprocess``,
which bypasses the stdin loop for simpler testability.
"""

from __future__ import annotations
import asyncio
import fcntl
import json
import multiprocessing as mp
import os
import sys
import threading
import traceback
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import asdict, is_dataclass
from pathlib import Path
from queue import Empty
from typing import Any, cast
import httpx
from orcheo.runtime.attachments import hydrate_attachment_runtime_config
from orcheo.runtime.credentials import (
    UnknownCredentialPayloadError,
    credential_resolution,
)
from orcheo.runtime.credentials.references import CredentialReference
from orcheo.sandbox.dispatch import use_launcher
from orcheo.sandbox.launcher import LocalProcessLauncher


_THREAD_STATE_FILE_ENV: str = "ORCHEO_WORKFLOW_THREAD_STATE_FILE"
_DEFAULT_THREAD_STATE_FILE: Path = Path("/scratch/orcheo-thread-state.json")


class _BrokerCredentialResolver:
    """Resolve sandbox credential references through the backend broker."""

    def __init__(
        self,
        *,
        broker_url: str,
        broker_token: str,
        run_id: str,
        workspace_id: str | None,
    ) -> None:
        self._broker_url = broker_url
        self._broker_token = broker_token
        self._run_id = run_id
        self._workspace_id = workspace_id

    def resolve(self, reference: CredentialReference) -> str:
        """Resolve secret credential references through the broker HTTP API."""
        if reference.payload_path not in ((), ("secret",)):
            path = ".".join(reference.payload_path)
            msg = (
                "Sandbox credential broker only supports secret payloads; "
                f"got {path!r} for {reference.identifier!r}"
            )
            raise UnknownCredentialPayloadError(msg)
        headers = {"Authorization": f"Bearer {self._broker_token}"}
        if self._workspace_id:
            headers["X-Orcheo-Workspace"] = self._workspace_id
        response = httpx.post(
            self._broker_url,
            json={
                "run_id": self._run_id,
                "credential_name": reference.identifier,
            },
            headers=headers,
            timeout=30.0,
        )
        if not response.is_success:
            msg = (
                f"Credential broker resolve failed for {reference.identifier!r}: "
                f"{response.status_code} {response.text}"
            )
            raise RuntimeError(msg)
        value = response.json().get("value")
        if not isinstance(value, str):
            msg = (
                "Credential broker returned an invalid value for "
                f"{reference.identifier!r}"
            )
            raise RuntimeError(msg)
        return value


class _SandboxThreadStateStore:
    """Persist thread state inside the sandbox container between runs.

    The workflow runner executes in a fresh child process for every dispatch,
    but the surrounding workspace sandbox container is warm-reused. A small
    file-backed store under ``/scratch`` lets ``save_thread_state`` survive
    across follow-up turns without exposing backend database credentials to
    tenant code.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._lock_path = self._path.with_name(f"{self._path.name}.lock")

    @classmethod
    def from_env(cls) -> _SandboxThreadStateStore:
        """Build the store path from the sandbox environment."""
        raw_path = os.getenv(_THREAD_STATE_FILE_ENV, str(_DEFAULT_THREAD_STATE_FILE))
        return cls(Path(raw_path))

    @staticmethod
    def _namespace_key(namespace: tuple[str, ...]) -> str:
        return json.dumps(list(namespace), separators=(",", ":"))

    def _with_file_lock(self, callback: Callable[[], Any]) -> Any:
        """Run ``callback`` while holding an exclusive lock on the backing file."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                return callback()
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    def _read_payload(self) -> dict[str, dict[str, Any]]:
        with self._lock:

            def _load() -> dict[str, dict[str, Any]]:
                try:
                    with self._path.open("r", encoding="utf-8") as handle:
                        payload = json.load(handle)
                except FileNotFoundError:
                    return {}
                except Exception:  # noqa: BLE001
                    return {}
                if isinstance(payload, dict):
                    return {
                        str(namespace): value
                        for namespace, value in payload.items()
                        if isinstance(value, dict)
                    }
                return {}

            return self._with_file_lock(_load)

    def _write_payload(self, payload: dict[str, dict[str, Any]]) -> None:
        with self._lock:

            def _store() -> None:
                tmp_path = self._path.with_name(f"{self._path.name}.tmp")
                with tmp_path.open("w", encoding="utf-8") as handle:
                    json.dump(
                        payload, handle, separators=(",", ":"), ensure_ascii=False
                    )
                tmp_path.replace(self._path)

            self._with_file_lock(_store)

    async def aget(self, namespace: tuple[str, ...], key: str) -> Any:
        """Return a stored item using the LangGraph store protocol."""
        payload = await asyncio.to_thread(self._read_payload)
        namespace_payload = payload.get(self._namespace_key(namespace), {})
        if key not in namespace_payload:
            return None
        return {"value": namespace_payload[key]}

    async def aput(self, namespace: tuple[str, ...], key: str, value: Any) -> None:
        """Persist an item using the LangGraph store protocol."""

        def _store() -> None:
            payload = self._read_payload()
            namespace_key = self._namespace_key(namespace)
            namespace_payload = payload.setdefault(namespace_key, {})
            namespace_payload[key] = value
            self._write_payload(payload)

        await asyncio.to_thread(_store)


def _credential_context(
    *,
    run_id: str,
    workspace_id: str | None,
) -> AbstractContextManager[Any]:
    """Bind the broker credential resolver when this run has a broker token."""
    broker_token = os.getenv("ORCHEO_BROKER_TOKEN", "").strip()
    if not broker_token:
        return nullcontext()
    broker_url = os.getenv("ORCHEO_CREDENTIAL_BROKER_URL", "").strip()
    if not broker_url:
        msg = "ORCHEO_CREDENTIAL_BROKER_URL is required for sandbox credentials"
        raise RuntimeError(msg)
    return credential_resolution(
        cast(
            Any,
            _BrokerCredentialResolver(
                broker_url=broker_url,
                broker_token=broker_token,
                run_id=run_id,
                workspace_id=workspace_id,
            ),
        )
    )


def _execute_workflow_in_child(
    queue: mp.Queue[Mapping[str, Any]],
    workflow_definition: Mapping[str, Any],
    inputs: Mapping[str, Any],
    runnable_config: Mapping[str, Any],
    state_config: Mapping[str, Any],
    run_id: str,
    workspace_id: str | None,
) -> None:
    """Execute the workflow in a forked child and send the result back."""
    try:
        # In a real deployment this calls the graph builder + runner.
        # The runner module ships with the workflow-sandbox image, so the
        # heavy imports stay out of the worker.
        outputs = _run_graph(
            workflow_definition,
            inputs,
            runnable_config=runnable_config,
            state_config=state_config,
            run_id=run_id,
            workspace_id=workspace_id,
        )
        queue.put({"status": "succeeded", "outputs": dict(outputs), "error": None})
    except Exception as exc:  # noqa: BLE001 — propagate failure to parent
        # Include the exception type AND the traceback so an empty
        # ``str(exc)`` (e.g. exceptions raised with no args) still tells the
        # caller what actually broke. Also echo to stderr so the traceback
        # appears in the sandbox-runtime container logs alongside the
        # serialized failure payload.
        tb = traceback.format_exc()
        print(tb, file=sys.stderr, flush=True)
        detail = str(exc).strip()
        error_message = (
            f"{type(exc).__name__}: {detail}\n{tb}"
            if detail
            else f"{type(exc).__name__} (no message)\n{tb}"
        )
        queue.put({"status": "failed", "outputs": {}, "error": error_message})


def _run_graph(
    workflow_definition: Mapping[str, Any],
    inputs: Mapping[str, Any],
    *,
    runnable_config: Mapping[str, Any] | None = None,
    state_config: Mapping[str, Any] | None = None,
    run_id: str = "",
    workspace_id: str | None = None,
) -> Mapping[str, Any]:
    """Execute the workflow graph and return its outputs.

    The real implementation imports the graph builder; this module-level
    function is patched in tests to avoid the heavy graph runtime.
    """
    # Imported lazily so unit tests of the runner can stub _run_graph without
    # paying the langgraph import cost.
    from orcheo.graph.builder import build_graph
    from orcheo.runtime.state_builder import build_initial_state

    runnable_config = hydrate_attachment_runtime_config(runnable_config)
    state_config = hydrate_attachment_runtime_config(state_config)
    graph = build_graph(dict(workflow_definition))
    compiled = graph.compile(store=cast(Any, _SandboxThreadStateStore.from_env()))
    state = build_initial_state(
        workflow_definition,
        inputs,
        state_config,
        workspace_id,
    )
    # Bind a local launcher so any ``ExternalAgentNode`` invoked by the graph
    # runs the CLI in this process tree instead of trying to dispatch into
    # *another* sandbox. The whole workflow is already running inside the
    # per-workspace sandbox the dispatcher acquired; recursing through the
    # remote sandbox-runtime here would deadlock at best, and at worst fail
    # with "no launcher bound" (the bound launcher only lives in the parent
    # backend / worker process, not the child mp.Process that the runner
    # spawns).
    with (
        _credential_context(run_id=run_id, workspace_id=workspace_id),
        use_launcher(LocalProcessLauncher()),
    ):
        result = asyncio.run(
            compiled.ainvoke(
                cast(Any, state),
                config=cast(Any, dict(runnable_config or {})),
            )
        )
    return cast("Mapping[str, Any]", result)


def run_in_subprocess(
    workflow_definition: Mapping[str, Any],
    inputs: Mapping[str, Any],
    *,
    runnable_config: Mapping[str, Any] | None = None,
    state_config: Mapping[str, Any] | None = None,
    run_id: str = "",
    workspace_id: str | None = None,
    spawn: bool = True,
) -> Mapping[str, Any]:
    """Run a workflow definition in a fresh child process and return its result.

    Args:
        workflow_definition: Serialized workflow graph.
        inputs: Workflow inputs.
        runnable_config: LangChain runtime config passed into graph invocation.
        state_config: Runtime config copied into the initial workflow state.
        run_id: Run identifier used by the run-scoped credential broker.
        workspace_id: Workspace identifier injected into the initial state and
            credential broker calls.
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

        _execute_workflow_in_child(
            _ListQueue(),  # type: ignore[arg-type]
            workflow_definition,
            inputs,
            runnable_config or {},
            state_config or {},
            run_id,
            workspace_id,
        )
        return queue[0]
    ctx = mp.get_context("spawn")
    q: mp.Queue[Mapping[str, Any]] = ctx.Queue()
    process = ctx.Process(
        target=_execute_workflow_in_child,
        args=(
            q,
            workflow_definition,
            inputs,
            runnable_config or {},
            state_config or {},
            run_id,
            workspace_id,
        ),
        daemon=True,
    )
    process.start()
    process.join()
    try:
        return q.get_nowait()
    except Empty:
        # The child exited without putting a result on the queue: either it
        # was killed (signal / OOM) or it died before reaching the try/except
        # in ``_execute_workflow_in_child`` (e.g. import-time crash during
        # spawn). Surface the exit status so the failure is debuggable from
        # the dispatcher logs instead of just ``_queue.Empty``.
        exitcode = process.exitcode
        if exitcode is None:
            detail = "child process is still running after join()"
        elif exitcode < 0:
            detail = f"killed by signal {-exitcode}"
        elif exitcode == 0:
            detail = "exited cleanly without producing a result"
        else:
            detail = f"exited with status {exitcode}"
        return {
            "status": "failed",
            "outputs": {},
            "error": f"sandbox child process produced no result ({detail})",
        }


def _json_default(value: Any) -> Any:
    """Convert workflow-state objects into JSON-safe sandbox result values."""
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(cast(Any, value))

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, set | frozenset):
        return list(value)

    return str(value)


def serve_stdin_loop() -> None:  # pragma: no cover - long-running entrypoint
    """Read newline-delimited JSON specs from stdin until EOF."""
    for line in sys.stdin:
        payload = json.loads(line)
        result = run_in_subprocess(
            payload["workflow_definition"],
            payload["inputs"],
            runnable_config=payload.get("runnable_config") or {},
            state_config=payload.get("state_config") or {},
            run_id=str(payload.get("run_id") or ""),
            workspace_id=payload.get("workspace_id"),
        )
        if is_dataclass(result) and not isinstance(result, type):
            serialized: Mapping[str, Any] = asdict(cast(Any, result))
        else:
            serialized = dict(result)
        sys.stdout.write(json.dumps(serialized, default=_json_default) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover - container entrypoint
    serve_stdin_loop()
