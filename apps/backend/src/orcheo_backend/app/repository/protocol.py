"""Contracts shared by repository implementations."""

from __future__ import annotations
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID
from orcheo.listeners import (
    ListenerCursor,
    ListenerDispatchPayload,
    ListenerSubscription,
    ListenerSubscriptionStatus,
)
from orcheo.models.workflow import (
    ChatKitStartScreenPrompt,
    ChatKitSupportedModel,
    Workflow,
    WorkflowDraftAccess,
    WorkflowRun,
    WorkflowVersion,
)
from orcheo.triggers.cron import CronTriggerConfig
from orcheo.triggers.manual import ManualDispatchRequest
from orcheo.triggers.retry import RetryDecision, RetryPolicyConfig
from orcheo.triggers.webhook import WebhookTriggerConfig


@dataclass(slots=True)
class VersionDiff:
    """Represents a unified diff between two workflow graphs."""

    base_version: int
    target_version: int
    diff: list[str]


@runtime_checkable
class WorkflowRepository(Protocol):
    """Protocol describing workflow repository behaviour."""

    async def list_workflows(
        self,
        *,
        include_archived: bool = False,
        tenant_id: str | None = None,
    ) -> list[Workflow]:
        """Return workflows stored within the repository.

        When *tenant_id* is provided only workflows belonging to that tenant
        are returned; omitting it returns all workflows (single-tenant mode).
        """

    async def create_workflow(
        self,
        *,
        name: str,
        handle: str | None = None,
        slug: str | None,
        description: str | None,
        tags: Iterable[str] | None,
        draft_access: WorkflowDraftAccess,
        actor: str,
        tenant_id: str | None = None,
    ) -> Workflow:
        """Persist and return a new workflow definition."""

    async def get_workflow(
        self,
        workflow_id: UUID,
        *,
        tenant_id: str | None = None,
    ) -> Workflow:
        """Return a single workflow by identifier.

        When *tenant_id* is provided the workflow is only returned when it
        belongs to that tenant; otherwise WorkflowNotFoundError is raised.
        """

    async def get_workflow_tenant_id(self, workflow_id: UUID) -> str | None:
        """Return the tenant_id stored for the workflow, or None if unscoped."""

    async def resolve_workflow_ref(
        self,
        workflow_ref: str,
        *,
        include_archived: bool = True,
        tenant_id: str | None = None,
    ) -> UUID:
        """Resolve a user-facing workflow ref to the canonical UUID.

        When *tenant_id* is provided resolution is limited to that tenant's
        workflows.
        """

    async def update_workflow(
        self,
        workflow_id: UUID,
        *,
        name: str | None,
        handle: str | None = None,
        description: str | None,
        tags: Iterable[str] | None,
        chatkit_start_screen_prompts: list[ChatKitStartScreenPrompt] | None = None,
        chatkit_supported_models: list[ChatKitSupportedModel] | None = None,
        clear_chatkit_start_screen_prompts: bool = False,
        clear_chatkit_supported_models: bool = False,
        draft_access: WorkflowDraftAccess | None = None,
        is_archived: bool | None,
        actor: str,
    ) -> Workflow:
        """Mutate workflow metadata and return the updated record."""

    async def archive_workflow(self, workflow_id: UUID, *, actor: str) -> Workflow:
        """Archive the specified workflow."""

    async def publish_workflow(
        self,
        workflow_id: UUID,
        *,
        require_login: bool,
        actor: str,
    ) -> Workflow:
        """Mark the workflow as public."""

    async def revoke_publish(self, workflow_id: UUID, *, actor: str) -> Workflow:
        """Revoke public access for the workflow."""

    async def create_version(
        self,
        workflow_id: UUID,
        *,
        graph: dict[str, Any],
        metadata: dict[str, Any],
        runnable_config: dict[str, Any] | None = None,
        notes: str | None,
        created_by: str,
    ) -> WorkflowVersion:
        """Persist a new workflow version for the workflow."""

    async def list_versions(self, workflow_id: UUID) -> list[WorkflowVersion]:
        """Return ordered versions for the given workflow."""

    async def get_version_by_number(
        self, workflow_id: UUID, version_number: int
    ) -> WorkflowVersion:
        """Return a workflow version by human-friendly number."""

    async def update_version_runnable_config(
        self,
        workflow_id: UUID,
        *,
        version_number: int,
        runnable_config: dict[str, Any] | None,
        actor: str,
    ) -> WorkflowVersion:
        """Update only runnable config for an existing workflow version."""

    async def get_version(self, version_id: UUID) -> WorkflowVersion:
        """Return a workflow version by identifier."""

    async def get_latest_version(self, workflow_id: UUID) -> WorkflowVersion:
        """Return the most recently created version for the workflow."""

    async def diff_versions(
        self, workflow_id: UUID, base_version: int, target_version: int
    ) -> VersionDiff:
        """Compute a diff between two workflow versions."""

    async def create_run(
        self,
        workflow_id: UUID,
        *,
        workflow_version_id: UUID,
        triggered_by: str,
        input_payload: dict[str, Any],
        actor: str | None = None,
        runnable_config: dict[str, Any] | None = None,
        tenant_id: str | None = None,
    ) -> WorkflowRun:
        """Create a workflow run for the specified version."""

    async def list_runs_for_workflow(
        self,
        workflow_id: UUID,
        *,
        limit: int | None = None,
        tenant_id: str | None = None,
    ) -> list[WorkflowRun]:
        """Return runs associated with the given workflow.

        Results are ordered most-recent-first.  When *limit* is given only the
        most recent *limit* runs are returned.  When *tenant_id* is provided
        results are limited to that tenant.
        """

    async def get_run(
        self,
        run_id: UUID,
        *,
        tenant_id: str | None = None,
    ) -> WorkflowRun:
        """Return a workflow run by identifier.

        When *tenant_id* is provided the run is only returned when it belongs
        to that tenant; otherwise WorkflowRunNotFoundError is raised.
        """

    async def mark_run_started(self, run_id: UUID, *, actor: str) -> WorkflowRun:
        """Transition a run into the running state."""

    async def mark_run_succeeded(
        self,
        run_id: UUID,
        *,
        actor: str,
        output: dict[str, Any] | None,
    ) -> WorkflowRun:
        """Mark a run as successfully completed."""

    async def mark_run_failed(
        self,
        run_id: UUID,
        *,
        actor: str,
        error: str,
    ) -> WorkflowRun:
        """Mark a run as failed with the provided error."""

    async def mark_run_cancelled(
        self,
        run_id: UUID,
        *,
        actor: str,
        reason: str | None,
    ) -> WorkflowRun:
        """Cancel a run with an optional reason."""

    async def reset(self) -> None:
        """Clear all stored workflows, versions, and runs."""

    async def configure_webhook_trigger(
        self, workflow_id: UUID, config: WebhookTriggerConfig
    ) -> WebhookTriggerConfig:
        """Store webhook trigger configuration for a workflow."""

    async def get_webhook_trigger_config(
        self, workflow_id: UUID
    ) -> WebhookTriggerConfig:
        """Return the webhook trigger configuration for a workflow."""

    async def handle_webhook_trigger(
        self,
        workflow_id: UUID,
        *,
        method: str,
        headers: Mapping[str, str],
        query_params: Mapping[str, str],
        payload: Any,
        source_ip: str | None,
    ) -> WorkflowRun:
        """Handle an inbound webhook event by enqueuing a run."""

    async def configure_cron_trigger(
        self, workflow_id: UUID, config: CronTriggerConfig
    ) -> CronTriggerConfig:
        """Persist cron trigger configuration for a workflow."""

    async def get_cron_trigger_config(self, workflow_id: UUID) -> CronTriggerConfig:
        """Return the cron trigger configuration for a workflow."""

    async def delete_cron_trigger(self, workflow_id: UUID) -> None:
        """Remove cron trigger configuration for a workflow."""

    async def dispatch_due_cron_runs(
        self, *, now: datetime | None = None
    ) -> list[WorkflowRun]:
        """Dispatch runs for cron triggers that are due at the given time."""

    async def dispatch_manual_runs(
        self, request: ManualDispatchRequest
    ) -> list[WorkflowRun]:
        """Dispatch manual runs according to the provided request."""

    async def configure_retry_policy(
        self, workflow_id: UUID, config: RetryPolicyConfig
    ) -> RetryPolicyConfig:
        """Persist retry policy configuration for a workflow."""

    async def get_retry_policy_config(self, workflow_id: UUID) -> RetryPolicyConfig:
        """Return the retry policy configuration for a workflow."""

    async def schedule_retry_for_run(
        self, run_id: UUID, *, failed_at: datetime | None = None
    ) -> RetryDecision | None:
        """Return the next retry decision for the specified run if available."""

    async def list_listener_subscriptions(
        self,
        *,
        workflow_id: UUID | None = None,
    ) -> list[ListenerSubscription]:
        """Return listener subscriptions, optionally filtered by workflow."""

    async def get_listener_subscription(
        self,
        subscription_id: UUID,
    ) -> ListenerSubscription:
        """Return a single listener subscription by identifier."""

    async def claim_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
        lease_seconds: int,
    ) -> ListenerSubscription | None:
        """Lease the subscription for a runtime if it is currently available."""

    async def release_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
    ) -> ListenerSubscription | None:
        """Release runtime ownership for a leased subscription."""

    async def get_listener_cursor(
        self,
        subscription_id: UUID,
    ) -> ListenerCursor | None:
        """Return the persisted cursor for a subscription if one exists."""

    async def save_listener_cursor(
        self,
        cursor: ListenerCursor,
    ) -> ListenerCursor:
        """Persist cursor progress for the subscription."""

    async def dispatch_listener_event(
        self,
        subscription_id: UUID,
        payload: ListenerDispatchPayload,
    ) -> WorkflowRun | None:
        """Dispatch a normalized listener event into the run queue."""

    async def update_listener_subscription_status(
        self,
        subscription_id: UUID,
        *,
        status: ListenerSubscriptionStatus,
        actor: str,
        last_error: str | None = None,
    ) -> ListenerSubscription:
        """Update the operational status for a listener subscription."""


__all__ = ["WorkflowRepository", "VersionDiff"]
