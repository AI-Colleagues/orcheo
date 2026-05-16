"""Authentication dependency tests split from the extended suite."""

from __future__ import annotations
import pytest
from orcheo_backend.app.authentication import (
    AuthenticationError,
    get_request_context,
)
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


def test_extract_bearer_token_rejects_blank_token() -> None:
    """_extract_bearer_token enforces a non-empty token segment."""

    class FakeHeader(str):
        def split(self) -> list[str]:  # type: ignore[override]
            return ["Bearer", " "]

    from orcheo_backend.app.authentication.dependencies import (
        AuthenticationError,
        _extract_bearer_token,
    )

    with pytest.raises(AuthenticationError) as exc:
        _extract_bearer_token(FakeHeader("Bearer  "))
    assert exc.value.code == "auth.missing_token"


@pytest.mark.asyncio
async def test_get_request_context_from_state() -> None:
    """get_request_context retrieves context from request state."""
    from starlette.requests import Request
    from orcheo_backend.app.authentication import RequestContext

    context = RequestContext(subject="test-user", identity_type="user")

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]
    request.state.auth = context

    result = await get_request_context(request)

    assert result is context
    assert result.subject == "test-user"


def test_authorization_policy_require_authenticated() -> None:
    """AuthorizationPolicy.require_authenticated raises for anonymous."""
    from orcheo_backend.app.authentication import (
        AuthorizationPolicy,
        RequestContext,
    )

    anon = RequestContext.anonymous()
    policy = AuthorizationPolicy(anon)

    with pytest.raises(AuthenticationError) as exc:
        policy.require_authenticated()
    assert exc.value.code == "auth.authentication_required"


def test_authorization_policy_require_workspaces() -> None:
    """AuthorizationPolicy.require_workspaces validates multiple workspaces."""
    from orcheo_backend.app.authentication import (
        AuthorizationError,
        AuthorizationPolicy,
        RequestContext,
    )

    context = RequestContext(
        subject="user-1",
        identity_type="user",
        workspace_ids=frozenset({"ws-1", "ws-2"}),
    )
    policy = AuthorizationPolicy(context)

    # Should succeed
    policy.require_workspaces(["ws-1", "ws-2"])

    # Should fail
    with pytest.raises(AuthorizationError):
        policy.require_workspaces(["ws-1", "ws-3"])


def test_ensure_workspace_access_with_empty_workspaces() -> None:
    """ensure_workspace_access allows empty workspace list."""
    from orcheo_backend.app.authentication import (
        RequestContext,
        ensure_workspace_access,
    )

    context = RequestContext(
        subject="user-1",
        identity_type="user",
    )

    # Should not raise
    ensure_workspace_access(context, [])


def test_ensure_workspace_access_no_workspace_context() -> None:
    """ensure_workspace_access raises when context has no workspaces."""
    from orcheo_backend.app.authentication import (
        AuthorizationError,
        RequestContext,
        ensure_workspace_access,
    )

    context = RequestContext(
        subject="user-1",
        identity_type="user",
    )

    with pytest.raises(AuthorizationError) as exc:
        ensure_workspace_access(context, ["ws-1"])
    assert exc.value.code == "auth.workspace_forbidden"


def test_authorization_policy_context_property() -> None:
    """AuthorizationPolicy.context returns the underlying context."""
    from orcheo_backend.app.authentication import (
        AuthorizationPolicy,
        RequestContext,
    )

    context = RequestContext(subject="user", identity_type="user")
    policy = AuthorizationPolicy(context)

    assert policy.context is context


