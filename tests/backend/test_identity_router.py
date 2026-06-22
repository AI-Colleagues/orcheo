"""HTTP integration tests for the first-party auth endpoints."""

from __future__ import annotations
import importlib
from datetime import UTC, datetime
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from orcheo.identity import InMemoryIdentityRepository
from orcheo.identity.errors import (
    IdentityChallengeExpiredError,
    IdentityChallengeLockedError,
    IdentitySessionNotFoundError,
    UserNotFoundError,
)
from orcheo.workspace.email import AuthChallengeEmail
from orcheo_backend.app.authentication import reset_authentication_state
from orcheo_backend.app.identity import (
    IdentityConfig,
    IdentityService,
    get_client_ip,
    reset_identity_state,
    set_identity_service,
)
from orcheo_backend.app.identity.router import _enforce_start_rate_limits
from tests.backend.authentication_test_utils import create_test_client

SECRET = "identity-http-secret"  # noqa: S105 - test fixture
ISSUER = "https://auth.orcheo.test"
AUDIENCE = "orcheo-api"
identity_router = importlib.import_module("orcheo_backend.app.identity.router")


class CapturingSender:
    def __init__(self) -> None:
        self.sent: list[AuthChallengeEmail] = []

    def send_auth_challenge(self, email: AuthChallengeEmail) -> None:
        self.sent.append(email)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, CapturingSender]:
    monkeypatch.setenv("ORCHEO_AUTH_JWT_SECRET", SECRET)
    monkeypatch.setenv("ORCHEO_AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("ORCHEO_AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.setenv("ORCHEO_AUTH_MODE", "required")
    reset_authentication_state()
    reset_identity_state()

    sender = CapturingSender()
    service = IdentityService(
        InMemoryIdentityRepository(),
        email_sender=sender,
        config=IdentityConfig(
            jwt_secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            verify_base_url="https://studio.test",
        ),
    )
    set_identity_service(service)

    test_client = create_test_client()
    try:
        yield test_client, sender
    finally:
        set_identity_service(None)
        reset_identity_state()
        reset_authentication_state()


def _start(
    client: TestClient, email: str = "alice@example.com", redirect_to: str | None = None
) -> None:
    payload = {"email": email}
    if redirect_to is not None:
        payload["redirect_to"] = redirect_to
    response = client.post("/api/auth/email/start", json=payload)
    assert response.status_code == 200
    assert response.json() == {"status": "sent"}


def test_email_start_is_constant_response(client) -> None:
    test_client, _ = client
    _start(test_client)
    # Malformed email still returns the identical "sent" response.
    bad = test_client.post("/api/auth/email/start", json={"email": "nope"})
    assert bad.status_code == 200
    assert bad.json() == {"status": "sent"}


def test_email_start_threads_redirect_to_magic_link(client) -> None:
    test_client, sender = client
    _start(test_client, redirect_to="/workflows/abc?tab=runs")
    assert "redirect=%2Fworkflows%2Fabc%3Ftab%3Druns" in sender.sent[-1].magic_link_url


def test_full_magic_link_login_and_me(client) -> None:
    test_client, sender = client
    _start(test_client)
    token = sender.sent[-1].magic_link_url.split("token=", 1)[1]

    verified = test_client.post("/api/auth/email/verify", json={"token": token})
    assert verified.status_code == 200
    body = verified.json()
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["email_verified"] is True
    access = body["access_token"]

    me = test_client.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_otp_verify_and_refresh_and_logout(client) -> None:
    test_client, sender = client
    _start(test_client)
    code = sender.sent[-1].otp_code

    verified = test_client.post(
        "/api/auth/email/verify",
        json={"email": "alice@example.com", "code": code},
    )
    assert verified.status_code == 200
    tokens = verified.json()
    access, refresh = tokens["access_token"], tokens["refresh_token"]

    refreshed = test_client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] != refresh

    # The original refresh token is now rotated away.
    replay = test_client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert replay.status_code == 401

    logout = test_client.post(
        "/api/auth/logout", headers={"Authorization": f"Bearer {access}"}
    )
    assert logout.status_code == 204

    # After logout, the rotated refresh token is revoked too.
    after = test_client.post(
        "/api/auth/refresh",
        json={"refresh_token": refreshed.json()["refresh_token"]},
    )
    assert after.status_code == 401


def test_verify_invalid_token_rejected(client) -> None:
    test_client, _ = client
    response = test_client.post("/api/auth/email/verify", json={"token": "bogus-token"})
    assert response.status_code == 400


def test_verify_requires_token_or_code(client) -> None:
    test_client, _ = client
    response = test_client.post("/api/auth/email/verify", json={})
    assert response.status_code == 400


def test_get_client_ip_ignores_forwarded_for_without_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORCHEO_TRUSTED_PROXY", raising=False)
    reset_identity_state()
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 10.0.0.2")],
            "client": ("10.0.0.2", 1234),
        }
    )

    assert get_client_ip(request) == "10.0.0.2"


def test_get_client_ip_honors_forwarded_for_with_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_TRUSTED_PROXY", "true")
    reset_identity_state()
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 10.0.0.2")],
            "client": ("10.0.0.2", 1234),
        }
    )

    assert get_client_ip(request) == "203.0.113.10"


