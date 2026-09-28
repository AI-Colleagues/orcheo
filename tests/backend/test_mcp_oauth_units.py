"""Edge cases of the MCP OAuth provider, identity grants and discovery URLs."""

from __future__ import annotations
import importlib
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from mcp.server.auth.provider import AccessToken, RefreshToken, TokenError
from mcp.shared.auth import OAuthClientInformationFull
from starlette.requests import Request
from orcheo.identity import (
    IdentitySessionNotFoundError,
    InMemoryIdentityRepository,
    OAuthAuthorizationRequestNotFoundError,
    OAuthClient,
)
from orcheo.identity.models import User, UserStatus
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication import RequestContext
from orcheo_backend.app.identity import IdentityConfig, IdentityService
from orcheo_backend.app.oauth.provider import (
    OrcheoAuthorizationCode,
    OrcheoOAuthProvider,
)
from orcheo_backend.app.oauth.urls import public_origin


oauth_router = importlib.import_module("orcheo_backend.app.oauth.router")
mcp_router = importlib.import_module("orcheo_backend.app.routers.mcp")

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


class _NullSender:
    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:  # noqa: D102
        return None


@pytest.fixture()
def service() -> IdentityService:
    return IdentityService(
        InMemoryIdentityRepository(),
        email_sender=_NullSender(),
        config=IdentityConfig(jwt_secret="secret"),  # noqa: S106 - test fixture
        clock=lambda: NOW,
    )


@pytest.fixture()
def provider(service: IdentityService) -> OrcheoOAuthProvider:
    return OrcheoOAuthProvider(
        service,
        public_url="https://orcheo.test",
        signing_secret="secret",  # noqa: S106 - test fixture
        consent_url="https://studio.test/oauth/consent",
        clock=lambda: NOW,
    )


def _client(client_id: str = "client-1") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        redirect_uris=["http://127.0.0.1:9000/cb"],  # type: ignore[list-item]
        token_endpoint_auth_method="none",
        scope="workflows:read",
    )


def _user(service: IdentityService, **overrides: Any) -> User:
    return service.repository.create_user(
        User(email="erin@example.com", email_verified=True, **overrides)
    )


@pytest.mark.asyncio()
async def test_unknown_clients_codes_and_requests(
    provider: OrcheoOAuthProvider,
) -> None:
    client = _client()

    assert await provider.get_client("missing") is None
    assert await provider.load_authorization_code(client, "nope") is None
    assert await provider.pending_request("missing") is None
    assert await provider.load_access_token("jwt") is None
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        await provider.decide("missing", user_id=uuid4(), approve=True)


@pytest.mark.asyncio()
async def test_codes_are_bound_to_their_client(
    provider: OrcheoOAuthProvider, service: IdentityService
) -> None:
    client = _client()
    await provider.register_client(client)
    user = _user(service)
    location = await provider.authorize(
        client,
        _params(),
    )
    request_id = location.rsplit("=", 1)[1]
    redirect = await provider.decide(request_id, user_id=user.id, approve=True)
    code = redirect.split("code=", 1)[1].split("&", 1)[0]

    assert await provider.load_authorization_code(_client("other"), code) is None
    loaded = await provider.load_authorization_code(client, code)
    assert isinstance(loaded, OrcheoAuthorizationCode)
    assert loaded.user_id == user.id

    # Two concurrent exchanges of one code: only the first wins.
    await provider.exchange_authorization_code(client, loaded)
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, loaded)


@pytest.mark.asyncio()
async def test_pending_request_requires_its_client(
    provider: OrcheoOAuthProvider,
) -> None:
    client = _client()
    location = await provider.authorize(client, _params())

    # The client was never registered, e.g. it was deleted meanwhile.
    assert await provider.pending_request(location.rsplit("=", 1)[1]) is None