@pytest.mark.asyncio
async def test_get_request_context_calls_authenticate_when_no_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_request_context authenticates when no context in state."""
    from starlette.requests import Request
    from orcheo_backend.app.authentication import (
        get_request_context,
        reset_authentication_state,
    )

    # Disable authentication for this test
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": None,
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]
    # No auth in state

    # Should call authenticate_request
    context = await get_request_context(request)

    assert context is not None
    assert not context.is_authenticated  # Anonymous in this test


def test_get_authorization_policy_dependency() -> None:
    """get_authorization_policy returns an AuthorizationPolicy."""
    from orcheo_backend.app.authentication import (
        AuthorizationPolicy,
        RequestContext,
        get_authorization_policy,
    )

    context = RequestContext(subject="user", identity_type="user")

    policy = get_authorization_policy(context)

    assert isinstance(policy, AuthorizationPolicy)
    assert policy.context is context


@pytest.mark.asyncio
async def test_authenticate_request_without_client_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """authenticate_request handles requests without client information."""
    from starlette.requests import Request
    from orcheo_backend.app.authentication import (
        authenticate_request,
        reset_authentication_state,
    )

    # Disable authentication for this test
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "disabled")
    reset_authentication_state()

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": None,
    }

    async def receive() -> dict[str, object]:
        return {"type": "http.request"}

    request = Request(scope, receive)  # type: ignore[arg-type]

    # Should not raise - returns anonymous context
    context = await authenticate_request(request)

    assert not context.is_authenticated


@pytest.mark.asyncio
async def test_require_scopes_dependency_returns_context() -> None:
    """require_scopes dependency returns context when scopes are present."""
    from orcheo_backend.app.authentication import RequestContext, require_scopes

    context = RequestContext(
        subject="user",
        identity_type="user",
        scopes=frozenset(["read", "write"]),
    )

    # Create the dependency function
    dependency = require_scopes("read", "write")

    # Call it with the context
    result = await dependency(context)

    assert result is context


def test_get_authenticator_postgres_backend_without_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_authenticator raises ValueError when postgres backend lacks DSN."""
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_BACKEND", "postgres")
    monkeypatch.delenv("ORCHEO_POSTGRES_DSN", raising=False)

    from unittest.mock import Mock, patch
    from orcheo_backend.app.authentication.settings import AuthSettings
    from orcheo_backend.app import (
        service_token_repository as service_token_repository_module,
    )

    auth_settings = AuthSettings(
        mode="optional",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("RS256", "HS256"),
        audiences=(),
        issuer=None,
        rate_limit_ip=1,
        rate_limit_identity=1,
        rate_limit_interval=60,
        service_token_backend="postgres",
        service_token_db_path=None,
    )

    with (
        patch(
            "orcheo_backend.app.authentication.dependencies.load_auth_settings",
            return_value=auth_settings,
        ),
        patch(
            "orcheo_backend.app.authentication.dependencies.get_settings",
            return_value=Mock(get=Mock(return_value=None)),
        ),
        patch.object(
            service_token_repository_module,
            "PostgresServiceTokenRepository",
        ),
    ):
        from orcheo_backend.app.authentication.dependencies import get_authenticator

        with pytest.raises(ValueError, match="ORCHEO_POSTGRES_DSN must be set"):
            get_authenticator(refresh=True)


def test_get_authenticator_postgres_backend_with_dsn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """get_authenticator initializes PostgresServiceTokenRepository with DSN."""
    test_dsn = "postgresql://user:pass@localhost:5432/testdb"
    monkeypatch.setenv("ORCHEO_AUTH_SERVICE_TOKEN_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", test_dsn)

    from unittest.mock import Mock, patch
    from orcheo_backend.app.authentication.settings import AuthSettings
    from orcheo_backend.app import (
        service_token_repository as service_token_repository_module,
    )
    from orcheo_backend.app.service_token_repository import (
        postgres_repository as postgres_repository_module,
    )

    auth_settings = AuthSettings(
        mode="optional",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=("RS256", "HS256"),
        audiences=(),
        issuer=None,
        rate_limit_ip=1,
        rate_limit_identity=1,
        rate_limit_interval=60,
        service_token_backend="postgres",
        service_token_db_path=None,
    )

    mock_repo = Mock()
    mock_settings = Mock()
    mock_settings.get.return_value = test_dsn

    with (
        patch(
            "orcheo_backend.app.authentication.dependencies.load_auth_settings",
            return_value=auth_settings,
        ),
        patch.object(
            service_token_repository_module,
            "PostgresServiceTokenRepository",
            return_value=mock_repo,
        ) as mock_postgres_repo,
        patch.object(
            postgres_repository_module,
            "PostgresServiceTokenRepository",
            return_value=mock_repo,
        ),
        patch(
            "orcheo_backend.app.authentication.dependencies.get_settings",
            return_value=mock_settings,
        ),
    ):
        from orcheo_backend.app.authentication.dependencies import get_authenticator

        authenticator = get_authenticator(refresh=True)

        # Verify PostgresServiceTokenRepository was called with the DSN
        mock_postgres_repo.assert_called_once_with(test_dsn)
        assert authenticator is not None