def test_enforce_start_rate_limits_accepts_injected_now(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[datetime] = []

    class CapturingLimiter:
        def check_ip(self, ip: str | None, *, now: datetime) -> None:
            observed.append(now)

        def check_identity(self, identity: str, *, now: datetime) -> None:
            observed.append(now)

    frozen = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setitem(
        _enforce_start_rate_limits.__globals__,
        "get_auth_rate_limiter",
        lambda: CapturingLimiter(),
    )

    _enforce_start_rate_limits("203.0.113.10", "auth-email:a@example.com", now=frozen)

    assert observed == [frozen, frozen]


class _NoopLimiter:
    def check_ip(self, ip: str | None, *, now: datetime) -> None:
        del ip, now


class _FakeIdentityService:
    def __init__(self) -> None:
        self.now_calls = 0

    def now(self) -> datetime:
        self.now_calls += 1
        return datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.asyncio
async def test_email_start_swallows_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeIdentityService()

    def _start_challenge(email: str, *, redirect_to: str | None = None) -> None:
        del email, redirect_to
        raise ValueError("bad email")

    service.start_challenge = _start_challenge  # type: ignore[attr-defined]
    monkeypatch.setattr(
        identity_router, "_enforce_start_rate_limits", lambda *a, **k: None
    )

    response = await identity_router.email_start(
        identity_router.EmailStartRequest(email="not-an-email"),
        service,  # type: ignore[arg-type]
        ip=None,
    )

    assert response.status == "sent"


@pytest.mark.asyncio
async def test_email_start_swallows_generic_delivery_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeIdentityService()

    def _start_challenge(email: str, *, redirect_to: str | None = None) -> None:
        del email, redirect_to
        raise RuntimeError("smtp down")

    service.start_challenge = _start_challenge  # type: ignore[attr-defined]
    monkeypatch.setattr(
        identity_router, "_enforce_start_rate_limits", lambda *a, **k: None
    )

    response = await identity_router.email_start(
        identity_router.EmailStartRequest(email="alice@example.com"),
        service,  # type: ignore[arg-type]
        ip=None,
    )

    assert response.status == "sent"


@pytest.mark.asyncio
async def test_email_verify_handles_domain_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeIdentityService()
    monkeypatch.setattr(
        identity_router, "get_auth_rate_limiter", lambda: _NoopLimiter()
    )
    request = SimpleNamespace(headers={"User-Agent": "pytest"})

    for exc, status_code, message in [
        (IdentityChallengeLockedError(), 423, None),
        (IdentityChallengeExpiredError(), 410, None),
        (ValueError("bad code"), 400, "bad code"),
        (RuntimeError("boom"), 400, "Invalid or expired challenge."),
    ]:
        if isinstance(exc, IdentityChallengeLockedError):
            service.verify_token = lambda *a, **k: (_ for _ in ()).throw(exc)  # type: ignore[attr-defined]
        elif isinstance(exc, IdentityChallengeExpiredError):
            service.verify_token = lambda *a, **k: (_ for _ in ()).throw(exc)  # type: ignore[attr-defined]
        elif isinstance(exc, ValueError):
            service.verify_token = lambda *a, **k: (_ for _ in ()).throw(exc)  # type: ignore[attr-defined]
        else:
            service.verify_token = lambda *a, **k: (_ for _ in ()).throw(exc)  # type: ignore[attr-defined]

        with pytest.raises(Exception) as raised:
            await identity_router.email_verify(
                identity_router.EmailVerifyRequest(token="token"),
                service,  # type: ignore[arg-type]
                request,  # type: ignore[arg-type]
                ip="127.0.0.1",
            )

        http_error = raised.value
        assert getattr(http_error, "status_code", None) == status_code
        if message is not None:
            detail = getattr(http_error, "detail", {})
            assert message in str(detail)


@pytest.mark.asyncio
async def test_refresh_and_me_and_logout_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _FakeIdentityService()
    monkeypatch.setattr(
        identity_router, "get_auth_rate_limiter", lambda: _NoopLimiter()
    )

    service.refresh = lambda token: (_ for _ in ()).throw(
        IdentitySessionNotFoundError()
    )  # type: ignore[attr-defined]
    with pytest.raises(Exception) as refresh_exc:
        await identity_router.refresh(
            identity_router.RefreshRequest(refresh_token="missing"),
            service,  # type: ignore[arg-type]
            ip="127.0.0.1",
        )
    assert getattr(refresh_exc.value, "status_code", None) == 401

    service.logout = lambda subject: (_ for _ in ()).throw(ValueError("nope"))  # type: ignore[attr-defined]
    logout = await identity_router.logout(
        service,  # type: ignore[arg-type]
        auth=SimpleNamespace(subject="missing"),
    )
    assert logout.status_code == 204

    service.get_user = lambda subject: (_ for _ in ()).throw(UserNotFoundError(subject))  # type: ignore[attr-defined]
    with pytest.raises(Exception) as me_exc:
        await identity_router.me(
            service,  # type: ignore[arg-type]
            auth=SimpleNamespace(subject="missing"),
        )
    assert getattr(me_exc.value, "status_code", None) == 404
