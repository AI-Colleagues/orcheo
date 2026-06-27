"""Listener repository helpers for PostgreSQL-backed persistence."""

from __future__ import annotations
from datetime import timedelta
from typing import Any, cast
from uuid import UUID
import orcheo_backend.app.repository_postgres._triggers as trigger_module
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
from orcheo_backend.app.repository import (
    WorkflowNotFoundError,
)
from orcheo_backend.app.repository_postgres._persistence import PostgresPersistenceMixin


class ListenerRepositoryMixin(PostgresPersistenceMixin):
    """Persist listener subscriptions, cursors, and dedupe state."""

    async def _disable_listener_subscriptions_locked(
        self,
        workflow_id: UUID,
        *,
        actor: str,
        conn: Any | None = None,
    ) -> None:
        """Disable all non-disabled subscriptions for a workflow."""

        async def _disable(connection: Any) -> None:
            cursor = await connection.execute(
                """
                SELECT id, payload
                  FROM listener_subscriptions
                 WHERE workflow_id = %s
                   AND status != %s
                """,
                (str(workflow_id), ListenerSubscriptionStatus.DISABLED.value),
            )
            for row in await cursor.fetchall():
                payload = row["payload"]
                existing = (
                    ListenerSubscription.model_validate_json(payload)
                    if isinstance(payload, str)
                    else ListenerSubscription.model_validate(payload)
                )
                existing.status = ListenerSubscriptionStatus.DISABLED
                existing.assigned_runtime = None
                existing.lease_expires_at = None
                existing.record_event(
                    actor=actor, action="listener_subscription_disabled"
                )
                await connection.execute(
                    """
                    UPDATE listener_subscriptions
                       SET status = %s,
                           assigned_runtime = NULL,
                           lease_expires_at = NULL,
                           payload = %s,
                           updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        existing.status.value,
                        self._dump_listener_subscription(existing),
                        existing.updated_at,
                        row["id"],
                    ),
                )

        if conn is not None:
            await _disable(conn)
            return

        async with self._connection() as connection:
            await _disable(connection)

    async def _replace_listener_subscriptions_locked(
        self,
        workflow_id: UUID,
        subscriptions: list[ListenerSubscription],
        *,
        actor: str,
    ) -> None:
        async with self._connection() as conn:
            await self._disable_listener_subscriptions_locked(
                workflow_id,
                actor=actor,
                conn=conn,
            )

            for subscription in subscriptions:
                subscription.record_event(
                    actor=actor, action="listener_subscription_synced"
                )
                await conn.execute(
                    """
                    INSERT INTO listener_subscriptions (
                        id, workflow_id, workflow_version_id, node_name, platform,
                        bot_identity_key, status, assigned_runtime, lease_expires_at,
                        last_event_at, last_error, payload, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        workflow_version_id = EXCLUDED.workflow_version_id,
                        node_name = EXCLUDED.node_name,
                        platform = EXCLUDED.platform,
                        bot_identity_key = EXCLUDED.bot_identity_key,
                        status = EXCLUDED.status,
                        assigned_runtime = EXCLUDED.assigned_runtime,
                        lease_expires_at = EXCLUDED.lease_expires_at,
                        last_event_at = EXCLUDED.last_event_at,
                        last_error = EXCLUDED.last_error,
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        str(subscription.id),
                        str(subscription.workflow_id),
                        str(subscription.workflow_version_id),
                        subscription.node_name,
                        subscription.platform,
                        subscription.bot_identity_key,
                        subscription.status.value,
                        subscription.assigned_runtime,
                        subscription.lease_expires_at,
                        subscription.last_event_at,
                        subscription.last_error,
                        self._dump_listener_subscription(subscription),
                        subscription.created_at,
                        subscription.updated_at,
                    ),
                )

    async def list_listener_subscriptions(
        self,
        *,
        workflow_id: UUID | None = None,
    ) -> list[ListenerSubscription]:
        await self._ensure_initialized()
        async with self._lock:
            query = "SELECT payload FROM listener_subscriptions"
            params: tuple[str, ...] = ()
            if workflow_id is not None:
                query += " WHERE workflow_id = %s"
                params = (str(workflow_id),)
            query += " ORDER BY created_at ASC"
            async with self._connection() as conn:
                cursor = await conn.execute(query, params)
                rows = await cursor.fetchall()
            result: list[ListenerSubscription] = []
            for row in rows:
                payload = row["payload"]
                model = (
                    ListenerSubscription.model_validate_json(payload)
                    if isinstance(payload, str)
                    else ListenerSubscription.model_validate(payload)
                )
                result.append(model.model_copy(deep=True))
            return result

    async def get_listener_subscription(
        self,
        subscription_id: UUID,
    ) -> ListenerSubscription:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT payload
                      FROM listener_subscriptions
                     WHERE id = %s
                     FOR UPDATE
                    """,
                    (str(subscription_id),),
                )
                row = await cursor.fetchone()
            if row is None:
                raise WorkflowNotFoundError(str(subscription_id))
            payload = row["payload"]
            model = (
                ListenerSubscription.model_validate_json(payload)
                if isinstance(payload, str)
                else ListenerSubscription.model_validate(payload)
            )
            return model.model_copy(deep=True)

    async def claim_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
        lease_seconds: int,
    ) -> ListenerSubscription | None:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT payload
                      FROM listener_subscriptions
                     WHERE id = %s
                     FOR UPDATE
                    """,
                    (str(subscription_id),),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                payload = row["payload"]
                subscription = (
                    ListenerSubscription.model_validate_json(payload)
                    if isinstance(payload, str)
                    else ListenerSubscription.model_validate(payload)
                )
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
                cursor = await conn.execute(
                    """
                    UPDATE listener_subscriptions
                       SET assigned_runtime = %s, lease_expires_at = %s, payload = %s,
                           updated_at = %s
                     WHERE id = %s
                       AND status = %s
                       AND (
                           assigned_runtime IS NULL
                           OR assigned_runtime = %s
                           OR lease_expires_at IS NULL
                           OR lease_expires_at <= %s
                       )
                    RETURNING id
                    """,
                    (
                        runtime_id,
                        subscription.lease_expires_at,
                        self._dump_listener_subscription(subscription),
                        subscription.updated_at,
                        str(subscription_id),
                        ListenerSubscriptionStatus.ACTIVE.value,
                        runtime_id,
                        now,
                    ),
                )
                if await cursor.fetchone() is None:
                    return None
                return subscription.model_copy(deep=True)

    async def release_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
    ) -> ListenerSubscription | None:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT payload FROM listener_subscriptions WHERE id = %s",
                    (str(subscription_id),),
                )
                row = await cursor.fetchone()
                if row is None:
                    return None
                payload = row["payload"]
                subscription = (
                    ListenerSubscription.model_validate_json(payload)
                    if isinstance(payload, str)
                    else ListenerSubscription.model_validate(payload)
                )
                if subscription.assigned_runtime != runtime_id:
                    return None
                subscription.assigned_runtime = None
                subscription.lease_expires_at = None
                subscription.record_event(
                    actor=runtime_id,
                    action="listener_subscription_released",
                )
                await conn.execute(
                    """
                    UPDATE listener_subscriptions
                       SET assigned_runtime = NULL, lease_expires_at = NULL,
                           payload = %s, updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        self._dump_listener_subscription(subscription),
                        subscription.updated_at,
                        str(subscription_id),
                    ),
                )
                return subscription.model_copy(deep=True)

    async def get_listener_cursor(
        self,
        subscription_id: UUID,
    ) -> ListenerCursor | None:
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT payload FROM listener_cursors WHERE subscription_id = %s",
                    (str(subscription_id),),
                )
                row = await cursor.fetchone()
            if row is None:
                return None
            payload = row["payload"]
            model = (
                ListenerCursor.model_validate_json(payload)
                if isinstance(payload, str)
                else ListenerCursor.model_validate(payload)
            )
            return model.model_copy(deep=True)

    async def save_listener_cursor(
        self,
        cursor: ListenerCursor,
    ) -> ListenerCursor:
        await self._ensure_initialized()
        async with self._lock:
            stored = cursor.model_copy(deep=True)
            stored.updated_at = _utcnow()
            async with self._connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO listener_cursors (subscription_id, payload, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT(subscription_id) DO UPDATE SET
                        payload = EXCLUDED.payload,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (
                        str(stored.subscription_id),
                        self._dump_listener_cursor(stored),
                        stored.updated_at,
                    ),
                )
            return stored.model_copy(deep=True)

    async def dispatch_listener_event(
        self,
        subscription_id: UUID,
        payload: ListenerDispatchPayload,
    ) -> WorkflowRun | None:
        await self._ensure_initialized()
        async with self._lock:
            now = _utcnow()
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT payload FROM listener_subscriptions WHERE id = %s",
                    (str(subscription_id),),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise WorkflowNotFoundError(str(subscription_id))
                subscription_payload = row["payload"]
                subscription = (
                    ListenerSubscription.model_validate_json(subscription_payload)
                    if isinstance(subscription_payload, str)
                    else ListenerSubscription.model_validate(subscription_payload)
                )
                if subscription.status != ListenerSubscriptionStatus.ACTIVE:
                    return None
                await conn.execute(
                    "DELETE FROM listener_dedupe WHERE expires_at <= %s",
                    (now,),
                )
                dedupe_cursor = await conn.execute(
                    """
                    SELECT 1
                      FROM listener_dedupe
                     WHERE subscription_id = %s
                       AND dedupe_key = %s
                       AND expires_at > %s
                    """,
                    (str(subscription_id), payload.dedupe_key, now),
                )
                if await dedupe_cursor.fetchone():
                    return None

                dedupe = ListenerDedupeRecord(
                    subscription_id=subscription_id,
                    dedupe_key=payload.dedupe_key,
                    expires_at=now
                    + timedelta(
                        seconds=int(
                            subscription.config.get("dedupe_window_seconds", 300)
                        )
                    ),
                )
                await conn.execute(
                    """
                    INSERT INTO listener_dedupe (
                        subscription_id, dedupe_key, payload, expires_at
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        str(subscription_id),
                        dedupe.dedupe_key,
                        self._dump_listener_dedupe(dedupe),
                        dedupe.expires_at,
                    ),
                )
                subscription.last_event_at = now
                subscription.last_error = None
                await conn.execute(
                    """
                    UPDATE listener_subscriptions
                       SET last_event_at = %s, last_error = NULL, payload = %s,
                           updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        now,
                        self._dump_listener_subscription(subscription),
                        subscription.updated_at,
                        str(subscription_id),
                    ),
                )
            version = await self._get_version_locked(subscription.workflow_version_id)
            workspace_repo = cast(trigger_module._WorkflowWorkspaceLookup, self)
            workspace_id = await workspace_repo._get_workflow_workspace_id_locked(
                subscription.workflow_id
            )
            run = await self._create_run_locked(
                workflow_id=subscription.workflow_id,
                workflow_version_id=version.id,
                triggered_by="listener",
                input_payload=payload.model_copy(
                    update={"listener_subscription_id": subscription_id}
                ).to_input_payload(),
                actor="listener",
                workspace_id=workspace_id,
            )
            run_copy = run.model_copy(deep=True)
        trigger_module._enqueue_run_for_execution(run_copy)
        return run_copy

    async def update_listener_subscription_status(
        self,
        subscription_id: UUID,
        *,
        status: ListenerSubscriptionStatus,
        actor: str,
        last_error: str | None = None,
    ) -> ListenerSubscription:
        """Update the operational status for a listener subscription."""
        await self._ensure_initialized()
        async with self._lock:
            async with self._connection() as conn:
                cursor = await conn.execute(
                    "SELECT payload FROM listener_subscriptions WHERE id = %s",
                    (str(subscription_id),),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise WorkflowNotFoundError(str(subscription_id))
                payload = row["payload"]
                subscription = (
                    ListenerSubscription.model_validate_json(payload)
                    if isinstance(payload, str)
                    else ListenerSubscription.model_validate(payload)
                )
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
                await conn.execute(
                    """
                    UPDATE listener_subscriptions
                       SET status = %s,
                           assigned_runtime = NULL,
                           lease_expires_at = NULL,
                           last_error = %s,
                           payload = %s,
                           updated_at = %s
                     WHERE id = %s
                    """,
                    (
                        subscription.status.value,
                        subscription.last_error,
                        self._dump_listener_subscription(subscription),
                        subscription.updated_at,
                        str(subscription_id),
                    ),
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
        compiled = compile_listener_subscriptions(
            workflow_id, workflow_version_id, graph
        )
        async with self._lock:
            await self._replace_listener_subscriptions_locked(
                workflow_id,
                compiled,
                actor=actor,
            )


__all__ = ["ListenerRepositoryMixin", "compile_listener_subscriptions"]