@pytest.mark.asyncio()
async def test_disabled_users_cannot_complete_or_refresh_grants(
    provider: OrcheoOAuthProvider, service: IdentityService
) -> None:
    client = _client()
    user = _user(service)
    tokens = service.issue_oauth_session(
        user.id, client_id="client-1", scopes=["workflows:read"]
    )
    service.repository.update_user(
        user.model_copy(update={"status": UserStatus.DISABLED})
    )

    code = OrcheoAuthorizationCode(
        code="c",
        scopes=["workflows:read"],
        expires_at=NOW.timestamp() + 60,
        client_id="client-1",
        code_challenge="x",
        redirect_uri="http://127.0.0.1:9000/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=True,
        request_id="req",
        user_id=user.id,
    )
    service.repository.add_oauth_client(OAuthClient(client_id="client-1"))
    service.repository.add_authorization_request(
        _stored_request(user_id=user.id, request_id="req")
    )
    with pytest.raises(TokenError):
        await provider.exchange_authorization_code(client, code)
    refresh = RefreshToken(
        token=tokens.refresh_token, client_id="client-1", scopes=["workflows:read"]
    )
    with pytest.raises(TokenError):
        await provider.exchange_refresh_token(client, refresh, ["workflows:read"])


def test_identity_grant_edge_cases(service: IdentityService) -> None:
    user = _user(service, status=UserStatus.DISABLED)

    with pytest.raises(IdentitySessionNotFoundError):
        service.issue_oauth_session(user.id, client_id="c", scopes=[])
    # Revoking an unknown token is a no-op (RFC 7009).
    service.revoke_oauth_session("unknown", client_id="c")
    with pytest.raises(IdentitySessionNotFoundError):
        service.refresh_oauth_session("unknown", client_id="c", scopes=[])


@pytest.mark.asyncio()
async def test_revoke_ignores_access_tokens(provider: OrcheoOAuthProvider) -> None:
    await provider.revoke_token(AccessToken(token="t", client_id="c", scopes=[]))


def _params() -> Any:
    from mcp.server.auth.provider import AuthorizationParams

    return AuthorizationParams(
        state="s",
        scopes=None,
        code_challenge="challenge",
        redirect_uri="http://127.0.0.1:9000/cb",  # type: ignore[arg-type]
        redirect_uri_provided_explicitly=True,
    )


def _stored_request(*, user_id: Any, request_id: str) -> Any:
    from orcheo.identity import OAuthAuthorizationRequest

    return OAuthAuthorizationRequest(
        id=request_id,
        client_id="client-1",
        redirect_uri="http://127.0.0.1:9000/cb",
        redirect_uri_provided_explicitly=True,
        code_challenge="x",
        expires_at=NOW + timedelta(minutes=5),
        user_id=user_id,
    )


def _request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "path": "/",
            "query_string": b"",
            "headers": [
                (key.lower().encode(), value.encode()) for key, value in headers.items()
            ]
            + [(b"host", b"backend:2025")],
            "server": ("backend", 2025),
        }
    )


def test_public_origin_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    from orcheo.config import get_settings

    forwarded = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "orcheo.test, x"}
    monkeypatch.delenv("ORCHEO_PUBLIC_URL", raising=False)
    monkeypatch.delenv("ORCHEO_TRUSTED_PROXY", raising=False)
    get_settings(refresh=True)
    assert public_origin(_request(forwarded)) == "http://backend:2025"

    monkeypatch.setenv("ORCHEO_TRUSTED_PROXY", "true")
    get_settings(refresh=True)
    assert public_origin(_request(forwarded)) == "https://orcheo.test"
    assert public_origin(_request({"X-Forwarded-Host": " "})) == "http://backend:2025"

    monkeypatch.setenv("ORCHEO_PUBLIC_URL", "https://public.test/")
    get_settings(refresh=True)
    assert public_origin(_request(forwarded)) == "https://public.test"
    monkeypatch.delenv("ORCHEO_PUBLIC_URL")
    monkeypatch.delenv("ORCHEO_TRUSTED_PROXY")
    get_settings(refresh=True)


