import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
import pytest
from orcheo.listeners import (
    ListenerHealthSnapshot,
    ListenerPlatform,
    ListenerSubscription,
    ListenerSubscriptionStatus,
)
from orcheo.listeners.supervisor import ListenerSupervisor
from orcheo.runtime.credentials import CredentialReferenceNotFoundError


class StubRepository:
    def __init__(self, subscriptions: list[ListenerSubscription]) -> None:
        self.subscriptions = list(subscriptions)
        self.claim_returns: dict[UUID, ListenerSubscription | None] = {}
        self.claim_calls: list[UUID] = []
        self.release_calls: list[UUID] = []

    async def list_listener_subscriptions(self) -> list[ListenerSubscription]:
        return list(self.subscriptions)

    async def claim_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
        lease_seconds: int,
    ) -> ListenerSubscription | None:
        self.claim_calls.append(subscription_id)
        if subscription_id in self.claim_returns:
            return self.claim_returns[subscription_id]
        return next(
            (sub for sub in self.subscriptions if sub.id == subscription_id),
            None,
        )

    async def release_listener_subscription(
        self,
        subscription_id: UUID,
        *,
        runtime_id: str,
    ) -> ListenerSubscription | None:
        self.release_calls.append(subscription_id)
        return None

    async def update_listener_subscription_status(
        self,
        subscription_id: UUID,
        *,
        status: ListenerSubscriptionStatus,
        actor: str,
        last_error: str | None = None,
    ) -> ListenerSubscription:
        for subscription in self.subscriptions:
            if subscription.id == subscription_id:
                subscription.status = status
                subscription.last_error = last_error
                subscription.assigned_runtime = None
                subscription.lease_expires_at = None
                return subscription
        raise AssertionError(f"Unknown subscription {subscription_id}")


class RecordingRepository(StubRepository):
    def __init__(self, subscriptions: list[ListenerSubscription]) -> None:
        super().__init__(subscriptions)
        self.update_calls: list[tuple[ListenerSubscriptionStatus, str, str | None]] = []

    async def update_listener_subscription_status(
        self,
        subscription_id: UUID,
        *,
        status: ListenerSubscriptionStatus,
        actor: str,
        last_error: str | None = None,
    ) -> ListenerSubscription:
        self.update_calls.append((status, actor, last_error))
        return await super().update_listener_subscription_status(
            subscription_id,
            status=status,
            actor=actor,
            last_error=last_error,
        )


def create_subscription(status: ListenerSubscriptionStatus) -> ListenerSubscription:
    return ListenerSubscription(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        node_name="listener",
        platform=ListenerPlatform.TELEGRAM,
        bot_identity_key="bot",
        config={"token": "x"},
        status=status,
    )


class BlockingAdapter:
    def __init__(self, subscription: ListenerSubscription) -> None:
        self.subscription = subscription
        self.stop_event: asyncio.Event | None = None

    async def run(self, stop_event: asyncio.Event) -> None:
        self.stop_event = stop_event
        await stop_event.wait()

    def health(self) -> ListenerHealthSnapshot:
        return ListenerHealthSnapshot(
            subscription_id=self.subscription.id,
            runtime_id="runtime",
            status="healthy",
            platform=self.subscription.platform,
        )


@pytest.mark.parametrize("lease_seconds", [0, -1])
def test_listener_rejects_nonpositive_lease(lease_seconds: int) -> None:
    with pytest.raises(ValueError, match="lease_seconds must be positive"):
        ListenerSupervisor(
            repository=StubRepository([]),
            runtime_id="runtime",
            adapter_factory=BlockingAdapter,
            lease_seconds=lease_seconds,
        )


@pytest.mark.parametrize("interval", [0, -1, 30, 60, float("inf"), float("nan")])
def test_listener_rejects_unsafe_reconcile_interval(interval: float) -> None:
    with pytest.raises(ValueError, match="less than half of lease_seconds"):
        ListenerSupervisor(
            repository=StubRepository([]),
            runtime_id="runtime",
            adapter_factory=BlockingAdapter,
            reconcile_interval_seconds=interval,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [5.0, 29.0])
async def test_listener_renews_only_near_expiry_and_detects_disabled_status(
    interval: float,
) -> None:
    """A warm lease skips writes while discovery still observes status changes."""
    subscription = create_subscription(ListenerSubscriptionStatus.ACTIVE)
    subscription.assigned_runtime = "runtime"
    subscription.lease_expires_at = datetime.now(UTC) + timedelta(seconds=60)
    repository = StubRepository([subscription])
    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=BlockingAdapter,
        reconcile_interval_seconds=interval,
    )
    await supervisor.run_once()
    await asyncio.sleep(0)
    await supervisor.run_once()
    assert repository.claim_calls == [subscription.id]
    subscription.lease_expires_at = datetime.now(UTC) + timedelta(seconds=20)
    await supervisor.run_once()
    assert repository.claim_calls == [subscription.id, subscription.id]
    subscription.status = ListenerSubscriptionStatus.DISABLED
    await supervisor.run_once()
    assert supervisor.health() == []
    assert repository.release_calls == [subscription.id]


