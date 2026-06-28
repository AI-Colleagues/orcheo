"""Service token tests split from the extended suite."""

from __future__ import annotations
from datetime import UTC, datetime
import pytest
from orcheo_backend.app.authentication import ServiceTokenManager, ServiceTokenRecord
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

    async def list_active(
        self, *, now: datetime | None = None
    ) -> list[ServiceTokenRecord]:
        self.list_active_calls += 1
        return await super().list_active(now=now)


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
