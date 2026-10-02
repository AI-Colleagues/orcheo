"""Listener supervisor for long-lived private bot subscriptions."""

from __future__ import annotations
import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID
from orcheo.listeners import (
    ListenerHealthSnapshot,
    ListenerSubscription,
    ListenerSubscriptionStatus,
)
from orcheo.runtime.credentials import CredentialReferenceNotFoundError


logger = logging.getLogger(__name__)


class ListenerAdapter(Protocol):
    """Runtime adapter contract managed by the listener supervisor."""

    subscription: ListenerSubscription

    async def run(self, stop_event: asyncio.Event) -> None:
        """Run the adapter until cancelled or ``stop_event`` is set."""

    def health(self) -> ListenerHealthSnapshot:
        """Return the current adapter health snapshot."""


AdapterFactory = Callable[[ListenerSubscription], ListenerAdapter]


class ListenerRepository(Protocol):
    """Repository operations required by the listener supervisor."""

    async def list_listener_subscriptions(
        self,
    ) -> list[ListenerSubscription]:
        """Return all persisted listener subscriptions."""

    async def claim_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
        lease_seconds: int,
    ) -> ListenerSubscription | None:
        """Claim a listener subscription for the current runtime."""

    async def release_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
    ) -> ListenerSubscription | None:
        """Release a previously claimed listener subscription."""

    async def update_listener_subscription_status(
        self,
        subscription_id: UUID,
        *,
        status: ListenerSubscriptionStatus,
        actor: str,
        last_error: str | None = None,
    ) -> ListenerSubscription:
        """Update the operational status for a listener subscription."""


