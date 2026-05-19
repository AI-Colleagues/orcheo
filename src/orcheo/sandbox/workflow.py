"""Workflow-run dispatcher and node-tiering for the sandboxed execution path.

The Celery worker doesn't execute tenant-authored workflow code directly any
more. Instead, ``WorkflowSandboxDispatcher`` acquires a warm per-workspace
Workflow Sandbox, asks the sandbox to fork a fresh child process for the run,
streams results back, and releases the sandbox to the pool. Run-scoped
credentials are minted by the Credential Broker and revoked on completion.

Node tiering — first-party built-in nodes vs. tenant-authored Python — is
encoded in ``TRUSTED_NODE_TYPES``. The dispatcher uses the tiering when
deciding whether a given run *must* be sandboxed; a workflow composed entirely
of trusted nodes may take the in-worker fast path when the operator allows it
(``allow_in_worker_fast_path``).
"""

from __future__ import annotations
import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from orcheo.sandbox.broker import CredentialBroker
from orcheo.sandbox.manager import SandboxRuntimeManager
from orcheo.sandbox.models import SandboxLease


# Trusted, first-party node types that may run inside the worker process.
# Everything not in this set is treated as tenant-authored and forced into a
# Workflow Sandbox. The list is intentionally minimal and grown by operator
# review.
TRUSTED_NODE_TYPES: frozenset[str] = frozenset(
    {
        "AINode",
        "ChatModelNode",
        "RSSNode",
        "MongoDBNode",
        "SlackNode",
        "TelegramNode",
        "TaskNode",
        "DataTransformNode",
        "IntegrationNode",
    }
)


@dataclass(frozen=True)
class WorkflowRunSpec:
    """Description of a workflow run that the dispatcher must execute."""

    run_id: str
    workspace_id: str
    workflow_definition: Mapping[str, Any]
    inputs: Mapping[str, Any]
    node_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkflowRunResult:
    """Result returned by the sandbox runner for a single run."""

    run_id: str
    status: str
    outputs: Mapping[str, Any]
    error: str | None = None


class SandboxRunner(Protocol):
    """Protocol for dispatching a run into a sandbox container."""

    async def execute(
        self,
        lease: SandboxLease,
        spec: WorkflowRunSpec,
        broker_token: str,
    ) -> WorkflowRunResult:
        """Execute the workflow run inside ``lease`` and return the result."""


def requires_sandbox(node_types: Iterable[str]) -> bool:
    """Return True if any node type is not in the trusted set."""
    return any(node_type not in TRUSTED_NODE_TYPES for node_type in node_types)


class WorkflowSandboxDispatcher:
    """Run workflows inside per-workspace warm sandboxes."""

    def __init__(
        self,
        manager: SandboxRuntimeManager,
        runner: SandboxRunner,
        broker: CredentialBroker,
        *,
        allow_in_worker_fast_path: bool = False,
    ) -> None:
        """Initialize the dispatcher.

        Args:
            manager: Sandbox Runtime Manager owning per-workspace pools.
            runner: Component that knows how to execute a run inside a sandbox.
            broker: Credential Broker for minting / revoking run-scoped tokens.
            allow_in_worker_fast_path: When True, workflows composed entirely
                of trusted node types may execute in-worker (bypassing the
                sandbox). When False, every run goes through the sandbox.
        """
        self._manager = manager
        self._runner = runner
        self._broker = broker
        self._fast_path = allow_in_worker_fast_path

    def should_sandbox(self, spec: WorkflowRunSpec) -> bool:
        """Decide whether ``spec`` must be routed through a sandbox."""
        if not self._fast_path:
            return True
        return requires_sandbox(spec.node_types)

    async def dispatch(self, spec: WorkflowRunSpec) -> WorkflowRunResult:
        """Execute ``spec`` and return its result.

        Acquires a warm Workflow Sandbox, issues a run-scoped broker token,
        runs the workflow, releases the sandbox, and revokes the token.
        Failures inside the runner are surfaced as a failed ``WorkflowRunResult``
        rather than re-raised so the worker can persist the run state.
        """
        if not self.should_sandbox(spec):
            return await self._runner.execute(
                lease=_synthetic_lease(spec),
                spec=spec,
                broker_token="",
            )

        lease = await asyncio.to_thread(
            self._manager.acquire,
            spec.workspace_id,
            run_id=spec.run_id,
        )
        token = self._broker.issue(workspace_id=spec.workspace_id, run_id=spec.run_id)
        try:
            return await self._runner.execute(lease, spec, token)
        except Exception as exc:  # noqa: BLE001 — surface any runner failure
            return WorkflowRunResult(
                run_id=spec.run_id,
                status="failed",
                outputs={},
                error=str(exc),
            )
        finally:
            self._broker.revoke(spec.run_id)
            await asyncio.to_thread(self._manager.release, lease)


def _synthetic_lease(spec: WorkflowRunSpec) -> SandboxLease:
    """Build a synthetic in-worker lease for the trusted-only fast path."""
    from orcheo.sandbox.models import SandboxState

    return SandboxLease(
        lease_id=f"inproc-{spec.run_id}",
        workspace_id=spec.workspace_id,
        sandbox_id="in-worker",
        state=SandboxState.IN_USE,
    )
