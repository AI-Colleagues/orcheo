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
from uuid import UUID, uuid4
from orcheo.graph.ingestion import (
    DEFAULT_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_SCRIPT_SIZE_LIMIT,
)
from orcheo.models.credential_scope import CredentialAccessContext
from orcheo.sandbox.broker import (
    CredentialBroker,
    InMemoryRevocationStore,
    RedisRevocationStore,
    RevocationStore,
)
from orcheo.sandbox.config import SandboxSettings
from orcheo.sandbox.errors import SandboxError
from orcheo.sandbox.launcher import SandboxedProcessLauncher
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.remote import (
    RemoteContainerRuntime,
    RemoteSandboxExec,
    RemoteSandboxIngestor,
    RemoteSandboxRunner,
)
from orcheo.sandbox.workflow import (
    TRUSTED_NODE_TYPES,
    WorkflowRunSpec,
    WorkflowSandboxDispatcher,
)
from orcheo_backend.app.dependencies import get_vault


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
        self._ingestor: RemoteSandboxIngestor | None = None
        self._broker: CredentialBroker | None = None

    def configure(self, broker: CredentialBroker) -> None:
        """Bind the credential broker. Must be called before dispatcher use."""
        self._control_token()
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
            self._runtime = RemoteContainerRuntime(
                self._runtime_url(), control_token=self._control_token()
            )
            self._manager = SandboxRuntimeManager(
                runtime=self._runtime,
                settings=SandboxSettings.from_env(),
            )
        return self._manager

    def _control_token(self) -> str:
        token = os.getenv("ORCHEO_SANDBOX_CONTROL_TOKEN", "")
        if not token:
            msg = (
                "ORCHEO_SANDBOX_CONTROL_TOKEN is not set. Backend and worker "
                "must authenticate to the sandbox-runtime control service."
            )
            raise SandboxRuntimeNotConfiguredError(msg)
        return token

    def launcher(self) -> SandboxedProcessLauncher:
        """Return the shared ``SandboxedProcessLauncher``."""
        with self._lock:
            if self._launcher is None:
                manager = self._ensure_manager()
                self._exec_backend = RemoteSandboxExec(
                    self._runtime_url(), control_token=self._control_token()
                )
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
                self._runner = RemoteSandboxRunner(
                    self._runtime_url(), control_token=self._control_token()
                )
                self._dispatcher = WorkflowSandboxDispatcher(
                    manager,
                    self._runner,
                    self._broker,
                    allow_in_worker_fast_path=_fast_path_enabled(),
                )
            return self._dispatcher

    async def ingest_script(
        self,
        *,
        workspace_id: str,
        source: str,
        entrypoint: str | None,
        max_script_bytes: int | None,
        execution_timeout_seconds: float | None,
    ) -> dict[str, Any]:
        """Ingest tenant source in a fresh, destroyed-after-use sandbox."""
        with self._lock:
            manager = self._ensure_manager()
            if self._ingestor is None:
                self._ingestor = RemoteSandboxIngestor(
                    self._runtime_url(), control_token=self._control_token()
                )
            ingestor = self._ingestor
        preview_workspace = f"ingest:{workspace_id}:{uuid4().hex}"
        lease = manager.acquire(preview_workspace, run_id="script-ingestion")
        try:
            return await ingestor.ingest(
                lease.sandbox_id,
                source=source,
                entrypoint=entrypoint,
                max_script_bytes=max_script_bytes,
                execution_timeout_seconds=execution_timeout_seconds,
            )
        finally:
            manager.destroy(lease)


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


async def ingest_sandboxed_script(
    *,
    workspace_id: str,
    source: str,
    entrypoint: str | None = None,
    max_script_bytes: int | None = DEFAULT_SCRIPT_SIZE_LIMIT,
    execution_timeout_seconds: float | None = DEFAULT_EXECUTION_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Build the stored graph payload without executing tenant source locally."""
    return await _bootstrap.ingest_script(
        workspace_id=workspace_id,
        source=source,
        entrypoint=entrypoint,
        max_script_bytes=max_script_bytes,
        execution_timeout_seconds=execution_timeout_seconds,
    )


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
    fast-path a run. Returns ``()`` for missing/malformed graphs; downstream
    decision helpers (``run_uses_trusted_nodes_only`` /
    ``WorkflowSandboxDispatcher.should_sandbox``) treat an empty tuple as
    untrusted so an unclassifiable graph always lands in the sandbox.
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
    runnable_config: dict[str, Any] | None = None,
    state_config: dict[str, Any] | None = None,
) -> WorkflowRunSpec:
    """Build a ``WorkflowRunSpec`` for the dispatcher."""
    return WorkflowRunSpec(
        run_id=execution_id,
        workspace_id=workspace_id,
        workflow_definition=graph_config,
        inputs=inputs,
        node_types=collect_node_types(graph_config),
        runnable_config=runnable_config or {},
        state_config=state_config or {},
    )


def build_credential_broker() -> CredentialBroker:
    """Build a credential broker bound to the process-local vault.

    Both the FastAPI application and the Celery worker call this helper so
    sandboxed runs can resolve credentials through a shared, workspace-pinned
    broker. The lookup is performed lazily — ``get_vault`` is re-resolved via
    this module on every call so test overrides (which monkeypatch
    ``orcheo_backend.app.sandbox.get_vault``) and runtime rebinding both work.
    """

    def _resolve_credential(*, workspace_id: str, credential_name: str) -> str:
        vault = get_vault()
        context = CredentialAccessContext(workspace_id=UUID(workspace_id))
        for metadata in vault.list_credentials(
            context=context,
            workspace_id=workspace_id,
        ):
            if metadata.name == credential_name:
                return vault.reveal_secret(credential_id=metadata.id, context=context)
        raise KeyError(credential_name)

    broker_secret = os.getenv("ORCHEO_CREDENTIAL_BROKER_SECRET")
    if not broker_secret:
        msg = (
            "ORCHEO_CREDENTIAL_BROKER_SECRET is not set. Sandboxing is always on; "
            "generate a secret with `python -m orcheo.sandbox.broker --gen-secret` "
            "and export it before starting the backend or worker."
        )
        raise RuntimeError(msg)
    revocation_store: RevocationStore
    if os.getenv("ORCHEO_SANDBOX_REVOCATION_STORE", "redis") == "memory":
        revocation_store = InMemoryRevocationStore()
    else:
        revocation_store = RedisRevocationStore(
            os.getenv("REDIS_URL", "redis://redis:6379/0")
        )
    return CredentialBroker(
        secret=broker_secret,
        resolver=_resolve_credential,
        revocation_store=revocation_store,
    )


def ensure_sandbox_configured() -> None:
    """Wire the shared sandbox bootstrap with a fresh broker if unconfigured.

    Safe to call from any entry point — the underlying bootstrap stores a
    single broker reference behind a lock, so repeated calls are no-ops.
    """
    if _bootstrap._broker is None:  # noqa: SLF001 — module-private cache
        configure_sandbox(build_credential_broker())


def run_uses_trusted_nodes_only(node_types: Iterable[str]) -> bool:
    """Return True iff every node type is in ``TRUSTED_NODE_TYPES``.

    Fails closed for empty input: an unclassifiable graph (no node types
    parsed) is treated as untrusted so it cannot silently take the in-worker
    fast path. Mirrors ``orcheo.sandbox.workflow.requires_sandbox`` so the
    backend and dispatcher agree on what "trusted-only" means.
    """
    types = list(node_types)
    if not types:
        return False
    return all(node_type in TRUSTED_NODE_TYPES for node_type in types)
