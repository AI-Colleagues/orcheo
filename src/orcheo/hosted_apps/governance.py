"""Atomic quota leases and explicit per-operation failure policy."""

from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID, uuid4
from orcheo.models.base import _utcnow


__all__ = ["OPERATION_POLICY", "QuotaExceededError", "QuotaLeaseManager"]


OPERATION_POLICY = {
    "static_delivery": "fail_open",
    "upload_bytes": "fail_closed",
    "data_write_bytes": "fail_closed",
    "data_write_rows": "fail_closed",
    "new_session": "fail_closed",
    "anonymous_invocation": "fail_closed",
    "authenticated_invocation": "fail_closed",
    "concurrent_run": "fail_closed",
}


class QuotaExceededError(RuntimeError):
    """Raised when an atomic reservation would exceed its configured limit."""


@dataclass(slots=True)
class _Lease:
    id: UUID
    workspace_id: UUID
    operation: str
    amount: int
    expires_at: datetime
    state: str = "reserved"


class QuotaLeaseManager:
    """Synchronized reference semantics for a Redis/Postgres lease adapter."""

    def __init__(self, limits: dict[tuple[UUID, str], int]) -> None:
        """Initialize explicit operation limits without process-default fallbacks."""
        self._limits = limits
        self._leases: dict[UUID, _Lease] = {}
        self._lock = RLock()

    def reserve(
        self,
        workspace_id: UUID,
        operation: str,
        amount: int,
        *,
        ttl_seconds: int = 300,
    ) -> UUID:
        """Atomically reserve capacity with a bounded crash-recovery expiry."""
        if operation not in OPERATION_POLICY or amount <= 0:
            raise ValueError("Hosted Apps quota reservation is invalid.")
        with self._lock:
            self.reconcile()
            limit = self._limits.get((workspace_id, operation))
            if limit is None:
                raise QuotaExceededError("Hosted Apps governance limit is unavailable.")
            used = sum(
                lease.amount
                for lease in self._leases.values()
                if lease.workspace_id == workspace_id
                and lease.operation == operation
                and lease.state in {"reserved", "committed"}
            )
            if used + amount > limit:
                raise QuotaExceededError("Hosted Apps governance limit exceeded.")
            lease_id = uuid4()
            self._leases[lease_id] = _Lease(
                id=lease_id,
                workspace_id=workspace_id,
                operation=operation,
                amount=amount,
                expires_at=_utcnow() + timedelta(seconds=ttl_seconds),
            )
            return lease_id

    def commit(self, lease_id: UUID) -> None:
        """Settle a reservation idempotently after authoritative work commits."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is None or lease.state == "released":
                raise QuotaExceededError("Hosted Apps quota lease is unavailable.")
            lease.state = "committed"

    def release(self, lease_id: UUID) -> None:
        """Release capacity idempotently after failure, cancellation, or completion."""
        with self._lock:
            lease = self._leases.get(lease_id)
            if lease is not None:
                lease.state = "released"

    def reconcile(self) -> int:
        """Release expired unsettled reservations after worker/process crashes."""
        now = _utcnow()
        reconciled = 0
        for lease in self._leases.values():
            if lease.state == "reserved" and lease.expires_at <= now:
                lease.state = "released"
                reconciled += 1
        return reconciled