class ListenerSupervisor:
    """Claim subscriptions, manage adapter lifecycles, and expose health."""

    def __init__(
        self,
        *,
        repository: ListenerRepository,
        runtime_id: str,
        adapter_factory: AdapterFactory,
        lease_seconds: int = 60,
        reconcile_interval_seconds: float = 5.0,
    ) -> None:
        """Initialize a supervisor for a single worker runtime."""
        self._repository = repository
        self._runtime_id = runtime_id
        self._adapter_factory = adapter_factory
        self._lease_seconds = lease_seconds
        self._reconcile_interval_seconds = reconcile_interval_seconds
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._adapters: dict[UUID, ListenerAdapter] = {}
        self._build_failures: dict[UUID, ListenerHealthSnapshot] = {}
        self._stop_events: dict[UUID, asyncio.Event] = {}

    async def run_once(self) -> None:
        """Reconcile active subscriptions with the currently running adapters."""
        subscriptions = await self._repository.list_listener_subscriptions()
        active_ids: set[UUID] = set()
        for subscription in subscriptions:
            claimed, recovered_adapter = await self._prepare_subscription(subscription)
            if claimed is None:
                continue
            active_ids.add(claimed.id)
            if claimed.id in self._tasks:
                self._build_failures.pop(claimed.id, None)
                continue
            stop_event = asyncio.Event()
            try:
                adapter = recovered_adapter or self._adapter_factory(claimed)
            except CredentialReferenceNotFoundError as exc:
                logger.warning(
                    "Blocking listener subscription %s until credentials are "
                    "configured: %s",
                    claimed.id,
                    exc,
                )
                await self._repository.update_listener_subscription_status(
                    claimed.id,
                    status=ListenerSubscriptionStatus.BLOCKED,
                    actor=self._runtime_id,
                    last_error=str(exc),
                )
                self._build_failures.pop(claimed.id, None)
                continue
            except Exception as exc:
                logger.exception(
                    "Failed to build listener adapter for subscription %s",
                    claimed.id,
                )
                self._record_build_failure(claimed, exc)
                await self._repository.release_listener_subscription(
                    claimed.id,
                    runtime_id=self._runtime_id,
                )
                continue
            task = asyncio.create_task(
                self._run_adapter(claimed.id, adapter, stop_event)
            )
            self._stop_events[claimed.id] = stop_event
            self._adapters[claimed.id] = adapter
            self._tasks[claimed.id] = task
            self._build_failures.pop(claimed.id, None)

        stale_ids = set(self._tasks) - active_ids
        for subscription_id in stale_ids:
            await self._stop_adapter(subscription_id)
        for subscription_id in set(self._build_failures) - active_ids:
            self._build_failures.pop(subscription_id, None)

    async def _prepare_subscription(
        self,
        subscription: ListenerSubscription,
    ) -> tuple[ListenerSubscription | None, ListenerAdapter | None]:
        """Return a claimable subscription plus any prebuilt adapter."""
        if subscription.status == ListenerSubscriptionStatus.BLOCKED:
            return await self._recover_blocked_subscription(subscription)
        if subscription.status != ListenerSubscriptionStatus.ACTIVE:
            return None, None
        if (
            subscription.id in self._tasks
            and subscription.assigned_runtime == self._runtime_id
            and subscription.lease_expires_at is not None
            and subscription.lease_expires_at
            > datetime.now(UTC) + timedelta(seconds=self._lease_seconds / 2)
        ):
            return subscription, None
        claimed = await self._repository.claim_listener_subscription(
            subscription.id,
            runtime_id=self._runtime_id,
            lease_seconds=self._lease_seconds,
        )
        return claimed, None

    async def _recover_blocked_subscription(
        self,
        subscription: ListenerSubscription,
    ) -> tuple[ListenerSubscription | None, ListenerAdapter | None]:
        """Promote a blocked subscription back to active once credentials resolve."""
        try:
            adapter = self._adapter_factory(subscription)
        except CredentialReferenceNotFoundError as exc:
            logger.warning(
                "Listener subscription %s is still blocked on credentials: %s",
                subscription.id,
                exc,
            )
            if subscription.last_error != str(exc):  # pragma: no branch
                await self._repository.update_listener_subscription_status(
                    subscription.id,
                    status=ListenerSubscriptionStatus.BLOCKED,
                    actor=self._runtime_id,
                    last_error=str(exc),
                )
            self._build_failures.pop(subscription.id, None)
            return None, None
        except Exception as exc:
            logger.exception(
                "Failed to re-evaluate blocked listener subscription %s",
                subscription.id,
            )
            self._record_build_failure(subscription, exc)
            return None, None
        await self._repository.update_listener_subscription_status(
            subscription.id,
            status=ListenerSubscriptionStatus.ACTIVE,
            actor=self._runtime_id,
        )
        claimed = await self._repository.claim_listener_subscription(
            subscription.id,
            runtime_id=self._runtime_id,
            lease_seconds=self._lease_seconds,
        )
        return claimed, adapter

    def _record_build_failure(
        self,
        subscription: ListenerSubscription,
        exc: Exception,
    ) -> None:
        """Record a build failure snapshot for one subscription."""
        previous_failure = self._build_failures.get(subscription.id)
        self._build_failures[subscription.id] = ListenerHealthSnapshot(
            subscription_id=subscription.id,
            runtime_id=self._runtime_id,
            status="error",
            platform=subscription.platform,
            last_event_at=subscription.last_event_at,
            consecutive_failures=(
                previous_failure.consecutive_failures + 1
                if previous_failure is not None
                else 1
            ),
            detail=str(exc),
        )

    async def serve(self, stop_event: asyncio.Event | None = None) -> None:
        """Continuously reconcile subscriptions until asked to stop."""
        controller = stop_event or asyncio.Event()
        while not controller.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(
                    controller.wait(),
                    timeout=self._reconcile_interval_seconds,
                )
            except TimeoutError:
                continue
        await self.shutdown()

    async def shutdown(self) -> None:
        """Gracefully stop all adapters and release their claims."""
        for subscription_id in list(self._tasks):
            await self._stop_adapter(subscription_id)

    def health(self) -> list[ListenerHealthSnapshot]:
        """Return the health snapshot for all active adapters."""
        return [
            *self._build_failures.values(),
            *(adapter.health() for adapter in self._adapters.values()),
        ]

    async def _run_adapter(
        self,
        subscription_id: UUID,
        adapter: ListenerAdapter,
        stop_event: asyncio.Event,
    ) -> None:
        try:
            await adapter.run(stop_event)
        finally:
            await self._repository.release_listener_subscription(
                subscription_id,
                runtime_id=self._runtime_id,
            )
            self._tasks.pop(subscription_id, None)
            self._adapters.pop(subscription_id, None)
            self._stop_events.pop(subscription_id, None)

    async def _stop_adapter(self, subscription_id: UUID) -> None:
        stop_event = self._stop_events.get(subscription_id)
        if stop_event is not None:
            stop_event.set()
        task = self._tasks.get(subscription_id)
        if task is None:
            return
        await task


__all__ = ["ListenerAdapter", "ListenerSupervisor"]
