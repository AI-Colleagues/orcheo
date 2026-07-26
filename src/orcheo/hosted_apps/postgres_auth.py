"""Durable PostgreSQL adapter for Hosted Apps authorization codes and sessions."""

from __future__ import annotations
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from orcheo.hosted_apps.auth import AppAuthError, IssuedAppSession, _hash
from orcheo.hosted_apps.models import AppSession
from orcheo.models.base import _utcnow


__all__ = ["PostgresAppAuthService"]


class PostgresAppAuthService:  # pragma: no cover
    """Persist single-use authorization codes and host-bound app sessions."""

    def __init__(
        self,
        dsn: str,
        *,
        code_ttl_seconds: int = 300,
        absolute_seconds: int = 43_200,
        idle_seconds: int = 1_800,
    ) -> None:
        """Initialize bounded code and session lifetimes."""
        self._code_ttl = code_ttl_seconds
        self._absolute = absolute_seconds
        self._idle = idle_seconds
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
        """Close pooled authentication connections."""
        self._pool.close()

    def issue_code(
        self,
        *,
        app_id: UUID,
        workspace_id: UUID,
        user_id: str,
        redirect_uri: str,
        code_challenge: str,
    ) -> str:
        """Persist a hashed, short-lived authorization code."""
        raw = secrets.token_urlsafe(32)
        now = _utcnow()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO hosted_app_authorization_codes (
                    id, code_hash, workspace_id, app_id, user_id, redirect_uri,
                    code_challenge, expires_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    _hash(raw),
                    workspace_id,
                    app_id,
                    user_id,
                    redirect_uri,
                    code_challenge,
                    now + timedelta(seconds=self._code_ttl),
                    now,
                ),
            )
        return raw

    def exchange(
        self,
        *,
        raw_code: str,
        verifier: str,
        app_host: str,
        redirect_uri: str,
        runtime_generation: int,
        current_member: bool,
    ) -> IssuedAppSession:
        """Consume one PKCE code and persist an exact-host session."""
        from orcheo.hosted_apps.auth import _pkce_challenge

        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_authorization_codes
                 WHERE code_hash = %s
                 FOR UPDATE
                """,
                (_hash(raw_code),),
            ).fetchone()
            if (
                row is None
                or row["consumed_at"] is not None
                or row["expires_at"] <= now
                or row["redirect_uri"] != redirect_uri
                or not current_member
            ):
                raise AppAuthError("App authorization code is invalid or expired.")
            if not secrets.compare_digest(
                _pkce_challenge(verifier), row["code_challenge"]
            ):
                raise AppAuthError("App authorization PKCE verification failed.")
            conn.execute(
                """
                UPDATE hosted_app_authorization_codes
                   SET consumed_at = %s
                 WHERE id = %s
                """,
                (now, row["id"]),
            )
            raw_secret = secrets.token_urlsafe(32)
            session = AppSession(
                secret_hash=_hash(raw_secret),
                app_id=row["app_id"],
                workspace_id=row["workspace_id"],
                app_host=app_host,
                user_id=row["user_id"],
                runtime_generation=runtime_generation,
                expires_at=now + timedelta(seconds=self._absolute),
                idle_expires_at=now + timedelta(seconds=self._idle),
                last_seen_at=now,
            )
            conn.execute(
                """
                INSERT INTO hosted_app_sessions (
                    id, secret_hash, workspace_id, app_id, app_host, user_id,
                    runtime_generation, expires_at, idle_expires_at, revoked_at,
                    last_seen_at, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
                """,
                (
                    session.id,
                    session.secret_hash,
                    session.workspace_id,
                    session.app_id,
                    session.app_host,
                    session.user_id,
                    session.runtime_generation,
                    session.expires_at,
                    session.idle_expires_at,
                    session.last_seen_at,
                    session.created_at,
                ),
            )
        return IssuedAppSession((raw_secret, session))

    def introspect(
        self,
        raw_secret: str,
        *,
        app_host: str,
        runtime_generation: int,
        current_member: bool,
    ) -> AppSession:
        """Revalidate and refresh one durable app session."""
        now = _utcnow()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                  FROM hosted_app_sessions
                 WHERE secret_hash = %s
                 FOR UPDATE
                """,
                (_hash(raw_secret),),
            ).fetchone()
            if (
                row is None
                or row["revoked_at"] is not None
                or row["app_host"] != app_host
                or row["runtime_generation"] != runtime_generation
                or row["expires_at"] <= now
                or row["idle_expires_at"] <= now
                or not current_member
            ):
                raise AppAuthError("App session is invalid or expired.")
            idle_expires_at = min(
                row["expires_at"], now + timedelta(seconds=self._idle)
            )
            conn.execute(
                """
                UPDATE hosted_app_sessions
                   SET last_seen_at = %s,
                       idle_expires_at = %s
                 WHERE id = %s
                """,
                (now, idle_expires_at, row["id"]),
            )
            payload = dict(row)
            payload["last_seen_at"] = now
            payload["idle_expires_at"] = idle_expires_at
            return AppSession(**payload)

    def revoke(self, raw_secret: str) -> None:
        """Revoke a durable session idempotently."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE hosted_app_sessions
                   SET revoked_at = COALESCE(revoked_at, %s)
                 WHERE secret_hash = %s
                """,
                (_utcnow(), _hash(raw_secret)),
            )
