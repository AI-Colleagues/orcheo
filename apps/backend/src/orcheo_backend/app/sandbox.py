"""Shared sandbox runtime wiring for the backend / worker processes.

This module owns the *single* set of sandbox primitives used by every
workflow execution path:

- one :class:`SandboxRuntimeManager` backed by
  :class:`RemoteContainerRuntime`,
- one :class:`SandboxedProcessLauncher` for ``ExternalAgentNode``,
- one :class:`WorkflowSandboxDispatcher` for tenant-authored workflow runs.

Centralizing the construction here keeps Docker-socket access out of the
backend (per the design's container-runtime socket note) and ensures all
call sites share lease accounting.

The dispatcher routes runs through a per-workspace gVisor sandbox by
default. Operators may opt trusted-only graphs into an in-worker fast path
by setting ``ORCHEO_SANDBOX_FAST_PATH_TRUSTED=true`` — this matches the
design's Open Issue #1 (fast path vs uniform routing) and is otherwise off.
"""

from __future__ import annotations
import logging
import os
from collections.abc import Iterable
from threading import Lock
from typing import Any
from orcheo.sandbox.broker import CredentialBroker
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.errors import SandboxError
from orcheo.sandbox.launcher import SandboxedProcessLauncher
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.remote import (
    RemoteContainerRuntime,
    RemoteSandboxExec,
    RemoteSandboxRunner,
)
from orcheo.sandbox.workflow import (
    TRUSTED_NODE_TYPES,
    WorkflowRunSpec,
    WorkflowSandboxDispatcher,
)


logger = logging.getLogger(__name__)


class SandboxRuntimeNotConfiguredError(SandboxError):
    """Raised when sandbox primitives are requested without configuration."""


class _SandboxBootstrap:
    """Lazily build and cache the per-process sandbox primitives."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._manager: SandboxRuntimeManager | None = None
        self._launcher: SandboxedProcessLauncher | None = None
        self._dispatcher: WorkflowSandboxDispatcher | None = None
        self._runtime: RemoteContainerRuntime | None = None
        self._exec_backend: RemoteSandboxExec | None = None
        self._runner: RemoteSandboxRunner | None = None
        self._broker: CredentialBroker | None = None

    def configure(self, broker: CredentialBroker) -> None:
        """Bind the credential broker. Must be called before dispatcher use."""
        with self._lock:
            self._broker = broker

    def _runtime_url(self) -> str:
        url = os.getenv("ORCHEO_SANDBOX_RUNTIME_URL")
        if not url:
            msg = (
                "ORCHEO_SANDBOX_RUNTIME_URL is not set. Sandboxing is always on; "
                "point this at the sandbox-runtime service (e.g. "
                "http://sandbox-runtime:9090) before starting the backend."
            )
            raise SandboxRuntimeNotConfiguredError(msg)
        return url

    def _ensure_manager(self) -> SandboxRuntimeManager:
        if self._manager is None:
            self._runtime = RemoteContainerRuntime(self._runtime_url())
            self._manager = SandboxRuntimeManager(
                runtime=self._runtime,
                settings=SandboxSettings(),
            )
        return self._manager

    def launcher(self) -> SandboxedProcessLauncher:
        """Return the shared ``SandboxedProcessLauncher``."""
        with self._lock:
            if self._launcher is None:
                manager = self._ensure_manager()
                self._exec_backend = RemoteSandboxExec(self._runtime_url())
                self._launcher = SandboxedProcessLauncher(
                    manager, exec_backend=self._exec_backend
                )
            return self._launcher

    def dispatcher(self) -> WorkflowSandboxDispatcher:
        """Return the shared ``WorkflowSandboxDispatcher``."""
        with self._lock:
            if self._dispatcher is None:
                if self._broker is None:
                    msg = (
                        "Sandbox dispatcher requested before the credential broker "
                        "was configured. Call configure_sandbox(broker) first."
                    )
                    raise SandboxRuntimeNotConfiguredError(msg)
                manager = self._ensure_manager()
                self._runner = RemoteSandboxRunner(self._runtime_url())
                self._dispatcher = WorkflowSandboxDispatcher(
                    manager,
                    self._runner,
                    self._broker,
                    allow_in_worker_fast_path=_fast_path_enabled(),
                )
            return self._dispatcher


_bootstrap = _SandboxBootstrap()


def configure_sandbox(broker: CredentialBroker) -> None:
    """Bind the credential broker into the shared sandbox bootstrap."""
    _bootstrap.configure(broker)


def get_sandbox_launcher() -> SandboxedProcessLauncher:
    """Return the shared sandbox launcher."""
    return _bootstrap.launcher()


def get_sandbox_dispatcher() -> WorkflowSandboxDispatcher:
    """Return the shared workflow-sandbox dispatcher."""
    return _bootstrap.dispatcher()


def reset_sandbox_bootstrap() -> None:
    """Reset the cached bootstrap. Test-only — never call from runtime code."""
    global _bootstrap  # noqa: PLW0603
    _bootstrap = _SandboxBootstrap()


def install_sandbox_bootstrap(bootstrap: _SandboxBootstrap) -> None:
    """Replace the bootstrap with a pre-built instance. Test-only injection point."""
    global _bootstrap  # noqa: PLW0603
    _bootstrap = bootstrap


def _fast_path_enabled() -> bool:
    return os.getenv("ORCHEO_SANDBOX_FAST_PATH_TRUSTED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def collect_node_types(graph_config: Any) -> tuple[str, ...]:
    """Pull the unique node-type names from a graph config dict.

    Used by callers to decide whether ``WorkflowSandboxDispatcher`` will
    fast-path a run. The set is intentionally tolerant: missing or malformed
    nodes default to an empty list, in which case the dispatcher treats the
    run as trusted (no untrusted nodes ⇒ no forced sandbox), and the
    operator's ``ORCHEO_SANDBOX_FAST_PATH_TRUSTED`` setting decides routing.
    """
    if not isinstance(graph_config, dict):
        return ()
    seen: list[str] = []
    nodes: Iterable[Any] = graph_config.get("nodes") or []
    for node in nodes:
        if isinstance(node, dict):
            node_type = node.get("type") or node.get("kind")
            if isinstance(node_type, str) and node_type not in seen:
                seen.append(node_type)
    return tuple(seen)


def build_workflow_run_spec(
    *,
    execution_id: str,
    workspace_id: str,
    graph_config: dict[str, Any],
    inputs: dict[str, Any],
) -> WorkflowRunSpec:
    """Build a ``WorkflowRunSpec`` for the dispatcher."""
    return WorkflowRunSpec(
        run_id=execution_id,
        workspace_id=workspace_id,
        workflow_definition=graph_config,
        inputs=inputs,
        node_types=collect_node_types(graph_config),
    )


def run_uses_trusted_nodes_only(node_types: Iterable[str]) -> bool:
    """Return True iff every node type is in ``TRUSTED_NODE_TYPES``."""
    types = list(node_types)
    if not types:
        return True
    return all(node_type in TRUSTED_NODE_TYPES for node_type in types)
