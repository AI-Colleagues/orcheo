"""Tests for the listener supervisor."""

from __future__ import annotations
import asyncio
import pytest
from orcheo.listeners import ListenerHealthSnapshot, ListenerSupervisor
from orcheo.listeners.models import ListenerSubscriptionStatus
from orcheo.models import WorkflowDraftAccess
from orcheo.runtime.credentials import CredentialReferenceNotFoundError
from orcheo_backend.app.repository import InMemoryWorkflowRepository


class StubAdapter:
    def __init__(self, subscription) -> None:
        self.subscription = subscription
        self.started = asyncio.Event()

    async def run(self, stop_event: asyncio.Event) -> None:
        self.started.set()
        await stop_event.wait()

    def health(self) -> ListenerHealthSnapshot:
        return ListenerHealthSnapshot(
            subscription_id=self.subscription.id,
            runtime_id="runtime-1",
            status="healthy",
            platform=self.subscription.platform,
        )


def _listener_graph(*listeners: dict[str, object]) -> dict[str, object]:
    return {"nodes": [], "edges": [], "index": {"listeners": list(listeners)}}


@pytest.mark.asyncio()
async def test_listener_supervisor_claims_and_releases_subscriptions() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Supervisor Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener",
                "platform": "telegram",
                "token": "[[telegram_one]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime-1",
        adapter_factory=lambda subscription: StubAdapter(subscription),
        reconcile_interval_seconds=0.01,
    )

    await supervisor.run_once()
    assert len(supervisor.health()) == 1
    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    assert subscription.assigned_runtime == "runtime-1"

    await supervisor.shutdown()
    released = await repository.get_listener_subscription(subscription.id)
    assert released.assigned_runtime is None


@pytest.mark.asyncio()
async def test_listener_supervisor_retries_blocked_listeners_and_recovers() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Supervisor Failure Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener",
                "platform": "telegram",
                "token": "[[telegram_one]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )

    attempts = 0

    def adapter_factory(subscription):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            msg = "Credential 'telegram_one' was not found in the configured vault"
            raise CredentialReferenceNotFoundError(msg)
        return StubAdapter(subscription)

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime-1",
        adapter_factory=adapter_factory,
        reconcile_interval_seconds=0.01,
    )

    await supervisor.run_once()
    assert supervisor.health() == []

    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    assert subscription.status == ListenerSubscriptionStatus.BLOCKED
    assert "telegram_one" in (subscription.last_error or "")
    assert subscription.assigned_runtime is None

    await supervisor.run_once()
    recovered = await repository.get_listener_subscription(subscription.id)
    assert recovered.status == ListenerSubscriptionStatus.ACTIVE
    assert recovered.assigned_runtime == "runtime-1"
    assert recovered.last_error is None
    assert attempts == 2

    await supervisor.shutdown()


@pytest.mark.asyncio()
async def test_listener_supervisor_tracks_consecutive_adapter_failures() -> None:
    repository = InMemoryWorkflowRepository()
    workflow = await repository.create_workflow(
        name="Supervisor Failure Flow",
        slug=None,
        description=None,
        tags=None,
        draft_access=WorkflowDraftAccess.PERSONAL,
        actor="author",
    )
    await repository.create_version(
        workflow.id,
        graph=_listener_graph(
            {
                "node_name": "telegram_listener",
                "platform": "telegram",
                "token": "[[telegram_one]]",
            }
        ),
        metadata={},
        notes=None,
        created_by="author",
    )

    class FailingAdapterFactory:
        """Adapter factory that always raises to trigger the error path."""

        def __init__(self) -> None:
            self.attempts = 0

        def __call__(self, subscription) -> None:
            self.attempts += 1
            raise RuntimeError("Adapter build failure")

    factory = FailingAdapterFactory()

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime-1",
        adapter_factory=factory,
        reconcile_interval_seconds=0.01,
    )

    await supervisor.run_once()
    first_health = supervisor.health()
    assert len(first_health) == 1
    assert first_health[0].status == "error"
    assert first_health[0].consecutive_failures == 1
    assert "Adapter build failure" in (first_health[0].detail or "")
    assert factory.attempts == 1

    await supervisor.run_once()
    second_health = supervisor.health()
    assert len(second_health) == 1
    assert second_health[0].consecutive_failures == 2
    assert factory.attempts == 2

    subscription = (
        await repository.list_listener_subscriptions(workflow_id=workflow.id)
    )[0]
    assert subscription.assigned_runtime is None

    await supervisor.shutdown()
