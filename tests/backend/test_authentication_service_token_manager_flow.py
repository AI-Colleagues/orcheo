"""ServiceTokenManager integration flow tests."""

from __future__ import annotations
import hashlib
from datetime import UTC, datetime, timedelta
import pytest
from orcheo_backend.app.authentication import (
    AuthenticationError,
    ServiceTokenManager,
    ServiceTokenRecord,
)
from orcheo_backend.app.service_token_repository import (
    InMemoryServiceTokenRepository,
)


@pytest.mark.asyncio
async def test_service_token_manager_mint_authenticate_revoke() -> None:
    """ServiceTokenManager should support minting, authentication, and revocation."""

    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository)
    secret, record = await manager.mint(
        scopes={"workflows:read"}, workspace_ids={"ws-1"}
    )

    assert record.matches(secret)
    all_tokens = await manager.all()
    assert record.identifier in {item.identifier for item in all_tokens}

    authenticated = await manager.authenticate(secret)
    assert authenticated.identifier == record.identifier

    await manager.revoke(record.identifier, reason="test")
    with pytest.raises(AuthenticationError):
        await manager.authenticate(secret)


@pytest.mark.asyncio
async def test_service_token_manager_authenticate_revoked_token() -> None:
    """Authenticate raises when token is revoked."""

    token = "revoked-token"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

    record = ServiceTokenRecord(
        identifier="revoked",
        secret_hash=digest,
        revoked_at=datetime.now(tz=UTC),
    )
    repository = InMemoryServiceTokenRepository()
    await repository.create(record)
    manager = ServiceTokenManager(repository)

    with pytest.raises(AuthenticationError) as exc:
        await manager.authenticate(token)
    assert exc.value.code == "auth.token_revoked"
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_service_token_manager_authenticate_expired_token() -> None:
    """Authenticate raises when token is expired."""

    token = "expired-token"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

    record = ServiceTokenRecord(
        identifier="expired",
        secret_hash=digest,
        expires_at=datetime.now(tz=UTC) - timedelta(hours=1),
    )
    repository = InMemoryServiceTokenRepository()
    await repository.create(record)
    manager = ServiceTokenManager(repository)

    with pytest.raises(AuthenticationError) as exc:
        await manager.authenticate(token)
    assert exc.value.code == "auth.token_expired"
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_service_token_manager_mint_with_timedelta() -> None:
    """Mint can accept timedelta for expires_in."""

    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository)
    secret, record = await manager.mint(expires_in=timedelta(hours=1))

    assert record.matches(secret)
    assert record.expires_at is not None


@pytest.mark.asyncio
async def test_service_token_manager_mint_with_seconds() -> None:
    """Mint can accept int seconds for expires_in."""

    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository)
    secret, record = await manager.mint(expires_in=3600)

    assert record.matches(secret)
    assert record.expires_at is not None


@pytest.mark.asyncio
async def test_service_token_manager_mint_without_expiry() -> None:
    """Mint creates token without expiry when expires_in is None."""

    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository)
    secret, record = await manager.mint()

    assert record.matches(secret)
    assert record.expires_at is None


@pytest.mark.asyncio
async def test_service_token_manager_revoke_nonexistent_raises() -> None:
    """Revoke raises KeyError for nonexistent identifier."""

    repository = InMemoryServiceTokenRepository()
    manager = ServiceTokenManager(repository)

    with pytest.raises(KeyError):
        await manager.revoke("nonexistent", reason="test")


@pytest.mark.asyncio
async def test_service_token_manager_all() -> None:
    """all() returns all managed tokens."""

    record1 = ServiceTokenRecord(identifier="token-1", secret_hash="hash1")
    record2 = ServiceTokenRecord(identifier="token-2", secret_hash="hash2")
    repository = InMemoryServiceTokenRepository()
    await repository.create(record1)
    await repository.create(record2)
    manager = ServiceTokenManager(repository)

    all_tokens = await manager.all()

    assert len(all_tokens) == 2
    identifiers = {token.identifier for token in all_tokens}
    assert "token-1" in identifiers
    assert "token-2" in identifiers
