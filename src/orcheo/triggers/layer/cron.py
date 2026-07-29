"""Cron trigger helpers for the trigger layer."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import UUID
from orcheo.triggers.cron import CronTriggerConfig, CronTriggerState
from orcheo.triggers.layer.models import CronDispatchPlan
from orcheo.triggers.layer.state import TriggerLayerState


class CronTriggerMixin(TriggerLayerState):
    """Provide cron trigger orchestration helpers."""

    def configure_cron(
        self,
        workflow_id: UUID,
        config: CronTriggerConfig,
        *,
        last_dispatched_at: datetime | None = None,
    ) -> CronTriggerConfig:
        """Persist cron configuration for the workflow and return a copy.

        Args:
            workflow_id: The workflow identifier.
            config: The cron trigger configuration.
            last_dispatched_at: Optional datetime of the last successful dispatch.
                Used to correctly compute the next fire time after worker restarts.
        """
        existing_state = self._cron_states.get(workflow_id)
        if existing_state is None:
            state = CronTriggerState(config, last_dispatched_at=last_dispatched_at)
            self._cron_states[workflow_id] = state
        else:
            existing_state.update_config(config, last_dispatched_at=last_dispatched_at)
            state = existing_state
        return state.config

    def get_cron_config(self, workflow_id: UUID) -> CronTriggerConfig | None:
        """Return the stored cron configuration or None if not configured."""
        state = self._cron_states.get(workflow_id)
        if state is None:
            return None
        return state.config

    def get_cron_last_dispatched_at(self, workflow_id: UUID) -> datetime | None:
        """Return the last dispatch time for the workflow or None if not set."""
        state = self._cron_states.get(workflow_id)
        if state is None:
            return None
        return state.last_dispatched_at

    def remove_cron_config(self, workflow_id: UUID) -> bool:
        """Remove cron configuration state for the workflow if present."""
        removed = self._cron_states.pop(workflow_id, None) is not None
        if removed:
            self._cron_run_index = {
                run_id: stored_workflow_id
                for run_id, stored_workflow_id in self._cron_run_index.items()
                if stored_workflow_id != workflow_id
            }
        return removed

    def collect_due_cron_dispatches(self, *, now: datetime) -> list[CronDispatchPlan]:
        """Return cron dispatch plans that are due at the provided reference time."""
        if now is None:
            raise ValueError("now parameter cannot be None")

        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        plans: list[CronDispatchPlan] = []
        for workflow_id, state in self._cron_states.items():
            try:
                if self._health_guard and not self._health_guard.is_workflow_healthy(
                    workflow_id
                ):
                    continue
                due_at = state.peek_due(now=now)
                if due_at is None or not state.can_dispatch():
                    continue
                plans.append(
                    CronDispatchPlan(
                        workflow_id=workflow_id,
                        scheduled_for=due_at,
                        timezone=state.config.timezone,
                    )
                )
            except Exception as exc:  # pragma: no cover - logging path
                self._logger.error(
                    "Error checking cron dispatch for workflow %s: %s",
                    workflow_id,
                    exc,
                )
                continue
        return plans

    def commit_cron_dispatch(self, workflow_id: UUID) -> None:
        """Advance the cron schedule after a run has been enqueued."""
        if workflow_id is None:
            raise ValueError("workflow_id cannot be None")

        state = self._cron_states.get(workflow_id)
        if state is None:
            self._logger.warning(
                "Cannot commit cron dispatch for workflow %s: no cron state found",
                workflow_id,
            )
            return

        try:
            state.consume_due()
        except Exception as exc:  # pragma: no cover - logging path
            self._logger.error(
                "Failed to commit cron dispatch for workflow %s: %s",
                workflow_id,
                exc,
            )
            raise

    def register_cron_run(self, run_id: UUID) -> None:
        """Register a cron-triggered run so overlap guards are enforced."""
        workflow_id = self._run_workflows.get(run_id)
        if workflow_id is None:
            self._logger.warning(
                "Cannot register cron run %s: workflow not tracked",
                run_id,
            )
            return

        self._cron_run_index[run_id] = workflow_id

        state = self._cron_states.get(workflow_id)
        if state is None:
            self._logger.warning(
                "Cannot register cron run %s: no cron state for workflow %s",
                run_id,
                workflow_id,
            )
            return

        try:
            state.register_run(run_id)
        except Exception:
            self._cron_run_index.pop(run_id, None)
            raise

    def release_cron_run(self, run_id: UUID) -> None:
        """Release overlap tracking for the provided cron run."""
        workflow_id = self._cron_run_index.pop(run_id, None)
        if workflow_id is None:
            return
        state = self._cron_states.get(workflow_id)
        if state is not None:
            state.release_run(run_id)

    def sync_cron_active_runs(self, workflow_id: UUID, run_ids: set[UUID]) -> None:
        """Reconcile a workflow's cron overlap tracking against persisted state.

        See CronTriggerState.sync_active_runs for why this periodic
        reconciliation (rather than relying solely on register/release
        calls) is necessary when dispatch and execution can happen in
        different worker processes.
        """
        state = self._cron_states.get(workflow_id)
        if state is None:
            return
        state.sync_active_runs(run_ids)
        self._cron_run_index = {
            stored_run_id: stored_workflow_id
            for stored_run_id, stored_workflow_id in self._cron_run_index.items()
            if stored_workflow_id != workflow_id
        }
        for run_id in run_ids:
            self._cron_run_index[run_id] = workflow_id


__all__ = ["CronTriggerMixin"]
