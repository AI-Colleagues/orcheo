from __future__ import annotations
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from orcheo.workspace import (
    InMemoryWorkspaceRepository,
    Role,
    Workspace,
    WorkspaceContext,
    WorkspaceMembership,
    WorkspaceService,
)
from orcheo_backend.app.errors import WorkspaceRateLimitError
from orcheo_backend.app.workspace import dependencies as workspace_dependencies
from orcheo_backend.app.workspace import errors as workspace_errors


def test_get_workspace_repository_selects_all_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository selection should only accept postgres backends."""

    created: list[tuple[str, tuple[object, ...]]] = []

    class _PostgresRepo:
        def __init__(self, dsn: str) -> None:
            created.append(("postgres", (dsn,)))

    monkeypatch.setattr(
        workspace_dependencies, "PostgresWorkspaceRepository", _PostgresRepo
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
            "POSTGRES_DSN": "postgres://dsn",
        },
    )
    with pytest.raises(ValueError, match="ORCHEO_WORKSPACE_BACKEND must be 'postgres'"):
        workspace_dependencies.get_workspace_repository()


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
    """Unauthenticated requests should be rejected when multi-workspace is enabled."""

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(is_authenticated=False, subject="user-1")

    with pytest.raises(workspace_errors.WorkspaceContextRequiredError):
        await workspace_dependencies.resolve_workspace_context(request, auth)


@pytest.mark.asyncio()
async def test_resolve_workspace_context_legacy_single_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthesise default workspace context when multi-workspace is disabled."""

    repo = InMemoryWorkspaceRepository()
    default_ws = Workspace(slug="default", name="Default Workspace")
    repo.create_workspace(default_ws)

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: WorkspaceService(repo),
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": False},  # noqa: ARG005
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(is_authenticated=False, subject="anonymous")

    result = await workspace_dependencies.resolve_workspace_context(request, auth)
    assert result.workspace_slug == "default"
    assert result.role == Role.OWNER
    assert request.state.workspace is result


@pytest.mark.asyncio()
async def test_resolve_workspace_context_rate_limit_and_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Workspace context resolution should set request state and enforce limits."""

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    context = SimpleNamespace(workspace_id="workspace-1", role=Role.OWNER)
    request = SimpleNamespace(
        headers={"X-Orcheo-Workspace": "acme"}, state=SimpleNamespace()
    )
    auth = SimpleNamespace(
        is_authenticated=True, subject="user-1", workspace_ids=frozenset()
    )

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


def test_set_workspace_service_with_non_none_also_sets_repository() -> None:
    """Lines 58-60: set_workspace_service with a non-None service also updates the repository ref."""
    from types import SimpleNamespace
    from orcheo.workspace import InMemoryWorkspaceRepository

    repo = InMemoryWorkspaceRepository()
    service = SimpleNamespace(repository=repo)

    workspace_dependencies.set_workspace_service(service)
    assert workspace_dependencies._workspace_service_ref["service"] is service
    assert workspace_dependencies._workspace_repository_ref["repository"] is repo

    workspace_dependencies.reset_workspace_state()


def test_get_workspace_resolver_returns_resolver_from_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 108: get_workspace_resolver returns service.resolver."""
    sentinel = object()

    class _FakeService:
        resolver = sentinel

    monkeypatch.setattr(
        workspace_dependencies, "get_workspace_service", lambda: _FakeService()
    )

    assert workspace_dependencies.get_workspace_resolver() is sentinel


