"""Trigger layer cron scheduling tests."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID, uuid4
import pytest
from orcheo.triggers import (
    CronDispatchPlan,
    CronOverlapError,
    CronTriggerConfig,
    TriggerLayer,
)


def test_cron_dispatch_and_overlap_controls() -> None:
    """Cron dispatch plans honour timezone and overlap guards."""

    workflow_id = uuid4()
    layer = TriggerLayer()
    layer.configure_cron(
        workflow_id,
        CronTriggerConfig(expression="0 9 * * *", timezone="UTC"),
    )

    reference = datetime(2025, 1, 1, 9, 0, tzinfo=UTC)
    plans = layer.collect_due_cron_dispatches(now=reference)
    assert plans == [
        CronDispatchPlan(
            workflow_id=workflow_id,
            scheduled_for=reference,
            timezone="UTC",
        )
    ]

    repeat_plans = layer.collect_due_cron_dispatches(now=reference)
    assert repeat_plans == plans

    run_id = uuid4()
    layer.track_run(workflow_id, run_id)
    layer.register_cron_run(run_id)

    with pytest.raises(CronOverlapError):
        conflicting_run = uuid4()
        layer.track_run(workflow_id, conflicting_run)
        layer.register_cron_run(conflicting_run)

    layer.commit_cron_dispatch(workflow_id)
    layer.release_cron_run(run_id)
    next_plans = layer.collect_due_cron_dispatches(
        now=datetime(2025, 1, 2, 9, 0, tzinfo=UTC)
    )
    assert next_plans[0].timezone == "UTC"


def test_get_cron_last_dispatched_at_returns_none_for_unknown_workflow() -> None:
    """Unconfigured workflows should report no last dispatch."""

    layer = TriggerLayer()
    assert layer.get_cron_last_dispatched_at(uuid4()) is None


def test_remove_cron_config_cleans_run_index() -> None:
    """Removing a cron config also drops overlap tracking entries."""

    workflow_id = uuid4()
    layer = TriggerLayer()
    layer.configure_cron(
        workflow_id,
        CronTriggerConfig(expression="0 0 * * *", timezone="UTC"),
    )

    run_id = uuid4()
    layer._cron_run_index[run_id] = workflow_id
    assert layer.remove_cron_config(workflow_id) is True
    assert run_id not in layer._cron_run_index
    assert layer._cron_states.get(workflow_id) is None

    # Removing again returns False without side effects.
    assert layer.remove_cron_config(workflow_id) is False


def test_collect_due_cron_dispatches_skips_unhealthy_workflows() -> None:
    workflow_id = uuid4()

    class Guard:
        def is_workflow_healthy(self, workflow_id: UUID) -> bool:
            return False

        def get_report(self, workflow_id: UUID):  # pragma: no cover - unused
            return None

    layer = TriggerLayer(health_guard=Guard())
    layer.configure_cron(
        workflow_id,
        CronTriggerConfig(
            expression="* * * * *", timezone="UTC", allow_overlapping=True
        ),
    )

    plans = layer.collect_due_cron_dispatches(now=datetime.now(tz=UTC))
    assert plans == []


def test_collect_due_cron_dispatches_handles_naive_datetime_and_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cron dispatch collection normalizes naive timestamps and logs failures."""

    layer = TriggerLayer()
    workflow_id = uuid4()

    class ExplodingState:
        config = CronTriggerConfig(expression="0 0 * * *", timezone="UTC")

        def peek_due(self, *, now: datetime) -> datetime:
            raise RuntimeError("boom")

        def can_dispatch(self) -> bool:
            return True

    layer._cron_states[workflow_id] = ExplodingState()
    naive_now = datetime(2025, 1, 1, 0, 0)

    with caplog.at_level("ERROR"):
        plans = layer.collect_due_cron_dispatches(now=naive_now)

    assert plans == []
    assert any("Error checking cron dispatch" in message for message in caplog.messages)


def test_commit_cron_dispatch_logs_and_reraises_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Commit failures propagate after being logged."""

    layer = TriggerLayer()
    workflow_id = uuid4()

    class FailingState:
        def consume_due(self) -> None:
            raise RuntimeError("consume failed")

    layer._cron_states[workflow_id] = FailingState()

    with caplog.at_level("ERROR"):
        with pytest.raises(RuntimeError):
            layer.commit_cron_dispatch(workflow_id)

    assert any("Failed to commit cron dispatch" in msg for msg in caplog.messages)


def test_sync_cron_active_runs_noop_for_unconfigured_workflow() -> None:
    """Syncing active runs for a workflow with no cron state is a no-op."""

    layer = TriggerLayer()
    workflow_id = uuid4()
    run_id = uuid4()

    layer.sync_cron_active_runs(workflow_id, {run_id})

    assert layer._cron_states.get(workflow_id) is None
    assert run_id not in layer._cron_run_index


def test_concurrent_access_patterns() -> None:
    """Concurrent operations don't corrupt state."""

    layer = TriggerLayer()
    workflow_id = uuid4()

    layer.configure_cron(workflow_id, CronTriggerConfig(expression="0 9 * * *"))

    run1 = uuid4()
    run2 = uuid4()

    layer.track_run(workflow_id, run1)
    layer.register_cron_run(run1)

    layer.track_run(workflow_id, run2)
    with pytest.raises(CronOverlapError):
        layer.register_cron_run(run2)

    metrics = layer.get_state_metrics()
    assert metrics["cron_run_index"] == 1
    assert metrics["retry_states"] == 2
