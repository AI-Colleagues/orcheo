"""Cron trigger configuration, validation, and scheduling utilities."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID
from zoneinfo import ZoneInfo
from croniter import CroniterBadCronError, croniter  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CronValidationError(ValueError):
    """Base error raised when cron configuration cannot be validated."""


class CronOverlapError(RuntimeError):
    """Raised when a cron trigger would create overlapping runs."""


@dataclass(slots=True)
class CronOccurrence:
    """Single cron occurrence with precomputed schedule bounds."""

    expression: str
    timezone: ZoneInfo
    start_at: datetime | None
    end_at: datetime | None

    def _localize(self, value: datetime) -> datetime:
        return value.astimezone(self.timezone)

    def _local_naive(self, value: datetime) -> datetime:
        return self._localize(value).replace(tzinfo=None)

    def next_after(
        self, reference: datetime, *, inclusive: bool = False
    ) -> datetime | None:
        """Return the next occurrence after the provided reference."""
        local_reference = self._local_naive(reference)
        start_naive = (
            self.start_at.astimezone(self.timezone).replace(tzinfo=None)
            if self.start_at
            else None
        )
        end_naive = (
            self.end_at.astimezone(self.timezone).replace(tzinfo=None)
            if self.end_at
            else None
        )

        if start_naive and local_reference < start_naive:
            local_reference = start_naive
            inclusive = True

        if inclusive and croniter.match(self.expression, local_reference):
            candidate = local_reference
        else:
            iterator = croniter(
                self.expression,
                local_reference,
                ret_type=datetime,
                day_or=True,
            )
            candidate = iterator.get_next(datetime)

        if end_naive and candidate > end_naive:
            return None

        return candidate.replace(tzinfo=self.timezone)


class CronTriggerConfig(BaseModel):
    """Configuration describing how cron triggers should be scheduled."""

    model_config = ConfigDict(extra="forbid")

    expression: str = Field(
        default="0 * * * *",
        description=(
            "Cron expression controlling the schedule (minute hour day month weekday)."
        ),
    )
    timezone: str = Field(
        default="UTC",
        description="IANA timezone identifier used to evaluate the cron expression.",
    )
    allow_overlapping: bool = Field(
        default=False,
        description="Whether multiple cron-triggered runs may overlap in time.",
    )
    start_at: datetime | None = Field(
        default=None,
        description="Optional earliest datetime (inclusive) the schedule may fire.",
    )
    end_at: datetime | None = Field(
        default=None,
        description="Optional latest datetime (inclusive) the schedule may fire.",
    )

    @field_validator("expression")
    @classmethod
    def _validate_expression(cls, value: str) -> str:
        try:
            croniter(value)
        except CroniterBadCronError as exc:
            msg = f"Invalid cron expression: {value}"
            raise CronValidationError(msg) from exc
        return value

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except Exception as exc:
            msg = f"Unknown timezone '{value}'"
            raise CronValidationError(msg) from exc
        return value

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def _ensure_timezone_awareness(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            msg = "Cron boundaries must be timezone-aware"
            raise CronValidationError(msg)
        if value.second != 0 or value.microsecond != 0:
            msg = "Cron boundaries must align to whole minutes"
            raise CronValidationError(msg)
        return value

    @model_validator(mode="after")
    def _validate_window(self) -> CronTriggerConfig:
        if self.start_at and self.end_at and self.start_at > self.end_at:
            msg = "start_at must be earlier than end_at"
            raise CronValidationError(msg)
        return self

    _HIGH_FREQUENCY_THRESHOLD_SECONDS = 300  # 5 minutes

    @model_validator(mode="after")
    def _validate_overlap_frequency(self) -> CronTriggerConfig:
        """Reject allow_overlapping=False with high-frequency schedules.

        Cron expressions that fire more often than every 5 minutes are almost
        certainly misconfigured when combined with overlap protection, as any
        run exceeding the interval will permanently block future dispatches.
        """
        if self.allow_overlapping:
            return self
        try:
            it = croniter(self.expression)
            first = it.get_next(datetime)
            second = it.get_next(datetime)
            interval = (second - first).total_seconds()
        except Exception:
            return self
        if interval < self._HIGH_FREQUENCY_THRESHOLD_SECONDS:
            msg = (
                f"Cron expression '{self.expression}' fires every "
                f"{int(interval)}s, but allow_overlapping is disabled. "
                f"This risks permanently blocking the schedule. Either "
                f"increase the interval to at least 5 minutes or set "
                f"allow_overlapping to true."
            )
            raise CronValidationError(msg)
        return self

    def timezone_info(self) -> ZoneInfo:
        """Return the ZoneInfo instance for the configured timezone."""
        return ZoneInfo(self.timezone)

    def to_occurrence(self) -> CronOccurrence:
        """Create a schedule helper for computing future occurrences."""
        return CronOccurrence(
            expression=self.expression,
            timezone=self.timezone_info(),
            start_at=self.start_at,
            end_at=self.end_at,
        )


class CronTriggerState:
    """Holds cron trigger configuration and scheduling state."""

    def __init__(
        self,
        config: CronTriggerConfig | None = None,
        *,
        last_dispatched_at: datetime | None = None,
    ) -> None:
        """Initialize the cron trigger state with optional configuration.

        Args:
            config: The cron trigger configuration.
            last_dispatched_at: The datetime when the cron was last successfully
                dispatched. Used to compute the next fire time correctly after
                worker restarts.
        """
        self._config = (config or CronTriggerConfig()).model_copy(deep=True)
        self._occurrence = self._config.to_occurrence()
        self._next_fire_at: datetime | None = None
        self._last_dispatched_at: datetime | None = last_dispatched_at
        self._active_runs: set[UUID] = set()

    @property
    def config(self) -> CronTriggerConfig:
        """Return a deep copy of the cron trigger configuration."""
        return self._config.model_copy(deep=True)

    @property
    def last_dispatched_at(self) -> datetime | None:
        """Return the datetime when the cron was last successfully dispatched."""
        return self._last_dispatched_at

    def update_config(
        self,
        config: CronTriggerConfig,
        *,
        last_dispatched_at: datetime | None = None,
    ) -> None:
        """Replace the configuration and optionally update dispatch state.

        Args:
            config: The new cron trigger configuration.
            last_dispatched_at: If provided, updates the last dispatch time.
                If None, preserves the existing last_dispatched_at value.
        """
        self._config = config.model_copy(deep=True)
        self._occurrence = self._config.to_occurrence()
        self._next_fire_at = None
        if last_dispatched_at is not None:
            self._last_dispatched_at = last_dispatched_at

    def can_dispatch(self) -> bool:
        """Return whether a new cron run may be dispatched."""
        return self._config.allow_overlapping or not self._active_runs

    def register_run(self, run_id: UUID) -> None:
        """Register a newly created cron run, enforcing overlap rules."""
        if not self.can_dispatch():
            msg = "Cron trigger already has an active run"
            raise CronOverlapError(msg)
        self._active_runs.add(run_id)

    def release_run(self, run_id: UUID) -> None:
        """Release an active run from overlap tracking."""
        self._active_runs.discard(run_id)

    def sync_active_runs(self, run_ids: set[UUID]) -> None:
        """Replace tracked active runs with an authoritative set.

        Overlap tracking normally advances via register_run/release_run, but
        those only mutate this process's in-memory state. A cron run is
        often dispatched by one worker process and completed by another
        (e.g. Celery's prefork pool), so the release never reaches this
        copy and the trigger can appear permanently active. Callers should
        periodically resync against a persisted source of truth (e.g. the
        set of non-terminal runs for this workflow) to correct that drift.
        """
        self._active_runs = set(run_ids)

    def peek_due(self, *, now: datetime) -> datetime | None:
        """Return the next due execution time without advancing state."""
        self._ensure_next(now)
        if self._next_fire_at and self._next_fire_at <= now.astimezone(UTC):
            return self._next_fire_at
        return None

    def consume_due(self) -> datetime:
        """Advance the schedule and return the datetime that was consumed."""
        if self._next_fire_at is None:
            msg = "No scheduled run is ready to consume"
            raise CronValidationError(msg)
        fire_at = self._next_fire_at
        self._last_dispatched_at = fire_at
        next_time = self._occurrence.next_after(fire_at, inclusive=False)
        self._next_fire_at = next_time.astimezone(UTC) if next_time else None
        return fire_at

    def _ensure_next(self, now: datetime) -> None:
        if self._next_fire_at is not None:
            return
        # Priority for determining the reference time:
        # 1. last_dispatched_at - ensures we compute NEXT occurrence after last dispatch
        # 2. start_at - allows scheduling to begin from a specific time
        # 3. now - fallback for newly configured crons
        #
        # When last_dispatched_at is set, we use inclusive=False to get the NEXT
        # occurrence after that time, not the same occurrence again.
        if self._last_dispatched_at is not None:
            next_time = self._occurrence.next_after(
                self._last_dispatched_at, inclusive=False
            )
        else:
            reference = self._config.start_at or now
            next_time = self._occurrence.next_after(reference, inclusive=True)
        self._next_fire_at = next_time.astimezone(UTC) if next_time else None