@pytest.mark.asyncio
async def test_run_once_skips_non_active_subscriptions() -> None:
    subscription = create_subscription(ListenerSubscriptionStatus.PAUSED)
    repository = StubRepository([subscription])
    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=lambda subscription: BlockingAdapter(subscription),
    )
    await supervisor.run_once()
    assert repository.claim_calls == []


@pytest.mark.asyncio
async def test_run_once_claim_returns_none_does_not_build() -> None:
    subscription = create_subscription(ListenerSubscriptionStatus.ACTIVE)
    repository = StubRepository([subscription])
    repository.claim_returns[subscription.id] = None
    factory_calls = 0

    def factory(subscription: ListenerSubscription) -> BlockingAdapter:
        nonlocal factory_calls
        factory_calls += 1
        return BlockingAdapter(subscription)

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=factory,
    )
    await supervisor.run_once()
    assert factory_calls == 0


@pytest.mark.asyncio
async def test_run_once_reuses_existing_tasks() -> None:
    subscription = create_subscription(ListenerSubscriptionStatus.ACTIVE)
    repository = StubRepository([subscription])
    factory_calls = 0
    adapters: list[BlockingAdapter] = []

    def factory(subscription: ListenerSubscription) -> BlockingAdapter:
        nonlocal factory_calls
        factory_calls += 1
        adapter = BlockingAdapter(subscription)
        adapters.append(adapter)
        return adapter

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=factory,
    )
    await supervisor.run_once()
    await asyncio.sleep(0)
    await supervisor.run_once()
    assert factory_calls == 1
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_run_once_stops_stale_adapters_and_releases() -> None:
    subscription = create_subscription(ListenerSubscriptionStatus.ACTIVE)
    repository = StubRepository([subscription])
    adapters: list[BlockingAdapter] = []

    def factory(subscription: ListenerSubscription) -> BlockingAdapter:
        adapter = BlockingAdapter(subscription)
        adapters.append(adapter)
        return adapter

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=factory,
    )
    await supervisor.run_once()
    await asyncio.sleep(0)
    repository.subscriptions.clear()
    await supervisor.run_once()
    assert adapters[0].stop_event is not None and adapters[0].stop_event.is_set()
    assert repository.release_calls and repository.release_calls[-1] == subscription.id
    await supervisor.shutdown()


@pytest.mark.asyncio
async def test_build_failures_cleared_when_not_active() -> None:
    repository = StubRepository([])
    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=lambda subscription: BlockingAdapter(subscription),
    )
    snapshot = ListenerHealthSnapshot(
        subscription_id=uuid4(),
        runtime_id="runtime",
        status="error",
        platform=ListenerPlatform.TELEGRAM,
    )
    supervisor._build_failures[snapshot.subscription_id] = snapshot
    await supervisor.run_once()
    assert not supervisor._build_failures


@pytest.mark.asyncio
async def test_serve_handles_timeouts_between_runs() -> None:
    repository = StubRepository([])
    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=lambda subscription: BlockingAdapter(subscription),
        reconcile_interval_seconds=0.001,
    )
    stop_event = asyncio.Event()
    task = asyncio.create_task(supervisor.serve(stop_event))
    await asyncio.sleep(0.02)
    stop_event.set()
    await task
    assert task.done()


@pytest.mark.asyncio
async def test_stop_adapter_returns_when_no_task() -> None:
    repository = StubRepository([])
    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=lambda subscription: BlockingAdapter(subscription),
    )
    await supervisor._stop_adapter(uuid4())


@pytest.mark.asyncio
async def test_recover_blocked_subscription_updates_blocked_status() -> None:
    subscription = create_subscription(ListenerSubscriptionStatus.BLOCKED)
    subscription.last_error = "previous"
    repository = StubRepository([subscription])

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=lambda _: (_ for _ in ()).throw(
            CredentialReferenceNotFoundError("missing secret")
        ),
    )

    result = await supervisor._recover_blocked_subscription(subscription)
    assert result == (None, None)
    assert subscription.status == ListenerSubscriptionStatus.BLOCKED
    assert subscription.last_error == "missing secret"


@pytest.mark.asyncio
async def test_recover_blocked_subscription_records_status_update() -> None:
    subscription = create_subscription(ListenerSubscriptionStatus.BLOCKED)
    subscription.last_error = "old error"
    repository = RecordingRepository([subscription])

    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=lambda _: (_ for _ in ()).throw(
            CredentialReferenceNotFoundError("missing secret")
        ),
    )

    result = await supervisor._recover_blocked_subscription(subscription)
    assert result == (None, None)
    assert repository.update_calls == [
        (ListenerSubscriptionStatus.BLOCKED, "runtime", "missing secret")
    ]


@pytest.mark.asyncio
async def test_recover_blocked_subscription_records_build_failure() -> None:
    subscription = create_subscription(ListenerSubscriptionStatus.BLOCKED)
    repository = StubRepository([subscription])
    supervisor = ListenerSupervisor(
        repository=repository,
        runtime_id="runtime",
        adapter_factory=lambda _: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await supervisor._recover_blocked_subscription(subscription)
    failure = supervisor._build_failures[subscription.id]
    assert failure.consecutive_failures == 1
    assert "boom" in (failure.detail or "")
