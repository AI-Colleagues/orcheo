"""Shared helpers for authentication tests."""

from __future__ import annotations
import hashlib
import json
import sqlite3
import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from orcheo_backend.app import create_app
from orcheo_backend.app.authentication import (
    ServiceTokenRecord,
    reset_authentication_state,
)
from orcheo_backend.app.repository import InMemoryWorkflowRepository
from orcheo_backend.app.service_token_repository import (
    SqliteServiceTokenRepository,
)


def reset_auth_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[None, None, None]:
    """Reset authentication-related environment between tests."""

    for key in (
        "ORCHEO_AUTH_SERVICE_TOKENS",
        "ORCHEO_AUTH_JWT_SECRET",
        "ORCHEO_AUTH_MODE",
        "ORCHEO_AUTH_ALLOWED_ALGORITHMS",
        "ORCHEO_AUTH_AUDIENCE",
        "ORCHEO_AUTH_ISSUER",
        "ORCHEO_AUTH_JWKS_URL",
        "ORCHEO_AUTH_JWKS",
        "ORCHEO_AUTH_JWKS_STATIC",
        "ORCHEO_AUTH_RATE_LIMIT_IP",
        "ORCHEO_AUTH_RATE_LIMIT_IDENTITY",
        "ORCHEO_AUTH_RATE_LIMIT_INTERVAL",
        "ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH",
        "ORCHEO_AUTH_BOOTSTRAP_SERVICE_TOKEN",
        "ORCHEO_AUTH_DEV_LOGIN_ENABLED",
        "ORCHEO_AUTH_DEV_COOKIE_NAME",
        "ORCHEO_AUTH_DEV_SCOPES",
        "ORCHEO_AUTH_DEV_WORKSPACE_IDS",
        "ORCHEO_WORKSPACE_BACKEND",
        "ORCHEO_POSTGRES_DSN",
        "ORCHEO_CHATKIT_PUBLIC_BASE_URL",
        "ORCHEO_CORS_ALLOW_ORIGINS",
    ):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("ORCHEO_WORKSPACE_BACKEND", "inmemory")
    reset_authentication_state()
    try:
        yield
    finally:
        monkeypatch.undo()
    reset_authentication_state()


def create_test_client() -> TestClient:
    """Build a FastAPI test client wired to the in-memory repository."""

    repository = InMemoryWorkflowRepository()
    return TestClient(create_app(repository=repository))


def _setup_service_token(
    monkeypatch: pytest.MonkeyPatch,
    token_secret: str,
    *,
    identifier: str | None = None,
    scopes: list[str] | None = None,
    workspace_ids: list[str] | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, str]:
    """Set up a service token for testing."""

    temp_dir = tempfile.mkdtemp()
    db_path = str(Path(temp_dir) / "test_tokens.sqlite")

    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_DB_PATH", db_path)

    SqliteServiceTokenRepository(db_path)

    token_hash = hashlib.sha256(token_secret.encode("utf-8")).hexdigest()
    record = ServiceTokenRecord(
        identifier=identifier or "test-token",
        secret_hash=token_hash,
        scopes=frozenset(scopes or []),
        workspace_ids=frozenset(workspace_ids or []),
        issued_at=datetime.now(tz=UTC),
        expires_at=expires_at,
    )

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO service_tokens (
            identifier, secret_hash, scopes, workspace_ids,
            created_at, issued_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.identifier,
            record.secret_hash,
            json.dumps(sorted(record.scopes)) if record.scopes else None,
            json.dumps(sorted(record.workspace_ids)) if record.workspace_ids else None,
            datetime.now(tz=UTC).isoformat(),
            record.issued_at.isoformat() if record.issued_at else None,
            record.expires_at.isoformat() if record.expires_at else None,
        ),
    )
    conn.commit()
    conn.close()

    return db_path, token_secret


__all__ = ["reset_auth_state", "create_test_client", "_setup_service_token"]
