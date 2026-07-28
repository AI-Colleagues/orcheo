"""PostgreSQL-backed Hosted Apps repository."""

from __future__ import annotations
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from orcheo.hosted_apps.errors import (
    AliasConflictError,
    AliasTombstonedError,
    HostedAppsDisabledError,
)
from orcheo.hosted_apps.models import (
    AliasLifecycle,
    AppAlias,
    AppBinding,
    AppCollection,
    AppDeployment,
    AppRelease,
    DeploymentStatus,
    HostedApp,
    ModerationBlock,
    PlatformAuditEvent,
    PublicationState,
    RuntimeGeneration,
    normalize_alias,
)
from orcheo.hosted_apps.postgres_schema import POSTGRES_HOSTED_APPS_SCHEMA
from orcheo.models.base import _utcnow


__all__ = ["PostgresHostedAppsRepository"]


def _json_payload(value: Any) -> Any:
    """Return decoded JSON for real and lightweight fake database rows."""
    if isinstance(value, str):
        return json.loads(value)
    return value


class PostgresHostedAppsRepository:  # pragma: no cover
    """Persistent Hosted Apps metadata store backed by PostgreSQL."""

    def __init__(
        self,
        dsn: str,
        *,
        alias_tombstone_days: int = 30,
        ensure_schema: bool = True,
    ) -> None:
        """Configure the database connection and initialize additive schema."""
        self._dsn = dsn
        self._alias_tombstone_days = alias_tombstone_days
        self._pool = ConnectionPool(
            conninfo=dsn,
            min_size=0,
            max_size=10,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        if ensure_schema:
            self._ensure_schema()

    @contextmanager
    def _connect(self) -> Iterator[Connection[Any]]:
        with self._pool.connection() as connection:
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(POSTGRES_HOSTED_APPS_SCHEMA)

    @property
    def dsn(self) -> str:
        """Return the configured DSN for colocated durable runtime adapters."""
        return self._dsn

    def close(self) -> None:
        """Close pooled PostgreSQL connections during controlled shutdown."""
        self._pool.close()

    @staticmethod
    def _app_from_row(row: Mapping[str, Any]) -> HostedApp:
        payload = dict(row)
        payload["external_origins"] = _json_payload(payload["external_origins"])
        return HostedApp(**payload)

    @staticmethod
    def _alias_from_row(row: Mapping[str, Any]) -> AppAlias:
        return AppAlias(**dict(row))

    @staticmethod
    def _deployment_from_row(row: Mapping[str, Any]) -> AppDeployment:
        payload = dict(row)
        if "app_manifest" in payload:
            payload["app_manifest"] = _json_payload(payload["app_manifest"])
        return AppDeployment(**payload)

    @staticmethod
    def _binding_from_row(row: Mapping[str, Any]) -> AppBinding:
        payload = dict(row)
        for field in (
            "runnable_config_snapshot",
            "input_schema",
            "output_projection",
            "limits",
        ):
            payload[field] = _json_payload(payload[field])
        return AppBinding(**payload)

    @staticmethod
    def _collection_from_row(row: Mapping[str, Any]) -> AppCollection:
        return AppCollection(**dict(row))

    @staticmethod
    def _release_from_row(row: Mapping[str, Any]) -> AppRelease:
        payload = dict(row)
        payload["capability_snapshot"] = _json_payload(payload["capability_snapshot"])
        payload["csp_snapshot"] = _json_payload(payload["csp_snapshot"])
        return AppRelease(**payload)

    @staticmethod
    def _moderation_from_row(row: Mapping[str, Any]) -> ModerationBlock:
        return ModerationBlock(**dict(row))

    @staticmethod
    def _audit_from_row(row: Mapping[str, Any]) -> PlatformAuditEvent:
        payload = dict(row)
        payload["metadata"] = _json_payload(payload["metadata"])
        return PlatformAuditEvent(**payload)

    @staticmethod
    def _runtime_from_row(row: Mapping[str, Any]) -> RuntimeGeneration:
        payload = dict(row)
        payload.pop("singleton", None)
        return RuntimeGeneration(**payload)

    @staticmethod
    def _insert_audit(
        conn: Connection[Any],
        action: str,
        actor: str,
        target_kind: str,
        target_id: str,
        *,
        reason_code: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> PlatformAuditEvent:
        event = PlatformAuditEvent(
            action=action,
            actor=actor,
            target_kind=target_kind,
            target_id=target_id,
            reason_code=reason_code,
            metadata=metadata or {},
        )
        conn.execute(
            """
            INSERT INTO hosted_app_platform_audit_events (
                id, action, actor, target_kind, target_id, reason_code,
                metadata, idempotency_key, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event.id,
                event.action,
                event.actor,
                event.target_kind,
                event.target_id,
                event.reason_code,
                Jsonb(event.metadata),
                event.idempotency_key,
                event.created_at,
            ),
        )
        return event

    @classmethod
    def _insert_app_audit(
        cls,
        conn: Connection[Any],
        action: str,
        actor: str,
        app: HostedApp,
    ) -> PlatformAuditEvent:
        return cls._insert_audit(
            conn,
            action,
            actor,
            "app",
            str(app.id),
            metadata={"workspace_id": str(app.workspace_id)},
        )

    @staticmethod
    def _insert_app(conn: Connection[Any], app: HostedApp) -> None:
        conn.execute(
            """
            INSERT INTO hosted_apps (
                id, workspace_id, name, description, visibility,
                publication_state, is_archived, active_release_id,
                permission_revision, published_permission_revision,
                external_origins, suspended_at, suspended_reason, suspended_by,
                created_by, created_at, updated_at, published_at, archived_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                app.id,
                app.workspace_id,
                app.name,
                app.description,
                app.visibility.value,
                app.publication_state.value,
                app.is_archived,
                app.active_release_id,
                app.permission_revision,
                app.published_permission_revision,
                Jsonb(list(app.external_origins)),
                app.suspended_at,
                app.suspended_reason,
                app.suspended_by,
                app.created_by,
                app.created_at,
                app.updated_at,
                app.published_at,
                app.archived_at,
            ),
        )

    @staticmethod
    def _update_app_row(conn: Connection[Any], app: HostedApp) -> None:
        cursor = conn.execute(
            """
            UPDATE hosted_apps
               SET name = %s,
                   description = %s,
                   visibility = %s,
                   publication_state = %s,
                   is_archived = %s,
                   active_release_id = %s,
                   permission_revision = %s,
                   published_permission_revision = %s,
                   external_origins = %s,
                   suspended_at = %s,
                   suspended_reason = %s,
                   suspended_by = %s,
                   created_by = %s,
                   created_at = %s,
                   updated_at = %s,
                   published_at = %s,
                   archived_at = %s
             WHERE workspace_id = %s
               AND id = %s
            """,
            (
                app.name,
                app.description,
                app.visibility.value,
                app.publication_state.value,
                app.is_archived,
                app.active_release_id,
                app.permission_revision,
                app.published_permission_revision,
                Jsonb(list(app.external_origins)),
                app.suspended_at,
                app.suspended_reason,
                app.suspended_by,
                app.created_by,
                app.created_at,
                app.updated_at,
                app.published_at,
                app.archived_at,
                app.workspace_id,
                app.id,
            ),
        )
        if cursor.rowcount == 0:
            raise KeyError("Hosted app was not found in this workspace.")

    @classmethod
    def _get_app_locked(
        cls, conn: Connection[Any], workspace_id: UUID, app_id: UUID
    ) -> HostedApp:
        row = conn.execute(
            """
            SELECT *
              FROM hosted_apps
             WHERE workspace_id = %s
               AND id = %s
             FOR UPDATE
            """,
            (workspace_id, app_id),
        ).fetchone()
        if row is None:
            raise KeyError("Hosted app was not found in this workspace.")
        return cls._app_from_row(row)

    def create_app_with_alias(self, app: HostedApp, alias: str) -> AppAlias:
        """Atomically persist an app and its globally unique initial alias."""
        reservation = AppAlias(
            alias=alias,
            app_id=app.id,
            workspace_id=app.workspace_id,
            reserved_kind=AliasLifecycle.APP,
        )
        try:
            with self._connect() as conn:
                existing_app = conn.execute(
                    "SELECT 1 FROM hosted_apps WHERE id = %s FOR UPDATE",
                    (app.id,),
                ).fetchone()
                if existing_app is not None:
                    raise ValueError("Hosted app already exists.")
                existing_alias = conn.execute(
                    "SELECT * FROM hosted_app_aliases WHERE alias = %s FOR UPDATE",
                    (reservation.alias,),
                ).fetchone()
                if existing_alias is not None:
                    current = self._alias_from_row(existing_alias)
                    if (
                        current.reserved_kind is AliasLifecycle.TOMBSTONE
                        and current.tombstoned_until is not None
                        and current.tombstoned_until <= _utcnow()
                    ):
                        conn.execute(
                            "DELETE FROM hosted_app_aliases WHERE alias = %s",
                            (reservation.alias,),
                        )
                    elif current.reserved_kind is AliasLifecycle.TOMBSTONE:
                        raise AliasTombstonedError(
                            "App alias is temporarily tombstoned."
                        )
                    else:
                        raise AliasConflictError("App alias is already reserved.")
                self._insert_app_audit(conn, "app.create", app.created_by, app)
                self._insert_app(conn, app)
                conn.execute(
                    """
                    INSERT INTO hosted_app_aliases (
                        alias, app_id, workspace_id, reserved_kind,
                        tombstoned_until, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        reservation.alias,
                        reservation.app_id,
                        reservation.workspace_id,
                        reservation.reserved_kind.value,
                        reservation.tombstoned_until,
                        reservation.created_at,
                        reservation.updated_at,
                    ),
                )
        except UniqueViolation as exc:
            constraint = exc.diag.constraint_name or ""
            if "hosted_apps_pkey" in constraint:
                raise ValueError("Hosted app already exists.") from exc
            raise AliasConflictError("App alias is already reserved.") from exc
        return reservation.model_copy(deep=True)

    def get_app(self, workspace_id: UUID, app_id: UUID) -> HostedApp:
        """Return an app only within its authoritative workspace scope."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM hosted_apps
                 WHERE workspace_id = %s
                   AND id = %s
                """,
                (workspace_id, app_id),
            ).fetchone()
        if row is None:
            raise KeyError("Hosted app was not found in this workspace.")
        return self._app_from_row(row)

    def list_apps(self, workspace_id: UUID) -> list[HostedApp]:
        """List workspace apps with newest updates first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                  FROM hosted_apps
                 WHERE workspace_id = %s
                 ORDER BY updated_at DESC, id DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [self._app_from_row(row) for row in rows]

    def get_active_deployment_id(self, workspace_id: UUID, app_id: UUID) -> UUID | None:
        """Return the deployment selected by the app's active release."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT release.deployment_id
                  FROM hosted_apps AS app
             LEFT JOIN hosted_app_releases AS release
                    ON release.workspace_id = app.workspace_id
                   AND release.app_id = app.id
                   AND release.id = app.active_release_id
                 WHERE app.workspace_id = %s
                   AND app.id = %s
                """,
                (workspace_id, app_id),
            ).fetchone()
        if row is None:
            raise KeyError("Hosted app was not found in this workspace.")
        deployment_id = row["deployment_id"]
        return UUID(str(deployment_id)) if deployment_id is not None else None

    def list_apps_page(
        self,
        workspace_id: UUID,
        *,
        cursor: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[list[tuple[HostedApp, AppAlias]], bool]:
        """Fetch one bounded page and its aliases in a single query."""
        parameters: list[Any] = [workspace_id]
        cursor_clause = ""
        if cursor is not None:
            cursor_clause = "AND (app.updated_at, app.id) < (%s, %s)"
            parameters.extend(cursor)
        parameters.append(limit + 1)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT app.*,
                       alias.alias AS joined_alias,
                       alias.reserved_kind AS joined_reserved_kind,
                       alias.tombstoned_until AS joined_tombstoned_until,
                       alias.created_at AS joined_alias_created_at,
                       alias.updated_at AS joined_alias_updated_at
                  FROM hosted_apps AS app
                  JOIN hosted_app_aliases AS alias
                    ON alias.workspace_id = app.workspace_id
                   AND alias.app_id = app.id
                   AND alias.reserved_kind = 'app'
                 WHERE app.workspace_id = %s
                   {cursor_clause}
                 ORDER BY app.updated_at DESC, app.id DESC
                 LIMIT %s
                """,
                parameters,
            ).fetchall()
        has_more = len(rows) > limit
        pairs: list[tuple[HostedApp, AppAlias]] = []
        for row in rows[:limit]:
            app_payload = {
                key: value
                for key, value in row.items()
                if not key.startswith("joined_")
            }
            app = self._app_from_row(app_payload)
            alias = AppAlias(
                alias=row["joined_alias"],
                app_id=app.id,
                workspace_id=app.workspace_id,
                reserved_kind=row["joined_reserved_kind"],
                tombstoned_until=row["joined_tombstoned_until"],
                created_at=row["joined_alias_created_at"],
                updated_at=row["joined_alias_updated_at"],
            )
            pairs.append((app, alias))
        return pairs, has_more

    def update_app(
        self,
        app: HostedApp,
        *,
        actor: str | None = None,
        action: str = "app.update",
    ) -> HostedApp:
        """Persist an app update and its audit evidence atomically."""
        stored = app.model_copy(deep=True)
        stored.updated_at = _utcnow()
        with self._connect() as conn:
            self._get_app_locked(conn, stored.workspace_id, stored.id)
            self._insert_app_audit(conn, action, actor or stored.created_by, stored)
            self._update_app_row(conn, stored)
        return stored.model_copy(deep=True)

    def reserve_alias(
        self, app: HostedApp, alias: str, *, actor: str | None = None
    ) -> AppAlias:
        """Atomically replace an app alias and tombstone the previous one."""
        normalized = normalize_alias(alias)
        now = _utcnow()
        try:
            with self._connect() as conn:
                persisted = self._get_app_locked(conn, app.workspace_id, app.id)
                row = conn.execute(
                    "SELECT * FROM hosted_app_aliases WHERE alias = %s FOR UPDATE",
                    (normalized,),
                ).fetchone()
                if row is not None:
                    existing = self._alias_from_row(row)
                    if existing.app_id == app.id:
                        self._insert_app_audit(
                            conn,
                            "alias.reserve",
                            actor or persisted.created_by,
                            persisted,
                        )
                        return existing
                    if (
                        existing.reserved_kind is AliasLifecycle.TOMBSTONE
                        and existing.tombstoned_until is not None
                        and existing.tombstoned_until <= now
                    ):
                        conn.execute(
                            "DELETE FROM hosted_app_aliases WHERE alias = %s",
                            (normalized,),
                        )
                    elif existing.reserved_kind is AliasLifecycle.TOMBSTONE:
                        raise AliasTombstonedError(
                            "App alias is temporarily tombstoned."
                        )
                    else:
                        raise AliasConflictError("App alias is already reserved.")
                self._insert_app_audit(
                    conn,
                    "alias.reserve",
                    actor or persisted.created_by,
                    persisted,
                )
                conn.execute(
                    """
                    UPDATE hosted_app_aliases
                       SET app_id = NULL,
                           workspace_id = NULL,
                           reserved_kind = %s,
                           tombstoned_until = %s,
                           updated_at = %s
                     WHERE app_id = %s
                    """,
                    (
                        AliasLifecycle.TOMBSTONE.value,
                        now + timedelta(days=self._alias_tombstone_days),
                        now,
                        app.id,
                    ),
                )
                reservation = AppAlias(
                    alias=normalized,
                    app_id=app.id,
                    workspace_id=app.workspace_id,
                    reserved_kind=AliasLifecycle.APP,
                    created_at=now,
                    updated_at=now,
                )
                conn.execute(
                    """
                    INSERT INTO hosted_app_aliases (
                        alias, app_id, workspace_id, reserved_kind,
                        tombstoned_until, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, NULL, %s, %s)
                    """,
                    (
                        reservation.alias,
                        reservation.app_id,
                        reservation.workspace_id,
                        reservation.reserved_kind.value,
                        reservation.created_at,
                        reservation.updated_at,
                    ),
                )
        except UniqueViolation as exc:
            raise AliasConflictError("App alias is already reserved.") from exc
        return reservation

    def get_alias(self, workspace_id: UUID, app_id: UUID) -> AppAlias:
        """Return the active alias for a workspace-owned app."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT alias.*
                  FROM hosted_app_aliases AS alias
                  JOIN hosted_apps AS app
                    ON app.workspace_id = alias.workspace_id
                   AND app.id = alias.app_id
                 WHERE alias.workspace_id = %s
                   AND alias.app_id = %s
                   AND alias.reserved_kind = %s
                """,
                (workspace_id, app_id, AliasLifecycle.APP.value),
            ).fetchone()
        if row is None:
            raise KeyError("Hosted app alias was not found.")
        return self._alias_from_row(row)

    def add_deployment(self, deployment: AppDeployment) -> AppDeployment:
        """Persist an app-owned deployment candidate."""
        try:
            with self._connect() as conn:
                app = self._get_app_locked(
                    conn, deployment.workspace_id, deployment.app_id
                )
                conn.execute(
                    """
                    INSERT INTO hosted_app_deployments (
                        id, workspace_id, app_id, status, archive_sha256,
                        manifest_sha256, app_manifest, validation_error_code,
                        validation_error_message, created_by, created_at,
                        validated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        deployment.id,
                        deployment.workspace_id,
                        deployment.app_id,
                        deployment.status.value,
                        deployment.archive_sha256,
                        deployment.manifest_sha256,
                        (
                            Jsonb(deployment.app_manifest.model_dump(mode="json"))
                            if deployment.app_manifest is not None
                            else None
                        ),
                        deployment.validation_error_code,
                        deployment.validation_error_message,
                        deployment.created_by,
                        deployment.created_at,
                        deployment.validated_at,
                    ),
                )
                if (
                    deployment.status is DeploymentStatus.READY
                    and deployment.app_manifest is not None
                ):
                    now = _utcnow()
                    self._insert_app_audit(
                        conn,
                        "deployment.manifest.request",
                        deployment.created_by,
                        app,
                    )
                    conn.execute(
                        """
                        UPDATE hosted_apps
                           SET permission_revision = permission_revision + 1,
                               updated_at = %s
                         WHERE workspace_id = %s
                           AND id = %s
                        """,
                        (now, deployment.workspace_id, deployment.app_id),
                    )
        except UniqueViolation as exc:
            raise ValueError("Hosted app deployment already exists.") from exc
        return deployment.model_copy(deep=True)

    def list_deployments(self, workspace_id: UUID, app_id: UUID) -> list[AppDeployment]:
        """List app-owned deployment candidates newest first."""
        with self._connect() as conn:
            self._get_app_locked(conn, workspace_id, app_id)
            rows = conn.execute(
                """
                SELECT *
                  FROM hosted_app_deployments
                 WHERE workspace_id = %s
                   AND app_id = %s
                 ORDER BY created_at DESC
                """,
                (workspace_id, app_id),
            ).fetchall()
        return [self._deployment_from_row(row) for row in rows]

    def save_binding(self, binding: AppBinding, *, actor: str) -> AppBinding:
        """Create or replace a draft binding and invalidate reviewed policy."""
        stored = binding.model_copy(deep=True)
        stored.updated_at = _utcnow()
        try:
            with self._connect() as conn:
                app = self._get_app_locked(conn, stored.workspace_id, stored.app_id)
                existing = conn.execute(
                    """
                    SELECT workspace_id, app_id
                      FROM hosted_app_bindings
                     WHERE id = %s
                     FOR UPDATE
                    """,
                    (stored.id,),
                ).fetchone()
                if existing is not None and (
                    existing["workspace_id"] != stored.workspace_id
                    or existing["app_id"] != stored.app_id
                ):
                    raise KeyError("Hosted app binding was not found.")
                self._insert_app_audit(conn, "capability.binding.save", actor, app)
                conn.execute(
                    """
                    INSERT INTO hosted_app_bindings (
                        id, workspace_id, app_id, name, workflow_id,
                        workflow_version_id, workflow_execution_sha256,
                        runnable_config_snapshot, access_mode, input_schema,
                        output_projection, visitor_can_read_output,
                        visitor_can_read_sanitized_errors, limits, deleted_at,
                        created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE
                       SET name = EXCLUDED.name,
                           workflow_id = EXCLUDED.workflow_id,
                           workflow_version_id = EXCLUDED.workflow_version_id,
                           workflow_execution_sha256 =
                               EXCLUDED.workflow_execution_sha256,
                           runnable_config_snapshot =
                               EXCLUDED.runnable_config_snapshot,
                           access_mode = EXCLUDED.access_mode,
                           input_schema = EXCLUDED.input_schema,
                           output_projection = EXCLUDED.output_projection,
                           visitor_can_read_output =
                               EXCLUDED.visitor_can_read_output,
                           visitor_can_read_sanitized_errors =
                               EXCLUDED.visitor_can_read_sanitized_errors,
                           limits = EXCLUDED.limits,
                           deleted_at = EXCLUDED.deleted_at,
                           updated_at = EXCLUDED.updated_at
                    """,
                    (
                        stored.id,
                        stored.workspace_id,
                        stored.app_id,
                        stored.name,
                        stored.workflow_id,
                        stored.workflow_version_id,
                        stored.workflow_execution_sha256,
                        Jsonb(stored.runnable_config_snapshot),
                        stored.access_mode,
                        Jsonb(stored.input_schema),
                        Jsonb(stored.output_projection),
                        stored.visitor_can_read_output,
                        stored.visitor_can_read_sanitized_errors,
                        Jsonb(stored.limits),
                        stored.deleted_at,
                        stored.created_at,
                        stored.updated_at,
                    ),
                )
                conn.execute(
                    """
                    UPDATE hosted_apps
                       SET permission_revision = permission_revision + 1,
                           updated_at = %s
                     WHERE workspace_id = %s
                       AND id = %s
                    """,
                    (stored.updated_at, stored.workspace_id, stored.app_id),
                )
        except UniqueViolation as exc:
            raise ValueError("A live binding already uses this name.") from exc
        return stored

    def list_bindings(self, workspace_id: UUID, app_id: UUID) -> list[AppBinding]:
        """List live draft bindings for one app."""
        with self._connect() as conn:
            self._get_app_locked(conn, workspace_id, app_id)
            rows = conn.execute(
                """
                SELECT *
                  FROM hosted_app_bindings
                 WHERE workspace_id = %s
                   AND app_id = %s
                   AND deleted_at IS NULL
                 ORDER BY created_at, id
                """,
                (workspace_id, app_id),
            ).fetchall()
        return [self._binding_from_row(row) for row in rows]

    def delete_binding(
        self, workspace_id: UUID, app_id: UUID, binding_id: UUID, *, actor: str
    ) -> None:
        """Tombstone a binding and invalidate reviewed policy."""
        now = _utcnow()
        with self._connect() as conn:
            app = self._get_app_locked(conn, workspace_id, app_id)
            self._insert_app_audit(conn, "capability.binding.delete", actor, app)
            cursor = conn.execute(
                """
                UPDATE hosted_app_bindings
                   SET deleted_at = %s,
                       updated_at = %s
                 WHERE workspace_id = %s
                   AND app_id = %s
                   AND id = %s
                   AND deleted_at IS NULL
                """,
                (now, now, workspace_id, app_id, binding_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("Hosted app binding was not found.")
            conn.execute(
                """
                UPDATE hosted_apps
                   SET permission_revision = permission_revision + 1,
                       updated_at = %s
                 WHERE workspace_id = %s
                   AND id = %s
                """,
                (now, workspace_id, app_id),
            )

    def invalidate_bindings_for_workflow(
        self, workspace_id: UUID, workflow_id: UUID, *, actor: str
    ) -> int:
        """Invalidate reviewed policy for apps using a changed workflow."""
        now = _utcnow()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT app.*
                  FROM hosted_apps AS app
                  JOIN hosted_app_bindings AS binding
                    ON binding.workspace_id = app.workspace_id
                   AND binding.app_id = app.id
                 WHERE binding.workspace_id = %s
                   AND binding.workflow_id = %s
                   AND binding.deleted_at IS NULL
                 FOR UPDATE OF app
                """,
                (workspace_id, workflow_id),
            ).fetchall()
            for row in rows:
                app = self._app_from_row(row)
                self._insert_app_audit(
                    conn,
                    "capability.binding.dependency_invalidated",
                    actor,
                    app,
                )
                conn.execute(
                    """
                    UPDATE hosted_apps
                       SET permission_revision = permission_revision + 1,
                           updated_at = %s
                     WHERE workspace_id = %s
                       AND id = %s
                    """,
                    (now, workspace_id, app.id),
                )
        return len(rows)

    def save_collection(
        self, collection: AppCollection, *, actor: str
    ) -> AppCollection:
        """Create or replace a collection and invalidate reviewed policy."""
        stored = collection.model_copy(deep=True)
        stored.updated_at = _utcnow()
        try:
            with self._connect() as conn:
                app = self._get_app_locked(conn, stored.workspace_id, stored.app_id)
                existing = conn.execute(
                    """
                    SELECT workspace_id, app_id
                      FROM hosted_app_collections
                     WHERE id = %s
                     FOR UPDATE
                    """,
                    (stored.id,),
                ).fetchone()
                if existing is not None and (
                    existing["workspace_id"] != stored.workspace_id
                    or existing["app_id"] != stored.app_id
                ):
                    raise KeyError("Hosted app collection was not found.")
                self._insert_app_audit(conn, "capability.collection.save", actor, app)
                conn.execute(
                    """
                    INSERT INTO hosted_app_collections (
                        id, workspace_id, app_id, name, scope, read_access,
                        write_access, max_document_bytes, max_records,
                        deleted_at, created_at, updated_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (id) DO UPDATE
                       SET name = EXCLUDED.name,
                           scope = EXCLUDED.scope,
                           read_access = EXCLUDED.read_access,
                           write_access = EXCLUDED.write_access,
                           max_document_bytes = EXCLUDED.max_document_bytes,
                           max_records = EXCLUDED.max_records,
                           deleted_at = EXCLUDED.deleted_at,
                           updated_at = EXCLUDED.updated_at
                    """,
                    (
                        stored.id,
                        stored.workspace_id,
                        stored.app_id,
                        stored.name,
                        stored.scope,
                        stored.read_access,
                        stored.write_access,
                        stored.max_document_bytes,
                        stored.max_records,
                        stored.deleted_at,
                        stored.created_at,
                        stored.updated_at,
                    ),
                )
                conn.execute(
                    """
                    UPDATE hosted_apps
                       SET permission_revision = permission_revision + 1,
                           updated_at = %s
                     WHERE workspace_id = %s
                       AND id = %s
                    """,
                    (stored.updated_at, stored.workspace_id, stored.app_id),
                )
        except UniqueViolation as exc:
            raise ValueError("A live collection already uses this name.") from exc
        return stored

    def list_collections(self, workspace_id: UUID, app_id: UUID) -> list[AppCollection]:
        """List live collection definitions for one app."""
        with self._connect() as conn:
            self._get_app_locked(conn, workspace_id, app_id)
            rows = conn.execute(
                """
                SELECT *
                  FROM hosted_app_collections
                 WHERE workspace_id = %s
                   AND app_id = %s
                   AND deleted_at IS NULL
                 ORDER BY created_at, id
                """,
                (workspace_id, app_id),
            ).fetchall()
        return [self._collection_from_row(row) for row in rows]

    def delete_collection(
        self, workspace_id: UUID, app_id: UUID, collection_id: UUID, *, actor: str
    ) -> None:
        """Tombstone a collection and invalidate reviewed policy."""
        now = _utcnow()
        with self._connect() as conn:
            app = self._get_app_locked(conn, workspace_id, app_id)
            self._insert_app_audit(conn, "capability.collection.delete", actor, app)
            cursor = conn.execute(
                """
                UPDATE hosted_app_collections
                   SET deleted_at = %s,
                       updated_at = %s
                 WHERE workspace_id = %s
                   AND app_id = %s
                   AND id = %s
                   AND deleted_at IS NULL
                """,
                (now, now, workspace_id, app_id, collection_id),
            )
            if cursor.rowcount == 0:
                raise KeyError("Hosted app collection was not found.")
            conn.execute(
                """
                UPDATE hosted_apps
                   SET permission_revision = permission_revision + 1,
                       updated_at = %s
                 WHERE workspace_id = %s
                   AND id = %s
                """,
                (now, workspace_id, app_id),
            )

    def publish_release(self, release: AppRelease) -> HostedApp:
        """Create and select a validated immutable release atomically."""
        now = _utcnow()
        try:
            with self._connect() as conn:
                app = self._get_app_locked(conn, release.workspace_id, release.app_id)
                if app.is_archived or app.suspended_at is not None:
                    raise ValueError("Archived or suspended apps cannot be published.")
                deployment_row = conn.execute(
                    """
                    SELECT *
                      FROM hosted_app_deployments
                     WHERE workspace_id = %s
                       AND app_id = %s
                       AND id = %s
                     FOR UPDATE
                    """,
                    (
                        release.workspace_id,
                        release.app_id,
                        release.deployment_id,
                    ),
                ).fetchone()
                if deployment_row is None or (
                    self._deployment_from_row(deployment_row).status
                    is not DeploymentStatus.READY
                ):
                    raise ValueError(
                        "Release deployment is not a ready app-owned deployment."
                    )
                if release.permission_revision != app.permission_revision:
                    raise ValueError(
                        "Release must acknowledge the current permission revision."
                    )
                self._insert_app_audit(conn, "release.publish", release.created_by, app)
                conn.execute(
                    """
                    INSERT INTO hosted_app_releases (
                        id, workspace_id, app_id, deployment_id,
                        permission_revision, visibility, capability_snapshot,
                        csp_snapshot, snapshot_sha256, created_by, created_at
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        release.id,
                        release.workspace_id,
                        release.app_id,
                        release.deployment_id,
                        release.permission_revision,
                        release.visibility.value,
                        Jsonb(release.capability_snapshot),
                        Jsonb(release.csp_snapshot),
                        release.snapshot_sha256,
                        release.created_by,
                        release.created_at,
                    ),
                )
                app.visibility = release.visibility
                app.active_release_id = release.id
                app.publication_state = PublicationState.PUBLISHED
                app.published_permission_revision = release.permission_revision
                app.published_at = now
                app.updated_at = now
                self._update_app_row(conn, app)
        except UniqueViolation as exc:
            raise ValueError("Hosted app release already exists.") from exc
        return app

    def unpublish(
        self, workspace_id: UUID, app_id: UUID, *, actor: str | None = None
    ) -> HostedApp:
        """Stop delivery while retaining immutable release history."""
        with self._connect() as conn:
            app = self._get_app_locked(conn, workspace_id, app_id)
            self._insert_app_audit(
                conn, "release.unpublish", actor or app.created_by, app
            )
            app.publication_state = PublicationState.UNPUBLISHED
            app.updated_at = _utcnow()
            self._update_app_row(conn, app)
        return app

    def list_audit_events(
        self, workspace_id: UUID, app_id: UUID
    ) -> list[PlatformAuditEvent]:
        """Return app-scoped mutation evidence."""
        with self._connect() as conn:
            self._get_app_locked(conn, workspace_id, app_id)
            rows = conn.execute(
                """
                SELECT *
                  FROM hosted_app_platform_audit_events
                 WHERE target_kind = 'app'
                   AND target_id = %s
                   AND metadata ->> 'workspace_id' = %s
                 ORDER BY created_at, id
                """,
                (str(app_id), str(workspace_id)),
            ).fetchall()
        return [self._audit_from_row(row) for row in rows]

    def get_runtime_generation(self) -> RuntimeGeneration:
        """Return durable runtime availability and cache generation."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT generation, enabled, updated_by, updated_at
                  FROM hosted_app_runtime_state
                 WHERE singleton = TRUE
                """
            ).fetchone()
        if row is None:
            raise RuntimeError("Hosted Apps runtime state is unavailable.")
        return self._runtime_from_row(row)

    def resolve_descriptor(self, alias: str) -> dict[str, Any]:
        """Resolve an enabled published alias to its immutable descriptor."""
        normalized = normalize_alias(alias)
        with self._connect() as conn:
            runtime_row = conn.execute(
                """
                SELECT generation, enabled, updated_by, updated_at
                  FROM hosted_app_runtime_state
                 WHERE singleton = TRUE
                """
            ).fetchone()
            if runtime_row is None:
                raise HostedAppsDisabledError("Hosted Apps runtime is disabled.")
            runtime = self._runtime_from_row(runtime_row)
            if not runtime.enabled:
                raise HostedAppsDisabledError("Hosted Apps runtime is disabled.")
            alias_row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_aliases
                 WHERE alias = %s
                   AND reserved_kind = %s
                """,
                (normalized, AliasLifecycle.APP.value),
            ).fetchone()
            if (
                alias_row is None
                or alias_row["app_id"] is None
                or alias_row["workspace_id"] is None
            ):
                raise KeyError("Hosted app alias was not found.")
            app_row = conn.execute(
                """
                SELECT *
                  FROM hosted_apps
                 WHERE workspace_id = %s
                   AND id = %s
                """,
                (alias_row["workspace_id"], alias_row["app_id"]),
            ).fetchone()
            if app_row is None:
                raise KeyError("Hosted app alias was not found.")
            app = self._app_from_row(app_row)
            blocked = conn.execute(
                """
                SELECT 1
                  FROM hosted_app_moderation_blocks
                 WHERE lifted_at IS NULL
                   AND (
                       (target_kind = 'alias' AND target_id = %s)
                       OR (target_kind = 'app' AND target_id = %s)
                       OR (target_kind = 'workspace' AND target_id = %s)
                       OR (target_kind = 'publisher' AND target_id = %s)
                   )
                 LIMIT 1
                """,
                (
                    normalized,
                    str(app.id),
                    str(app.workspace_id),
                    app.created_by,
                ),
            ).fetchone()
            if app.suspended_at is not None or blocked is not None:
                return {
                    "alias": normalized,
                    "state": "suspended",
                    "generation": runtime.generation,
                }
            if (
                app.is_archived
                or app.publication_state is not PublicationState.PUBLISHED
                or app.active_release_id is None
            ):
                raise KeyError("Hosted app alias is not published.")
            release_row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_releases
                 WHERE workspace_id = %s
                   AND app_id = %s
                   AND id = %s
                """,
                (app.workspace_id, app.id, app.active_release_id),
            ).fetchone()
            if release_row is None:
                raise KeyError("Hosted app release was not found.")
            release = self._release_from_row(release_row)
        return {
            "alias": normalized,
            "state": "published",
            "generation": runtime.generation,
            "workspace_id": str(app.workspace_id),
            "app_id": str(app.id),
            "release_id": str(release.id),
            "deployment_id": str(release.deployment_id),
            "visibility": release.visibility.value,
            "snapshot_sha256": release.snapshot_sha256,
            "capability_snapshot": release.capability_snapshot,
        }

    def reserve_platform_alias(self, alias: str, *, actor: str) -> AppAlias:
        """Reserve a global platform alias with audit evidence."""
        reservation = AppAlias(alias=alias, reserved_kind=AliasLifecycle.PLATFORM)
        try:
            with self._connect() as conn:
                self._insert_audit(
                    conn,
                    "moderation.alias.reserve",
                    actor,
                    "alias",
                    reservation.alias,
                )
                conn.execute(
                    """
                    INSERT INTO hosted_app_aliases (
                        alias, app_id, workspace_id, reserved_kind,
                        tombstoned_until, created_at, updated_at
                    )
                    VALUES (%s, NULL, NULL, %s, NULL, %s, %s)
                    """,
                    (
                        reservation.alias,
                        reservation.reserved_kind.value,
                        reservation.created_at,
                        reservation.updated_at,
                    ),
                )
        except UniqueViolation as exc:
            raise ValueError("Hosted app alias is already reserved.") from exc
        return reservation

    def create_moderation_block(
        self,
        *,
        target_kind: str,
        target_id: str,
        reason_code: str,
        reason_detail: str | None,
        actor: str,
    ) -> ModerationBlock:
        """Create a platform moderation block with audit evidence."""
        if target_kind not in {"app", "alias", "workspace", "publisher"}:
            raise ValueError("Hosted Apps moderation target is invalid.")
        block = ModerationBlock(
            target_kind=target_kind,
            target_id=target_id,
            reason_code=reason_code,
            reason_detail=reason_detail,
            created_by=actor,
        )
        with self._connect() as conn:
            self._insert_audit(
                conn,
                "moderation.block",
                actor,
                target_kind,
                target_id,
                reason_code=reason_code,
            )
            conn.execute(
                """
                INSERT INTO hosted_app_moderation_blocks (
                    id, target_kind, target_id, reason_code, reason_detail,
                    created_by, created_at, lifted_by, lifted_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, NULL)
                """,
                (
                    block.id,
                    block.target_kind,
                    block.target_id,
                    block.reason_code,
                    block.reason_detail,
                    block.created_by,
                    block.created_at,
                ),
            )
        return block

    def lift_moderation_block(self, block_id: UUID, *, actor: str) -> ModerationBlock:
        """Lift a moderation block and retain immutable audit evidence."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_moderation_blocks
                 WHERE id = %s
                 FOR UPDATE
                """,
                (block_id,),
            ).fetchone()
            if row is None:
                raise KeyError("Hosted Apps moderation block was not found.")
            block = self._moderation_from_row(row)
            if block.lifted_at is None:
                block.lifted_by = actor
                block.lifted_at = _utcnow()
                self._insert_audit(
                    conn,
                    "moderation.reinstate",
                    actor,
                    block.target_kind,
                    block.target_id,
                    reason_code=block.reason_code,
                )
                conn.execute(
                    """
                    UPDATE hosted_app_moderation_blocks
                       SET lifted_by = %s,
                           lifted_at = %s
                     WHERE id = %s
                    """,
                    (block.lifted_by, block.lifted_at, block.id),
                )
        return block

    def lookup_alias_owner(self, alias: str) -> dict[str, str] | None:
        """Return platform-safe alias ownership metadata."""
        normalized = normalize_alias(alias)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM hosted_app_aliases WHERE alias = %s",
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        reservation = self._alias_from_row(row)
        return {
            "alias": reservation.alias,
            "kind": reservation.reserved_kind.value,
            "app_id": str(reservation.app_id) if reservation.app_id else "",
            "workspace_id": (
                str(reservation.workspace_id) if reservation.workspace_id else ""
            ),
        }

    def set_runtime_enabled(self, *, enabled: bool, actor: str) -> RuntimeGeneration:
        """Persist runtime control and increment the global cache generation."""
        with self._connect() as conn:
            self._insert_audit(
                conn,
                "runtime_generation.update",
                actor,
                "runtime",
                "global",
            )
            row = conn.execute(
                """
                UPDATE hosted_app_runtime_state
                   SET generation = generation + 1,
                       enabled = %s,
                       updated_by = %s,
                       updated_at = %s
                 WHERE singleton = TRUE
                 RETURNING generation, enabled, updated_by, updated_at
                """,
                (enabled, actor, _utcnow()),
            ).fetchone()
        if row is None:
            raise RuntimeError("Hosted Apps runtime state is unavailable.")
        return self._runtime_from_row(row)

    def assert_runtime_enabled(self, expected_generation: int | None = None) -> None:
        """Fail closed when runtime delivery is disabled or stale."""
        runtime = self.get_runtime_generation()
        if not runtime.enabled:
            raise HostedAppsDisabledError("Hosted Apps runtime is disabled.")
        if (
            expected_generation is not None
            and expected_generation != runtime.generation
        ):
            raise HostedAppsDisabledError("Hosted Apps runtime generation is stale.")
