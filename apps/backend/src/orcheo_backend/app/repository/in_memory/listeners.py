"""Listener subscription persistence for the in-memory repository."""

from __future__ import annotations
from datetime import timedelta
from uuid import UUID
from orcheo.listeners import (
    ListenerCursor,
    ListenerDedupeRecord,
    ListenerDispatchPayload,
    ListenerSubscription,
    ListenerSubscriptionStatus,
    compile_listener_subscriptions,
)
from orcheo.models import WorkflowRun
from orcheo.models.base import _utcnow
from orcheo_backend.app.repository.errors import (
    WorkflowNotFoundError,
    WorkflowVersionNotFoundError,
)
from orcheo_backend.app.repository.in_memory.state import InMemoryRepositoryState


class ListenerRepositoryMixin(InMemoryRepositoryState):
    """Manage listener subscriptions, cursors, and dispatch state."""

    def _disable_listener_subscriptions_locked(
        self,
        workflow_id: UUID,
        *,
        actor: str,
    ) -> None:
        """Disable all active subscriptions for a workflow."""
        subscription_ids = self._workflow_listener_subscriptions.get(workflow_id, [])
        for subscription_id in subscription_ids:
            existing = self._listener_subscriptions.get(subscription_id)
            if existing is None:
                continue
            if existing.status == ListenerSubscriptionStatus.DISABLED:
                continue
            existing.status = ListenerSubscriptionStatus.DISABLED
            existing.assigned_runtime = None
            existing.lease_expires_at = None
            existing.record_event(actor=actor, action="listener_subscription_disabled")

    def _sync_listener_subscriptions_locked(
        self,
        workflow_id: UUID,
        workflow_version_id: UUID,
        graph: dict[str, object],
        *,
        actor: str,
    ) -> None:
        self._disable_listener_subscriptions_locked(workflow_id, actor=actor)

        compiled = compile_listener_subscriptions(
            workflow_id, workflow_version_id, graph
        )
        self._workflow_listener_subscriptions[workflow_id] = []
        for subscription in compiled:
            subscription.record_event(
                actor=actor, action="listener_subscription_synced"
            )
            self._listener_subscriptions[subscription.id] = subscription
            self._workflow_listener_subscriptions[workflow_id].append(subscription.id)

    async def list_listener_subscriptions(
        self,
        *,
        workflow_id: UUID | None = None,
    ) -> list[ListenerSubscription]:
        """Return all listener subscriptions, optionally scoped to one workflow."""
        async with self._lock:
            if workflow_id is None:
                return [
                    subscription.model_copy(deep=True)
                    for subscription in self._listener_subscriptions.values()
                ]
            return [
                self._listener_subscriptions[subscription_id].model_copy(deep=True)
                for subscription_id in self._workflow_listener_subscriptions.get(
                    workflow_id, []
                )
                if subscription_id in self._listener_subscriptions
            ]

    async def get_listener_subscription(
        self,
        subscription_id: UUID,
    ) -> ListenerSubscription:
        """Return a single listener subscription by identifier."""
        async with self._lock:
            subscription = self._listener_subscriptions.get(subscription_id)
            if subscription is None:
                raise WorkflowNotFoundError(str(subscription_id))
            return subscription.model_copy(deep=True)

    async def claim_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
        lease_seconds: int,
    ) -> ListenerSubscription | None:
        """Claim a listener subscription if it is available for this runtime."""
        async with self._lock:
            subscription = self._listener_subscriptions.get(subscription_id)
            if subscription is None:
                return None
            now = _utcnow()
            if subscription.status != ListenerSubscriptionStatus.ACTIVE:
                return None
            if (
                subscription.assigned_runtime
                and subscription.assigned_runtime != runtime_id
                and subscription.lease_expires_at is not None
                and subscription.lease_expires_at > now
            ):
                return None
            should_record_claim = subscription.assigned_runtime != runtime_id
            subscription.assigned_runtime = runtime_id
            subscription.lease_expires_at = now + timedelta(seconds=lease_seconds)
            if should_record_claim:
                subscription.record_event(
                    actor=runtime_id,
                    action="listener_subscription_claimed",
                )
            return subscription.model_copy(deep=True)

    async def release_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
    ) -> ListenerSubscription | None:
        """Release a listener subscription currently claimed by this runtime."""
        async with self._lock:
            subscription = self._listener_subscriptions.get(subscription_id)
            if subscription is None or subscription.assigned_runtime != runtime_id:
                return None
            subscription.assigned_runtime = None
            subscription.lease_expires_at = None
            subscription.record_event(
                actor=runtime_id,
                action="listener_subscription_released",
            )
            return subscription.model_copy(deep=True)

    async def get_listener_cursor(
        self,
        subscription_id: UUID,
    ) -> ListenerCursor | None:
        """Return the persisted cursor for a listener subscription."""
        async with self._lock:
            cursor = self._listener_cursors.get(subscription_id)
            return cursor.model_copy(deep=True) if cursor is not None else None

    async def save_listener_cursor(
        self,
        cursor: ListenerCursor,
    ) -> ListenerCursor:
        """Persist a listener cursor snapshot."""
        async with self._lock:
            stored = cursor.model_copy(deep=True)
            stored.updated_at = _utcnow()
            self._listener_cursors[cursor.subscription_id] = stored
            return stored.model_copy(deep=True)

    async def dispatch_listener_event(
        self,
        subscription_id: UUID,
        payload: ListenerDispatchPayload,
    ) -> WorkflowRun | None:
        """Dispatch a deduplicated listener event into the workflow run queue."""
        async with self._lock:
            subscription = self._listener_subscriptions.get(subscription_id)
            if subscription is None:
                raise WorkflowNotFoundError(str(subscription_id))
            if subscription.status != ListenerSubscriptionStatus.ACTIVE:
                return None

            version = self._versions.get(subscription.workflow_version_id)
            if version is None:
                raise WorkflowVersionNotFoundError(
                    str(subscription.workflow_version_id)
                )

            now = _utcnow()
            window = int(subscription.config.get("dedupe_window_seconds", 300))
            existing_records = self._listener_dedupe.setdefault(subscription_id, {})
            expired = [
                key
                for key, record in existing_records.items()
                if record.expires_at <= now
            ]
            for key in expired:
                existing_records.pop(key, None)
            if payload.dedupe_key in existing_records:
                return None

            existing_records[payload.dedupe_key] = ListenerDedupeRecord(
                subscription_id=subscription_id,
                dedupe_key=payload.dedupe_key,
                expires_at=now + timedelta(seconds=window),
            )

            subscription.last_event_at = now
            subscription.last_error = None
            workspace_id = self._workflow_workspaces.get(subscription.workflow_id)
            run = self._create_run_locked(
                workflow_id=subscription.workflow_id,
                workflow_version_id=version.id,
                triggered_by="listener",
                input_payload=payload.model_copy(
                    update={"listener_subscription_id": subscription_id}
                ).to_input_payload(),
                actor="listener",
                workspace_id=workspace_id,
            )
            return run.model_copy(deep=True)

    async def update_listener_subscription_status(
        self,
        subscription_id: UUID,
        *,
        status: ListenerSubscriptionStatus,
        actor: str,
        last_error: str | None = None,
    ) -> ListenerSubscription:
        """Update the operational status for a listener subscription."""
        async with self._lock:
            subscription = self._listener_subscriptions.get(subscription_id)
            if subscription is None:
                raise WorkflowNotFoundError(str(subscription_id))
            subscription.status = status
            subscription.assigned_runtime = None
            subscription.lease_expires_at = None
            if status == ListenerSubscriptionStatus.ACTIVE:
                subscription.last_error = None
            elif last_error is not None:
                subscription.last_error = last_error
            subscription.record_event(
                actor=actor,
                action=f"listener_subscription_status_{status.value}",
            )
            return subscription.model_copy(deep=True)

    async def sync_listener_subscriptions_for_version(
        self,
        workflow_id: UUID,
        workflow_version_id: UUID,
        graph: dict[str, object],
        *,
        actor: str,
    ) -> None:
        """Compile and replace subscriptions for a workflow version."""
        async with self._lock:
            self._sync_listener_subscriptions_locked(
                workflow_id, workflow_version_id, graph, actor=actor
            )


__all__ = ["ListenerRepositoryMixin"]
