"""JWKS cache and fetch tests split from the extended suite."""

from __future__ import annotations
from datetime import UTC, datetime
from unittest.mock import Mock, patch
import pytest
from orcheo_backend.app.authentication import (
    Authenticator,
    AuthSettings,
    ServiceTokenManager,
    load_auth_settings,
    reset_authentication_state,
)
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


@pytest.mark.asyncio
async def test_jwks_cache_lock_prevents_concurrent_fetches() -> None:
    """JWKS cache prevents concurrent fetches with async lock."""
    from orcheo_backend.app.authentication import JWKSCache

    fetch_count = 0

    async def fetcher() -> tuple[list[dict[str, str]], int | None]:
        nonlocal fetch_count
        fetch_count += 1
        # Simulate slow fetch
        import asyncio

        await asyncio.sleep(0.1)
        return ([{"kid": "key-1"}], 300)

    cache = JWKSCache(fetcher, ttl_seconds=300)

    # Trigger concurrent fetches
    import asyncio

    results = await asyncio.gather(
        cache.keys(),
        cache.keys(),
        cache.keys(),
    )

    # Should only fetch once due to lock
    assert fetch_count == 1
    assert all(r == [{"kid": "key-1"}] for r in results)


@pytest.mark.asyncio
async def test_jwks_fetch_with_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """JWKS fetcher handles HTTP errors appropriately."""
    import httpx
    from orcheo_backend.app.authentication import Authenticator

    monkeypatch.setenv(
        "ORCHEO_AUTH_JWKS_URL", "https://keys.example.com/.well-known/jwks.json"
    )
    reset_authentication_state()

    settings = load_auth_settings(refresh=True)
    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    # Mock httpx to raise an error
    with patch("orcheo_backend.app.authentication.httpx.AsyncClient") as mock_client:
        mock_response = Mock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Not found", request=Mock(), response=Mock(status_code=404)
        )
        mock_client.return_value.__aenter__.return_value.get.return_value = (
            mock_response
        )

        with pytest.raises(httpx.HTTPStatusError):
            await authenticator._jwt_authenticator._fetch_jwks()


@pytest.mark.asyncio
async def test_jwks_cache_with_zero_ttl_header() -> None:
    """JWKS cache handles zero TTL from headers correctly."""
    from orcheo_backend.app.authentication import JWKSCache

    async def fetcher() -> tuple[list[dict[str, str]], int | None]:
        return ([{"kid": "key-1"}], 0)

    cache = JWKSCache(fetcher, ttl_seconds=0)

    await cache.keys()

    # With both TTL and header at 0, expires_at should be None
    assert cache._expires_at is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_jwks_cache_respects_header_ttl_when_config_is_zero() -> None:
    """JWKS cache uses header TTL when configured TTL is zero."""
    from orcheo_backend.app.authentication import JWKSCache

    async def fetcher() -> tuple[list[dict[str, str]], int | None]:
        return ([{"kid": "key-1"}], 300)

    cache = JWKSCache(fetcher, ttl_seconds=0)

    await cache.keys()

    # Should use header TTL since config is 0
    assert cache._expires_at is not None  # noqa: SLF001
    remaining = (cache._expires_at - datetime.now(tz=UTC)).total_seconds()
    assert remaining == pytest.approx(300, abs=2.0)


@pytest.mark.asyncio
async def test_jwks_cache_early_return_with_valid_cache() -> None:
    """JWKS cache returns early when cache is still valid."""
    from orcheo_backend.app.authentication import JWKSCache

    call_count = 0

    async def fetcher() -> tuple[list[dict[str, str]], int | None]:
        nonlocal call_count
        call_count += 1
        return ([{"kid": "key-1"}], None)

    cache = JWKSCache(fetcher, ttl_seconds=300)

    # First call
    keys1 = await cache.keys()
    assert call_count == 1

    # Second call should return cached value without calling fetcher again
    keys2 = await cache.keys()
    assert call_count == 1  # No additional call
    assert keys1 == keys2


@pytest.mark.asyncio
async def test_fetch_jwks_returns_empty_when_no_url() -> None:
    """_fetch_jwks returns empty list when no URL is configured."""

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url=None,
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=(),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    keys, ttl = await authenticator._jwt_authenticator._fetch_jwks()  # noqa: SLF001

    assert keys == []
    assert ttl is None


@pytest.mark.asyncio
async def test_fetch_jwks_parses_cache_control() -> None:
    """_fetch_jwks extracts TTL from Cache-Control headers."""
    from unittest.mock import AsyncMock, Mock

    settings = AuthSettings(
        mode="required",
        jwt_secret=None,
        jwks_url="https://example.com/jwks.json",
        jwks_static=(),
        jwks_cache_ttl=300,
        jwks_timeout=5.0,
        allowed_algorithms=(),
        audiences=(),
        issuer=None,
        service_token_backend="inmemory",
        service_token_db_path=None,
        rate_limit_ip=0,
        rate_limit_identity=0,
        rate_limit_interval=60,
    )

    from orcheo_backend.app.service_token_repository import (
        InMemoryServiceTokenRepository,
    )

    repository = InMemoryServiceTokenRepository()
    token_manager = ServiceTokenManager(repository)
    authenticator = Authenticator(settings, token_manager)

    # Mock the HTTP response
    mock_response = Mock()
    mock_response.json.return_value = {"keys": [{"kid": "key1"}]}
    mock_response.headers = {"Cache-Control": "max-age=600"}
    mock_response.raise_for_status = Mock()

    with patch("orcheo_backend.app.authentication.httpx.AsyncClient") as mock_client:
        mock_client.return_value.__aenter__.return_value.get = AsyncMock(
            return_value=mock_response
        )

        keys, ttl = await authenticator._jwt_authenticator._fetch_jwks()  # noqa: SLF001

        assert len(keys) == 1
        assert ttl == 600
