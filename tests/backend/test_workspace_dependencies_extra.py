from __future__ import annotations
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from orcheo.workspace import InMemoryWorkspaceRepository, Role
from orcheo_backend.app.errors import WorkspaceRateLimitError
from orcheo_backend.app.workspace import dependencies as workspace_dependencies
from orcheo_backend.app.workspace import errors as workspace_errors


def test_get_workspace_repository_selects_all_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository selection should handle postgres, sqlite, and in-memory backends."""

    created: list[tuple[str, tuple[object, ...]]] = []

    class _PostgresRepo:
        def __init__(self, dsn: str) -> None:
            created.append(("postgres", (dsn,)))

    class _SqliteRepo:
        def __init__(self, path: str) -> None:
            created.append(("sqlite", (path,)))

    monkeypatch.setattr(
        workspace_dependencies, "PostgresWorkspaceRepository", _PostgresRepo
    )
    monkeypatch.setattr(
        workspace_dependencies, "SqliteWorkspaceRepository", _SqliteRepo
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "InMemoryWorkspaceRepository",
        lambda: created.append(("memory", ())) or InMemoryWorkspaceRepository(),
    )

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {  # noqa: ARG005
            "WORKSPACE_BACKEND": "postgres",
            "POSTGRES_DSN": "postgres://dsn",
        },
    )
    workspace_dependencies.reset_workspace_state()
    workspace_dependencies.get_workspace_repository()
    assert created[-1][0] == "postgres"

    workspace_dependencies.reset_workspace_state()
    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {  # noqa: ARG005
            "WORKSPACE_BACKEND": "sqlite",
            "WORKSPACE_SQLITE_PATH": "/tmp/workspaces.sqlite",
        },
    )
    workspace_dependencies.get_workspace_repository()
    assert created[-1][0] == "sqlite"

    workspace_dependencies.reset_workspace_state()
    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"WORKSPACE_BACKEND": "inmemory"},  # noqa: ARG005
    )
    workspace_dependencies.get_workspace_repository()
    assert created[-1][0] == "memory"


def test_get_workspace_repository_requires_dsn_for_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Postgres backend should reject missing DSN configuration."""

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"WORKSPACE_BACKEND": "postgres"},  # noqa: ARG005
    )
    workspace_dependencies.reset_workspace_state()

    with pytest.raises(ValueError, match="ORCHEO_POSTGRES_DSN"):
        workspace_dependencies.get_workspace_repository()


@pytest.mark.asyncio()
async def test_resolve_workspace_context_requires_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unauthenticated requests should be rejected before workspace lookup."""

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(is_authenticated=False, subject="user-1")

    with pytest.raises(workspace_errors.WorkspaceContextRequiredError):
        await workspace_dependencies.resolve_workspace_context(request, auth)


@pytest.mark.asyncio()
async def test_resolve_workspace_context_rate_limit_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace context resolution should set request state and enforce limits."""

    context = SimpleNamespace(workspace_id="workspace-1", role=Role.OWNER)
    request = SimpleNamespace(
        headers={"X-Orcheo-Workspace": "acme"}, state=SimpleNamespace()
    )
    auth = SimpleNamespace(is_authenticated=True, subject="user-1")

    class _Resolver:
        def resolve(self, *, user_id: str, workspace_slug: str | None) -> object:
            assert user_id == "user-1"
            assert workspace_slug == "acme"
            return context

    class _Service:
        resolver = _Resolver()

    monkeypatch.setattr(
        workspace_dependencies, "get_workspace_service", lambda: _Service()
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_governance",
        lambda refresh=False: SimpleNamespace(  # noqa: ARG005
            check_api_rate_limit=lambda workspace_id: None
        ),
    )

    result = await workspace_dependencies.resolve_workspace_context(request, auth)
    assert result is context
    assert request.state.workspace is context

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_governance",
        lambda refresh=False: SimpleNamespace(  # noqa: ARG005
            check_api_rate_limit=lambda workspace_id: (_ for _ in ()).throw(
                WorkspaceRateLimitError(
                    "Too many requests",
                    code="workspace.rate_limited",
                    retry_after=60,
                )
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspace_dependencies.resolve_workspace_context(request, auth)

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio()
async def test_require_role_and_require_workspace() -> None:
    """Role and workspace helpers should forward or reject contexts."""

    context = SimpleNamespace(has_role=lambda role: True, workspace_id="ws-1")
    checker = workspace_dependencies.require_role(Role.ADMIN)
    assert await checker(SimpleNamespace(), context) is context

    context = SimpleNamespace(has_role=lambda role: False, workspace_id="ws-1")
    checker = workspace_dependencies.require_role(Role.ADMIN)
    with pytest.raises(workspace_errors.WorkspaceHTTPError):
        await checker(SimpleNamespace(), context)

    assert await workspace_dependencies.require_workspace(context) is context
