"""Service token tests split from the extended suite."""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
import pytest
from orcheo_backend.app.authentication import (
    Authenticator,
    ServiceTokenManager,
    ServiceTokenRecord,
    load_auth_settings,
)
from orcheo_backend.app.service_token_repository import InMemoryServiceTokenRepository
from tests.backend.authentication_test_utils import reset_auth_state


@pytest.fixture(autouse=True)
def _reset_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure authentication state is cleared between tests."""

    yield from reset_auth_state(monkeypatch)


class _CountingServiceTokenRepository(InMemoryServiceTokenRepository):
    """Track repository calls for cache behavior assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.list_active_calls = 0
        self.find_by_hash_calls = 0

    async def list_active(
        self, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        self.list_active_calls += 1
        return await super().list_active(now=now)

    async def find_by_hash(self, secret_hash: str) -> ServiceTokenRecord | None:
        self.find_by_hash_calls += 1
        return await super().find_by_hash(secret_hash)


@pytest.mark.asyncio
async def test_service_token_manager_with_custom_clock() -> None:
    """ServiceTokenManager can use a custom clock function."""

    fixed_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)

    def custom_clock() -> datetime:
        return fixed_time

    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository, clock=custom_clock)
    secret, record = await manager.mint()

    assert record.issued_at == fixed_time
    assert record.secret_preview == secret[-4:]


@pytest.mark.asyncio
async def test_service_token_manager_all_uses_cache_on_repeated_calls() -> None:
    """all() should reuse the cached active token list while it is fresh."""

    repository = _CountingServiceTokenRepository()
    await repository.create(ServiceTokenRecord(identifier="token-1", secret_hash="h1"))
    manager = ServiceTokenManager(repository, clock=lambda: datetime.now(tz=UTC))

    first = await manager.all()
    second = await manager.all()

    assert first == second
    assert repository.list_active_calls == 1


@pytest.mark.asyncio
async def test_empty_token_cache_is_shared_until_expiry() -> None:
    """An empty cache avoids repeat queries and refreshes after its TTL."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repository = _CountingServiceTokenRepository()
    manager = ServiceTokenManager(repository, clock=lambda: now)
    assert await asyncio.gather(*(manager.all() for _ in range(20))) == [()] * 20
    assert repository.list_active_calls == 1
    now += timedelta(seconds=31)
    assert await manager.all() == ()
    assert repository.list_active_calls == 2


@pytest.mark.asyncio
async def test_authenticate_token_minted_elsewhere_with_cached_empty_list() -> None:
    """A worker accepts new tokens immediately despite its cached empty list."""
    now = datetime.now(tz=UTC)
    repository = _CountingServiceTokenRepository()
    receiving_manager = ServiceTokenManager(repository, clock=lambda: now)
    issuing_manager = ServiceTokenManager(repository, clock=lambda: now)
    authenticator = Authenticator(load_auth_settings(), receiving_manager)

    assert await receiving_manager.all() == ()
    token, record = await issuing_manager.mint(scopes=["read:workflows"])

    context = await authenticator.authenticate(token)

    assert context.token_id == record.identifier
    assert context.scopes == frozenset({"read:workflows"})
    assert repository.list_active_calls == 1
    assert repository.find_by_hash_calls == 1


@pytest.mark.asyncio
async def test_service_authentication_skips_active_token_listing() -> None:
    """Authenticating an opaque token needs only an indexed token lookup."""
    repository = _CountingServiceTokenRepository()
    manager = ServiceTokenManager(repository)
    token, record = await manager.mint()
    authenticator = Authenticator(load_auth_settings(), manager)

    assert (await authenticator.authenticate(token)).token_id == record.identifier
    assert repository.list_active_calls == 0
    assert repository.find_by_hash_calls == 1