@pytest.mark.asyncio()
async def test_resolve_from_authorized_workspaces_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service token with workspace_ids should resolve without membership lookup."""

    ws_id = uuid4()
    workspace = Workspace(id=ws_id, slug="acme", name="Acme Corp")
    repo = InMemoryWorkspaceRepository()
    repo.create_workspace(workspace)

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: WorkspaceService(repo),
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_governance",
        lambda refresh=False: SimpleNamespace(  # noqa: ARG005
            check_api_rate_limit=lambda workspace_id: None
        ),
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="svc-token-1",
        workspace_ids=frozenset({str(ws_id)}),
    )

    result = await workspace_dependencies.resolve_workspace_context(request, auth)
    assert result.workspace_id == ws_id
    assert result.workspace_slug == "acme"
    assert result.role == Role.OWNER


@pytest.mark.asyncio()
async def test_resolve_workspace_context_user_identity_uses_membership_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """User JWTs should resolve through memberships even when workspace_ids exist."""

    ws_id = uuid4()
    workspace = Workspace(id=ws_id, slug="acme", name="Acme Corp")
    repo = InMemoryWorkspaceRepository()
    repo.create_workspace(workspace)
    repo.add_membership(
        WorkspaceMembership(
            workspace_id=ws_id,
            user_id="user-1",
            role=Role.OWNER,
        )
    )

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: WorkspaceService(repo),
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_governance",
        lambda refresh=False: SimpleNamespace(  # noqa: ARG005
            check_api_rate_limit=lambda workspace_id: None
        ),
    )

    request = SimpleNamespace(
        headers={"X-Orcheo-Workspace": "acme"}, state=SimpleNamespace()
    )
    auth = SimpleNamespace(
        is_authenticated=True,
        identity_type="user",
        subject="user-1",
        workspace_ids=frozenset({str(uuid4())}),
    )

    result = await workspace_dependencies.resolve_workspace_context(request, auth)
    assert result.workspace_id == ws_id
    assert result.workspace_slug == "acme"
    assert result.user_id == "user-1"


@pytest.mark.asyncio()
async def test_resolve_from_authorized_workspaces_with_slug_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Header slug should select among authorized workspace IDs."""

    ws_a = Workspace(id=uuid4(), slug="alpha", name="Alpha")
    ws_b = Workspace(id=uuid4(), slug="beta", name="Beta")
    repo = InMemoryWorkspaceRepository()
    repo.create_workspace(ws_a)
    repo.create_workspace(ws_b)

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Service:
        repository = repo

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_governance",
        lambda refresh=False: SimpleNamespace(  # noqa: ARG005
            check_api_rate_limit=lambda workspace_id: None
        ),
    )

    request = SimpleNamespace(
        headers={"X-Orcheo-Workspace": "beta"}, state=SimpleNamespace()
    )
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="svc-token-1",
        workspace_ids=frozenset({str(ws_a.id), str(ws_b.id)}),
    )

    result = await workspace_dependencies.resolve_workspace_context(request, auth)
    assert result.workspace_id == ws_b.id
    assert result.workspace_slug == "beta"


@pytest.mark.asyncio()
async def test_resolve_from_authorized_workspaces_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown workspace IDs in token claims should raise forbidden."""

    repo = InMemoryWorkspaceRepository()

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Service:
        repository = repo

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="svc-token-1",
        workspace_ids=frozenset({str(uuid4())}),
    )

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        await workspace_dependencies.resolve_workspace_context(request, auth)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_resolve_from_authorized_workspaces_slug_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requesting a slug that doesn't match any authorized workspace raises 404."""

    ws_a = Workspace(id=uuid4(), slug="alpha", name="Alpha")
    ws_b = Workspace(id=uuid4(), slug="beta", name="Beta")
    repo = InMemoryWorkspaceRepository()
    repo.create_workspace(ws_a)
    repo.create_workspace(ws_b)

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Service:
        repository = repo

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )

    request = SimpleNamespace(
        headers={"X-Orcheo-Workspace": "gamma"}, state=SimpleNamespace()
    )
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="svc-token-1",
        workspace_ids=frozenset({str(ws_a.id), str(ws_b.id)}),
    )

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        await workspace_dependencies.resolve_workspace_context(request, auth)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_resolve_from_authorized_workspaces_multiple_no_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple workspaces with no slug header selects the first one."""

    ws_a = Workspace(id=uuid4(), slug="alpha", name="Alpha")
    ws_b = Workspace(id=uuid4(), slug="beta", name="Beta")
    repo = InMemoryWorkspaceRepository()
    repo.create_workspace(ws_a)
    repo.create_workspace(ws_b)

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Service:
        repository = repo

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )
    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_governance",
        lambda refresh=False: SimpleNamespace(  # noqa: ARG005
            check_api_rate_limit=lambda workspace_id: None
        ),
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="svc-token-1",
        workspace_ids=frozenset({str(ws_a.id), str(ws_b.id)}),
    )

    result = await workspace_dependencies.resolve_workspace_context(request, auth)
    assert result.workspace_id in {ws_a.id, ws_b.id}
    assert result.workspace_slug in {"alpha", "beta"}


@pytest.mark.asyncio()
async def test_resolve_from_authorized_workspaces_not_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a suspended workspace raises forbidden."""

    from orcheo.workspace import WorkspaceStatus

    ws = Workspace(
        id=uuid4(),
        slug="suspended-ws",
        name="Suspended",
        status=WorkspaceStatus.SUSPENDED,
    )
    repo = InMemoryWorkspaceRepository()
    repo.create_workspace(ws)

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Service:
        repository = repo

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="svc-token-1",
        workspace_ids=frozenset({str(ws.id)}),
    )

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        await workspace_dependencies.resolve_workspace_context(request, auth)
    assert exc_info.value.status_code == 403


