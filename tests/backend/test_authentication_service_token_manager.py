"""Service token tests split from the extended suite."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
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


@pytest.mark.asyncio
async def test_service_token_rotate_preserves_expiry_without_overlap() -> None:
    """Token rotation without overlap preserves original expiry if sooner."""

    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository)

    # Create token expiring in 1 hour
    original_secret, original_record = await manager.mint(expires_in=timedelta(hours=1))

    # Rotate with 0 overlap
    new_secret, new_record = await manager.rotate(
        original_record.identifier, overlap_seconds=0
    )

    # Original record should still exist but rotated
    updated_original = await repository.find_by_id(original_record.identifier)
    assert updated_original is not None
    assert updated_original.rotated_to == new_record.identifier


@pytest.mark.asyncio
async def test_service_token_rotate_expiry_with_none_original_expiry() -> None:
    """ServiceTokenManager rotation handles records with no expiry correctly."""
    from datetime import timedelta
    from orcheo_backend.app.authentication import (
        ServiceTokenManager,
        ServiceTokenRecord,
    )

    # Create record with no expiry
    record = ServiceTokenRecord(
        identifier="no-expiry",
        secret_hash="hash123",
        expires_at=None,
    )

    repository = InMemoryServiceTokenRepository()
    await repository.create(record)
    manager = ServiceTokenManager(repository)

    # Rotate with overlap
    new_secret, new_record = await manager.rotate(
        record.identifier, overlap_seconds=300, expires_in=timedelta(hours=1)
    )

    # Original record should have rotation_expires_at set based on overlap
    updated = await repository.find_by_id(record.identifier)
    assert updated is not None
    assert updated.rotation_expires_at is not None
    # Expiry should be set to overlap since original had None
    assert updated.expires_at is not None


def test_service_token_rotation_expiry_with_non_none_expires_at() -> None:
    """_calculate_rotation_expiry returns min when expires_at is not None."""
    from orcheo_backend.app.authentication import ServiceTokenManager

    now = datetime.now(tz=UTC)
    future = now + timedelta(days=30)
    record = ServiceTokenRecord(
        identifier="test",
        secret_hash="test-hash",
        expires_at=future,
    )

    # Overlap is shorter than record expiry
    result = ServiceTokenManager._calculate_rotation_expiry(record, now, 300)

    # Should return the overlap expiry (now + 300 seconds)
    expected = now + timedelta(seconds=300)
    assert result is not None
    assert abs((result - expected).total_seconds()) < 1


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
