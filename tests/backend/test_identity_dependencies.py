"""Coverage tests for the first-party identity dependency wiring."""

from __future__ import annotations
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from orcheo_backend.app.identity import dependencies


@pytest.fixture(autouse=True)
def _reset_identity_dependencies() -> None:
    dependencies.set_identity_repository(None)
    dependencies.set_identity_service(None)
    yield
    dependencies.set_identity_repository(None)
    dependencies.set_identity_service(None)


def _make_request(
    *,
    forwarded_for: str | None = None,
    client_host: str | None = "10.0.0.2",
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (client_host, 1234) if client_host else None,
    }

    async def _receive() -> dict[str, object]:
        return {"type": "http.request"}

    return Request(scope, _receive)  # type: ignore[arg-type]


def test_identity_repository_setters_reset_cached_service() -> None:
    repository = object()
    service = SimpleNamespace(repository=repository)

    dependencies.set_identity_repository(repository)
    assert dependencies.get_identity_repository() is repository

    dependencies.set_identity_service(service)  # type: ignore[arg-type]
    assert dependencies.get_identity_service() is service

    dependencies.set_identity_repository(None)
    assert dependencies._identity_service_ref["service"] is None


def test_get_identity_repository_covers_backend_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakePostgresRepository:
        def __init__(self, dsn: str) -> None:
            self.dsn = dsn

    class FakeMemoryRepository:
        def __init__(self) -> None:
            self.kind = "memory"

    monkeypatch.setattr(
        dependencies, "PostgresIdentityRepository", FakePostgresRepository
    )
    monkeypatch.setattr(
        dependencies, "InMemoryIdentityRepository", FakeMemoryRepository
    )
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: {"WORKSPACE_BACKEND": "postgres", "POSTGRES_DSN": "postgresql://db"},
    )

    repository = dependencies.get_identity_repository()
    assert isinstance(repository, FakePostgresRepository)
    assert repository.dsn == "postgresql://db"

    dependencies.set_identity_repository(None)
    monkeypatch.setattr(
        dependencies, "get_settings", lambda: {"WORKSPACE_BACKEND": "sqlite"}
    )
    repository = dependencies.get_identity_repository()
    assert isinstance(repository, FakeMemoryRepository)


def test_get_identity_config_requires_jwt_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dependencies,
        "load_auth_settings",
        lambda: SimpleNamespace(jwt_secret=None, issuer=None, audiences=()),
    )
    monkeypatch.setattr(dependencies, "get_settings", lambda: {})

    with pytest.raises(ValueError, match="AUTH_JWT_SECRET must be set"):
        dependencies.get_identity_config()


def test_get_identity_config_uses_defaults_and_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dependencies,
        "load_auth_settings",
        lambda: SimpleNamespace(
            jwt_secret="secret",
            issuer=None,
            audiences=("audience-1", "audience-2"),
        ),
    )
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: {
            "STUDIO_URL": "https://studio.example.com",
            "AUTH_ACCESS_TOKEN_TTL_SECONDS": "1200",
            "AUTH_CHALLENGE_TTL_MINUTES": "20",
            "AUTH_SESSION_TTL_DAYS": "45",
            "AUTH_OTP_DIGITS": "8",
            "AUTH_OTP_MAX_ATTEMPTS": "3",
        },
    )

    config = dependencies.get_identity_config()
    assert config.jwt_secret == "secret"
    assert config.issuer == dependencies.DEFAULT_FIRST_PARTY_ISSUER
    assert config.audience == "audience-1"
    assert config.verify_base_url == "https://studio.example.com"
    assert config.access_ttl_seconds == 1200
    assert config.challenge_ttl_minutes == 20
    assert config.session_ttl_days == 45
    assert config.otp_digits == 8
    assert config.otp_max_attempts == 3


def test_get_identity_service_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = object()
    config = object()
    sender = object()
    calls: list[str] = []

    class FakeService:
        def __init__(
            self, repository: object, *, email_sender: object, config: object
        ) -> None:
            calls.append("init")
            self.repository = repository
            self.email_sender = email_sender
            self.config = config

    monkeypatch.setattr(dependencies, "get_identity_repository", lambda: repository)
    monkeypatch.setattr(dependencies, "get_identity_config", lambda: config)
    monkeypatch.setattr(
        dependencies, "build_transactional_email_sender", lambda: sender
    )
    monkeypatch.setattr(dependencies, "IdentityService", FakeService)

    first = dependencies.get_identity_service()
    second = dependencies.get_identity_service()

    assert first is second
    assert calls == ["init"]
    assert first.repository is repository
    assert first.email_sender is sender
    assert first.config is config


def test_reset_identity_state_refreshes_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refreshed: list[bool] = []

    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda refresh=False: refreshed.append(refresh) or {},
    )

    dependencies.reset_identity_state()

    assert refreshed == [True]


def test_trusted_proxy_enabled_and_client_ip_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: {"TRUSTED_PROXY": "yes"},
    )
    assert dependencies._trusted_proxy_enabled() is True

    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: {"TRUSTED_PROXY": True},
    )
    assert dependencies._trusted_proxy_enabled() is True

    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: {"TRUSTED_PROXY": None},
    )
    assert dependencies._trusted_proxy_enabled() is False

    monkeypatch.setattr(
        dependencies,
        "get_settings",
        lambda: {"TRUSTED_PROXY": True},
    )
    request = SimpleNamespace(
        headers=SimpleNamespace(get=lambda *args, **kwargs: None),
        client=SimpleNamespace(host="10.0.0.2"),
    )
    assert dependencies.get_client_ip(request) == "10.0.0.2"

    forwarded_request = SimpleNamespace(
        headers=SimpleNamespace(get=lambda *args, **kwargs: "   "),
        client=SimpleNamespace(host="10.0.0.3"),
    )
    assert dependencies.get_client_ip(forwarded_request) == "10.0.0.3"
