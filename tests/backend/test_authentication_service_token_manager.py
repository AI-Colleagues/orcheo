"""Service token tests split from the extended suite."""

from __future__ import annotations
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
    """Track repository calls for authentication query assertions."""

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
async def test_service_token_manager_all_reflects_changes_from_other_managers() -> None:
    """Listing active tokens immediately observes another worker's changes."""
    repository = InMemoryServiceTokenRepository()
    reader = ServiceTokenManager(repository)
    writer = ServiceTokenManager(repository)
    assert await reader.all() == ()

    _, record = await writer.mint()
    assert await reader.all() == (record,)

    await writer.revoke(record.identifier)
    assert await reader.all() == ()


@pytest.mark.asyncio
async def test_service_token_manager_all_excludes_newly_expired_tokens() -> None:
    """Each listing filters expiry using the manager's current clock."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository, clock=lambda: now)
    _, record = await manager.mint(expires_in=10)
    assert await manager.all() == (record,)

    now += timedelta(seconds=10)
    assert await manager.all() == ()


@pytest.mark.asyncio
async def test_authenticate_token_minted_elsewhere_after_empty_listing() -> None:
    """A worker accepts new tokens immediately after observing an empty list."""
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
