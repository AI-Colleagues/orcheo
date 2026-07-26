"""Durable PostgreSQL adapter for app-scoped workflow runtime state."""

from __future__ import annotations
import hashlib
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from orcheo.hosted_apps.models import AppBinding
from orcheo.hosted_apps.runtime import (
    AppRuntimeConflictError,
    AppRuntimeError,
    AppRuntimeLimitError,
    AppRuntimeResult,
    _canonical_json,
    _hash,
    _hash_bytes,
    _project_output,
    _validate_schema,
)
from orcheo.models.base import _utcnow


__all__ = ["PostgresAppRuntimeService"]


class PostgresAppRuntimeService:
    """Persist handles, idempotency, results, and quota leases transactionally."""

    def __init__(
        self,
        dsn: str,
        *,
        handle_ttl_seconds: int = 3600,
        max_input_bytes: int = 256 * 1024,
        max_output_bytes: int = 256 * 1024,
    ) -> None:
        """Initialize the durable adapter with bounded runtime defaults."""
        self._dsn = dsn
        self._handle_ttl = handle_ttl_seconds
        self._max_input_bytes = max_input_bytes
        self._max_output_bytes = max_output_bytes
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=0,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )

    @contextmanager
    def _connect(self) -> Iterator[Connection[Any]]:
        with self._pool.connection() as connection:
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def close(self) -> None:
        """Close pooled runtime connections during controlled shutdown."""
        self._pool.close()

    def accept(
        self,
        binding: AppBinding,
        *,
        workspace_id: UUID,
        app_id: UUID,
        release_id: UUID,
        deployment_id: UUID,
        binding_snapshot_sha256: str,
        payload: Any,
        idempotency_key: str,
        runtime_generation: int,
        visitor_user_id: str | None,
        session_id: UUID | None,
        anonymous_visitor_id: str | None,
        workflow_run_id: UUID | None = None,
        client_ip: str | None = None,
    ) -> AppRuntimeResult:
        """Authorize and durably accept one idempotent binding invocation."""
        if (
            binding.workspace_id != workspace_id
            or binding.app_id != app_id
            or binding.deleted_at is not None
        ):
            raise AppRuntimeError("Workflow binding is unavailable.")
        if binding.access_mode == "authenticated" and (
            visitor_user_id is None or session_id is None
        ):
            raise AppRuntimeError("This workflow binding requires authentication.")
        if not idempotency_key or len(idempotency_key.encode()) > 256:
            raise AppRuntimeError("A bounded Idempotency-Key is required.")

        encoded = _canonical_json(payload)
        input_limit = min(
            self._max_input_bytes,
            binding.limits.get("input_max_bytes", self._max_input_bytes),
        )
        if len(encoded) > input_limit:
            raise AppRuntimeError("Workflow input exceeds the configured byte limit.")
        _validate_schema(payload, binding.input_schema)

        if session_id is not None:
            owner = str(session_id)
        elif anonymous_visitor_id:
            owner = f"anonymous:{anonymous_visitor_id}"
        else:
            raise AppRuntimeError("Anonymous visitor identity is required.")
        scope_hash = _hash(
            "|".join(
                [
                    str(app_id),
                    str(release_id),
                    str(binding.id),
                    owner,
                    idempotency_key,
                ]
            )
        )
        request_hash = _hash_bytes(encoded)
        now = _utcnow()
        expires_at = now + timedelta(seconds=self._handle_ttl)
        timeout_at = now + timedelta(
            seconds=binding.limits.get("timeout_seconds", self._handle_ttl)
        )

        with self._connect() as conn:
            self._lock_scope(conn, f"idempotency:{scope_hash}")
            conn.execute(
                "DELETE FROM hosted_app_idempotency WHERE expires_at <= %s",
                (now,),
            )
            existing = conn.execute(
                """
                SELECT idem.request_hash, run.*
                  FROM hosted_app_idempotency AS idem
                  JOIN hosted_app_runtime_runs AS run
                    ON run.public_handle = idem.public_handle
                 WHERE idem.scope_hash = %s
                   AND idem.expires_at > %s
                """,
                (scope_hash, now),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise AppRuntimeConflictError(
                        "Idempotency-Key was already used for a different request."
                    )
                return self._visitor_result(existing)

            concurrency_lease_ids = self._reserve_limits(
                conn,
                binding,
                workspace_id=workspace_id,
                app_id=app_id,
                session_id=session_id,
                client_ip=client_ip,
                now=now,
                timeout_at=timeout_at,
            )
            handle = secrets.token_urlsafe(32)
            run_id = uuid4()
            conn.execute(
                """
                INSERT INTO hosted_app_runtime_runs (
                    id, public_handle, workspace_id, app_id, release_id,
                    deployment_id, binding_id, binding_snapshot_sha256,
                    workflow_run_id, visitor_user_id, originating_session_id,
                    idempotency_key_hash, status, runtime_generation,
                    can_read_output, can_read_error, output_projection,
                    max_output_bytes, timeout_at, quota_lease_ids, expires_at,
                    created_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'accepted', %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    run_id,
                    handle,
                    workspace_id,
                    app_id,
                    release_id,
                    deployment_id,
                    binding.id,
                    binding_snapshot_sha256,
                    workflow_run_id or uuid4(),
                    visitor_user_id,
                    session_id,
                    _hash(idempotency_key),
                    runtime_generation,
                    binding.visitor_can_read_output,
                    binding.visitor_can_read_sanitized_errors,
                    Jsonb(binding.output_projection),
                    min(
                        self._max_output_bytes,
                        binding.limits.get("output_max_bytes", self._max_output_bytes),
                    ),
                    timeout_at,
                    Jsonb(
                        {
                            "concurrency": [
                                str(lease_id) for lease_id in concurrency_lease_ids
                            ]
                        }
                    ),
                    expires_at,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO hosted_app_idempotency (
                    id, scope_hash, request_hash, public_handle, expires_at,
                    created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (uuid4(), scope_hash, request_hash, handle, expires_at, now),
            )
        return AppRuntimeResult(
            handle=handle,
            status="accepted",
            newly_accepted=True,
        )

    def status(
        self,
        handle: str,
        *,
        workspace_id: UUID,
        app_id: UUID,
        runtime_generation: int,
        visitor_user_id: str | None,
        session_id: UUID | None,
    ) -> AppRuntimeResult:
        """Return a durable result only within its app and visitor scope."""
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_runtime_runs
                 WHERE public_handle = %s
                   AND workspace_id = %s
                   AND app_id = %s
                   AND runtime_generation = %s
                   AND expires_at > %s
                 FOR UPDATE
                """,
                (handle, workspace_id, app_id, runtime_generation, now),
            ).fetchone()
            if row is None:
                raise AppRuntimeError("Workflow run is unavailable.")
            if row["originating_session_id"] is not None and (
                session_id != row["originating_session_id"]
                or visitor_user_id != row["visitor_user_id"]
            ):
                raise AppRuntimeError("Workflow run is unavailable.")
            row = self._settle_timeout(conn, row, now)
            return self._visitor_result(row)

    def complete(
        self,
        handle: str,
        *,
        output: Any | None = None,
        error: str | None = None,
    ) -> None:
        """Settle a durable run and release its concurrency lease."""
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_runtime_runs
                 WHERE public_handle = %s
                 FOR UPDATE
                """,
                (handle,),
            ).fetchone()
            if row is None:
                raise KeyError("App runtime handle was not found.")
            if row["status"] not in {"accepted", "running"}:
                return
            if row["timeout_at"] is not None and row["timeout_at"] <= now:
                self._mark_failed(conn, row, "Workflow execution timed out.", now=now)
                return
            if error is not None:
                self._mark_failed(conn, row, "Workflow execution failed.", now=now)
                return
            projection = _json_value(row["output_projection"])
            projected = _project_output(output, projection)
            if len(_canonical_json(projected)) > row["max_output_bytes"]:
                self._mark_failed(
                    conn,
                    row,
                    "Workflow output exceeded the configured byte limit.",
                    now=now,
                )
                return
            conn.execute(
                """
                UPDATE hosted_app_runtime_runs
                   SET status = 'completed',
                       output = %s,
                       error = NULL
                 WHERE id = %s
                """,
                (Jsonb(projected), row["id"]),
            )
            self._release_concurrency(conn, row, now)

    def cancel(self, handle: str) -> None:
        """Cancel a durable accepted run and release its concurrency lease."""
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_runtime_runs
                 WHERE public_handle = %s
                 FOR UPDATE
                """,
                (handle,),
            ).fetchone()
            if row is None:
                raise KeyError("App runtime handle was not found.")
            if row["status"] in {"accepted", "running"}:
                conn.execute(
                    """
                    UPDATE hosted_app_runtime_runs
                       SET status = 'cancelled'
                     WHERE id = %s
                    """,
                    (row["id"],),
                )
                self._release_concurrency(conn, row, now)

    def _reserve_limits(
        self,
        conn: Connection[Any],
        binding: AppBinding,
        *,
        workspace_id: UUID,
        app_id: UUID,
        session_id: UUID | None,
        client_ip: str | None,
        now: datetime,
        timeout_at: datetime,
    ) -> list[UUID]:
        specs: list[tuple[str, int, datetime, bool]] = []
        app_limit = binding.limits.get("per_app_per_minute")
        if app_limit is not None:
            specs.append(
                (f"runtime:app:{app_id}", app_limit, now + timedelta(minutes=1), False)
            )
        ip_limit = binding.limits.get("per_ip_per_minute")
        if ip_limit is not None:
            if not client_ip:
                raise AppRuntimeLimitError(
                    "Client identity required for invocation governance."
                )
            ip_hash = hashlib.sha256(client_ip.encode()).hexdigest()
            specs.append(
                (
                    f"runtime:ip:{app_id}:{ip_hash}",
                    ip_limit,
                    now + timedelta(minutes=1),
                    False,
                )
            )
        session_limit = binding.limits.get("per_session_per_minute")
        if session_limit is not None and session_id is not None:
            specs.append(
                (
                    f"runtime:session:{app_id}:{session_id}",
                    session_limit,
                    now + timedelta(minutes=1),
                    False,
                )
            )
        concurrency_limit = binding.limits.get("max_concurrency")
        if concurrency_limit is not None:
            specs.append(
                (
                    f"runtime:concurrency:{app_id}:{binding.id}",
                    concurrency_limit,
                    timeout_at,
                    True,
                )
            )

        concurrency_ids: list[UUID] = []
        for operation, limit, expires_at, concurrency in specs:
            self._lock_scope(conn, operation)
            conn.execute(
                """
                UPDATE hosted_app_quota_leases
                   SET released_at = %s
                 WHERE operation = %s
                   AND released_at IS NULL
                   AND expires_at <= %s
                """,
                (now, operation, now),
            )
            usage_row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS used
                  FROM hosted_app_quota_leases
                 WHERE workspace_id = %s
                   AND operation = %s
                   AND released_at IS NULL
                   AND expires_at > %s
                """,
                (workspace_id, operation, now),
            ).fetchone()
            if usage_row is None:  # pragma: no cover - aggregate always returns a row
                raise RuntimeError("Hosted Apps quota usage is unavailable.")
            used = usage_row["used"]
            if int(used) >= limit:
                message = (
                    "Workflow binding concurrency limit exceeded."
                    if concurrency
                    else "Workflow invocation rate limit exceeded."
                )
                raise AppRuntimeLimitError(message)
            lease_id = uuid4()
            conn.execute(
                """
                INSERT INTO hosted_app_quota_leases (
                    id, workspace_id, operation, amount, expires_at, created_at
                )
                VALUES (%s, %s, %s, 1, %s, %s)
                """,
                (lease_id, workspace_id, operation, expires_at, now),
            )
            if concurrency:
                concurrency_ids.append(lease_id)
        return concurrency_ids

    @staticmethod
    def _lock_scope(conn: Connection[Any], scope: str) -> None:
        conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (scope,),
        )

    def _settle_timeout(
        self,
        conn: Connection[Any],
        row: Mapping[str, Any],
        now: datetime,
    ) -> Mapping[str, Any]:
        if (
            row["timeout_at"] is not None
            and row["timeout_at"] <= now
            and row["status"] in {"accepted", "running"}
        ):
            self._mark_failed(conn, row, "Workflow execution timed out.", now=now)
            updated = dict(row)
            updated["status"] = "failed"
            updated["error"] = "Workflow execution timed out."
            return updated
        return row

    def _mark_failed(
        self,
        conn: Connection[Any],
        row: Mapping[str, Any],
        message: str,
        *,
        now: datetime,
    ) -> None:
        conn.execute(
            """
            UPDATE hosted_app_runtime_runs
               SET status = 'failed',
                   output = NULL,
                   error = %s
             WHERE id = %s
            """,
            (message, row["id"]),
        )
        self._release_concurrency(conn, row, now)

    @staticmethod
    def _release_concurrency(
        conn: Connection[Any], row: Mapping[str, Any], now: datetime
    ) -> None:
        leases = _json_value(row["quota_lease_ids"])
        concurrency = leases.get("concurrency", []) if isinstance(leases, dict) else []
        if concurrency:
            conn.execute(
                """
                UPDATE hosted_app_quota_leases
                   SET released_at = %s
                 WHERE id = ANY(%s)
                   AND released_at IS NULL
                """,
                (now, [UUID(str(value)) for value in concurrency]),
            )

    @staticmethod
    def _visitor_result(row: Mapping[str, Any]) -> AppRuntimeResult:
        return AppRuntimeResult(
            handle=row["public_handle"],
            status=row["status"],
            output=_json_value(row["output"]) if row["can_read_output"] else None,
            error=row["error"] if row["can_read_error"] else None,
        )


def _json_value(value: Any) -> Any:
    """Decode JSON values returned by real or lightweight fake connections."""
    if isinstance(value, str):
        import json

        return json.loads(value)
    return value