def test_get_workspace_service_returns_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Second call to get_workspace_service returns the already-cached instance."""

    repo = InMemoryWorkspaceRepository()
    service = SimpleNamespace(repository=repo)

    workspace_dependencies.reset_workspace_state()
    try:
        workspace_dependencies.set_workspace_service(service)
        result = workspace_dependencies.get_workspace_service()
        assert result is service
    finally:
        workspace_dependencies.reset_workspace_state()


def test_set_workspace_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """set_workspace_repository stores the repo and clears the service cache."""

    repo = InMemoryWorkspaceRepository()
    try:
        workspace_dependencies.set_workspace_repository(repo)
        assert workspace_dependencies._workspace_repository_ref["repository"] is repo
        assert workspace_dependencies._workspace_service_ref["service"] is None
    finally:
        workspace_dependencies.reset_workspace_state()


@pytest.mark.asyncio()
async def test_resolve_via_resolver_not_found_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver WorkspaceNotFoundError should be translated to a 404."""

    from orcheo.workspace import WorkspaceNotFoundError

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Resolver:
        def resolve(self, *, user_id, workspace_slug):
            raise WorkspaceNotFoundError("no such workspace")

    class _Service:
        resolver = _Resolver()

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="user-1",
        workspace_ids=frozenset(),
    )

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        await workspace_dependencies.resolve_workspace_context(request, auth)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio()
async def test_resolve_via_resolver_permission_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver WorkspacePermissionError should be translated to a 403."""

    from orcheo.workspace import WorkspacePermissionError

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Resolver:
        def resolve(self, *, user_id, workspace_slug):
            raise WorkspacePermissionError("permission denied")

    class _Service:
        resolver = _Resolver()

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="user-1",
        workspace_ids=frozenset(),
    )

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        await workspace_dependencies.resolve_workspace_context(request, auth)
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_resolve_via_resolver_membership_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolver WorkspaceMembershipError should be translated to a 403."""

    from orcheo.workspace import WorkspaceMembershipError

    monkeypatch.setattr(
        workspace_dependencies,
        "get_settings",
        lambda refresh=False: {"MULTI_WORKSPACE_ENABLED": True},  # noqa: ARG005
    )

    class _Resolver:
        def resolve(self, *, user_id, workspace_slug):
            raise WorkspaceMembershipError("no membership")

    class _Service:
        resolver = _Resolver()

    monkeypatch.setattr(
        workspace_dependencies,
        "get_workspace_service",
        lambda: _Service(),
    )

    request = SimpleNamespace(headers={}, state=SimpleNamespace())
    auth = SimpleNamespace(
        is_authenticated=True,
        subject="user-1",
        workspace_ids=frozenset(),
    )

    with pytest.raises(workspace_errors.WorkspaceHTTPError) as exc_info:
        await workspace_dependencies.resolve_workspace_context(request, auth)
    assert exc_info.value.status_code == 403
