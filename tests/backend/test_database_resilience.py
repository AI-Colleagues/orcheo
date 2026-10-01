"""Regression tests for blocking database calls and transient failures."""

from __future__ import annotations
import asyncio
import importlib
import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
import httpx
import pytest
from fastapi import FastAPI
from psycopg import InterfaceError, OperationalError
from psycopg.errors import LockNotAvailable, QueryCanceled
from psycopg_pool import PoolTimeout, TooManyRequests
from orcheo.identity import IdentitySessionNotFoundError
from orcheo_backend.app.authentication import RequestContext, get_request_context
from orcheo_backend.app.database_errors import (
    DATABASE_UNAVAILABLE_ERRORS,
    database_unavailable_handler,
)
from orcheo_backend.app.identity.dependencies import get_identity_service
from orcheo_backend.app.identity.router import router
from orcheo_backend.app.identity.service import IssuedTokens


class _Limiter:
    def check_ip(self, *args: Any, **kwargs: Any) -> None:
        """Leave rate limits out of database-failure tests."""

    def check_identity(self, *args: Any, **kwargs: Any) -> None:
        """Leave rate limits out of database-failure tests."""


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build the identity routes without unrelated runtime dependencies."""
    identity_router = importlib.import_module("orcheo_backend.app.identity.router")

    monkeypatch.setattr(identity_router, "get_auth_rate_limiter", lambda: _Limiter())
    application = FastAPI()
    application.include_router(router)
    for error_type in DATABASE_UNAVAILABLE_ERRORS:
        application.add_exception_handler(error_type, database_unavailable_handler)
    application.dependency_overrides[get_request_context] = lambda: RequestContext(
        subject="test-user", identity_type="user"
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


@pytest.mark.asyncio
async def test_blocked_refresh_does_not_block_health_or_other_requests(
    app: FastAPI,
) -> None:
    """An actual blocking service call must run outside the ASGI event loop."""
    started = threading.Event()
    release = threading.Event()
    service_threads: list[int] = []
    event_loop_thread = threading.get_ident()

    def refresh(token: str) -> IssuedTokens:
        service_threads.append(threading.get_ident())
        started.set()
        # Bound the wait so a regression cannot hang pytest itself.
        assert release.wait(timeout=3)
        return IssuedTokens("access", "refresh", 900)

    service = SimpleNamespace(now=lambda: datetime.now(UTC), refresh=refresh)
    app.dependency_overrides[get_identity_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        request = asyncio.create_task(
            client.post("/auth/refresh", json={"refresh_token": "original"})
        )
        try:
            assert await asyncio.to_thread(started.wait, 1)
            assert service_threads[0] != event_loop_thread
            health = await asyncio.wait_for(client.get("/health"), timeout=0.5)
            assert health.status_code == 200
            assert not request.done()
        finally:
            release.set()
            response = await request
        assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type",
    [
        OperationalError,
        InterfaceError,
        PoolTimeout,
        TooManyRequests,
        QueryCanceled,
        LockNotAvailable,
    ],
)
@pytest.mark.parametrize("path", ["refresh", "email/verify", "logout", "me"])
async def test_database_failures_are_503_not_invalid_credentials(
    app: FastAPI, error_type: type[Exception], path: str
) -> None:
    """Outages must remain distinct from credential rejection at every route."""

    def fail(*args: Any, **kwargs: Any) -> None:
        raise error_type("private database diagnostic")

    service = SimpleNamespace(
        now=lambda: datetime.now(UTC),
        refresh=fail,
        verify_code=fail,
        logout=fail,
        get_user=fail,
    )
    app.dependency_overrides[get_identity_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(
            "GET" if path == "me" else "POST",
            f"/auth/{path}",
            json={
                "refresh_token": "token",
                "email": "user@example.com",
                "code": "123456",
            },
        )
    assert response.status_code == 503
    assert "private database diagnostic" not in response.text


@pytest.mark.asyncio
async def test_dependency_database_failure_is_sanitized_503(app: FastAPI) -> None:
    """Pool failures during lazy schema initialization bypass route catches."""

    def fail() -> None:
        raise PoolTimeout("private connection details")

    app.dependency_overrides[get_identity_service] = fail
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/auth/refresh", json={"refresh_token": "token"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "database.unavailable"
    assert "private connection details" not in response.text


@pytest.mark.asyncio
async def test_invalid_refresh_still_returns_401(app: FastAPI) -> None:
    """Definitive token rejection must continue to end the client session."""

    def fail(token: str) -> None:
        raise IdentitySessionNotFoundError(token)

    app.dependency_overrides[get_identity_service] = lambda: SimpleNamespace(
        now=lambda: datetime.now(UTC), refresh=fail
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/auth/refresh", json={"refresh_token": "token"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_email_start_retains_constant_response_on_database_failure(
    app: FastAPI,
) -> None:
    """Failures must not turn email/start into an account-existence oracle."""

    def fail(email: str) -> None:
        raise OperationalError("private diagnostic")

    app.dependency_overrides[get_identity_service] = lambda: SimpleNamespace(
        now=lambda: datetime.now(UTC), start_challenge=fail
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/auth/email/start", json={"email": "user@example.com"}
        )
    assert response.status_code == 200
    assert response.json() == {"status": "sent"}


@pytest.mark.asyncio
async def test_workspace_resolution_does_not_block_the_event_loop(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace PostgreSQL queries must have the same isolation as identity."""
    from uuid import uuid4
    from starlette.requests import Request
    from orcheo.workspace import Role, WorkspaceContext, WorkspaceQuotas

    dependencies = importlib.import_module("orcheo_backend.app.workspace.dependencies")
    started = threading.Event()
    release = threading.Event()
    event_loop_thread = threading.get_ident()
    context = WorkspaceContext(
        workspace_id=uuid4(),
        workspace_slug="test",
        user_id="test-user",
        role=Role.OWNER,
        quotas=WorkspaceQuotas(),
    )

    def resolve(**kwargs: Any) -> WorkspaceContext:
        assert threading.get_ident() != event_loop_thread
        started.set()
        assert release.wait(timeout=3)
        return context

    monkeypatch.setattr(
        dependencies,
        "get_workspace_service",
        lambda: SimpleNamespace(resolver=SimpleNamespace(resolve=resolve)),
    )
    monkeypatch.setattr(
        dependencies,
        "get_workspace_governance",
        lambda: SimpleNamespace(check_api_rate_limit=lambda _: None),
    )
    request = Request({"type": "http", "headers": []})
    task = asyncio.create_task(
        dependencies.resolve_workspace_context(
            request, RequestContext(subject="test-user", identity_type="user")
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 1)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await asyncio.wait_for(client.get("/health"), timeout=0.5)
            assert response.status_code == 200
            assert not task.done()
    finally:
        release.set()
        result = await task
    assert result is context