@pytest.mark.parametrize(
    "context",
    [
        RequestContext(subject="svc", identity_type="service", scopes=frozenset()),
        RequestContext(subject="not-a-uuid", identity_type="user", scopes=frozenset()),
    ],
    ids=["service-token", "non-uuid-subject"],
)
def test_consent_requires_first_party_users(context: RequestContext) -> None:
    with pytest.raises(HTTPException) as exc_info:
        oauth_router._consenting_user(context)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio()
async def test_endpoints_404_without_first_party_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    def _unconfigured() -> None:
        raise ValueError("AUTH_JWT_SECRET must be set")

    monkeypatch.setattr(oauth_router, "get_identity_config", _unconfigured)
    app = FastAPI()
    app.include_router(oauth_router.well_known_router)
    app.include_router(oauth_router.router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        metadata = await client.get("/.well-known/oauth-authorization-server")
        token = await client.post("/api/oauth/token")

    assert metadata.status_code == 404
    assert token.status_code == 404


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ("status_code", "provider", "expected"),
    [
        (401, None, "Bearer"),
        (403, object(), "Bearer"),
        (401, object(), 'Bearer resource_metadata="http://t/.well-known/'),
    ],
    ids=["oauth-unconfigured", "not-a-401", "challenge"],
)
async def test_mcp_challenge_only_advertises_configured_oauth(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
    provider: object,
    expected: str,
) -> None:
    from fastapi import FastAPI

    async def _reject(_request: Request) -> None:
        raise HTTPException(
            status_code=status_code,
            detail="nope",
            headers={"WWW-Authenticate": "Bearer"},
        )

    monkeypatch.setattr(mcp_router, "authenticate_request", _reject)
    monkeypatch.setattr(mcp_router, "oauth_server", lambda _request: provider)
    app = FastAPI()
    app.include_router(mcp_router.router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/mcp", json={})

    assert response.status_code == status_code
    assert response.headers["www-authenticate"].startswith(expected)


@pytest.mark.asyncio()
async def test_revocation_errors(provider: OrcheoOAuthProvider) -> None:
    from starlette.applications import Starlette
    from starlette.routing import Mount

    await provider.register_client(_client())
    app = Starlette(routes=[Mount("/api/oauth", routes=provider.get_routes())])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        unknown_client = await client.post(
            "/api/oauth/revoke", data={"token": "t", "client_id": "missing"}
        )
        no_token = await client.post(
            "/api/oauth/revoke", data={"client_id": "client-1"}
        )
        unknown_token = await client.post(
            "/api/oauth/revoke", data={"token": "t", "client_id": "client-1"}
        )

    assert unknown_client.status_code == 401
    assert unknown_client.json()["error"] == "invalid_client"
    assert no_token.status_code == 400
    assert unknown_token.status_code == 200


@pytest.mark.asyncio()
async def test_registration_is_rate_limited(
    provider: OrcheoOAuthProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from orcheo_backend.app.authentication import AuthenticationError
    from orcheo_backend.app.oauth import handlers

    class _Limiter:
        def check_ip(self, *_args: object, **_kwargs: object) -> None:
            raise AuthenticationError(
                "Too many requests", code="auth.rate_limited", status_code=429
            )

    monkeypatch.setattr(handlers, "get_auth_rate_limiter", lambda: _Limiter())
    app = Starlette(routes=[Mount("/api/oauth", routes=provider.get_routes())])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.post("/api/oauth/register", json={})

    assert response.status_code == 429
    assert response.json()["error"] == "slow_down"


@pytest.mark.asyncio()
async def test_oauth_needs_an_https_origin(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    service: IdentityService,
) -> None:
    from fastapi import FastAPI

    monkeypatch.setattr(oauth_router, "get_identity_service", lambda: service)
    monkeypatch.setattr(
        oauth_router,
        "get_identity_config",
        lambda: IdentityConfig(jwt_secret="secret"),  # noqa: S106 - test fixture
    )
    app = FastAPI()
    app.include_router(oauth_router.well_known_router)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://orcheo.lan"
    ) as client:
        response = await client.get("/.well-known/oauth-protected-resource/api/mcp")

    assert response.status_code == 404
    assert "must be served over HTTPS" in caplog.text
