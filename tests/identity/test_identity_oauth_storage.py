"""Tests for OAuth client and authorization-request storage."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4
import pytest
from orcheo.identity import (
    AuthSession,
    InMemoryIdentityRepository,
    OAuthAuthorizationRequest,
    OAuthAuthorizationRequestNotFoundError,
    OAuthClient,
    OAuthClientNotFoundError,
    PostgresIdentityRepository,
)
from tests.identity.test_identity_postgres_store import FakeConnection, fake_pool_class


NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def _request(**overrides: Any) -> OAuthAuthorizationRequest:
    fields: dict[str, Any] = {
        "id": "req-1",
        "client_id": "client-1",
        "redirect_uri": "http://127.0.0.1:9000/cb",
        "redirect_uri_provided_explicitly": True,
        "code_challenge": "challenge",
        "state": "s",
        "scopes": ["workflows:read"],
        "resource": "https://orcheo.test/api/mcp",
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=10),
    }
    fields.update(overrides)
    return OAuthAuthorizationRequest(**fields)


def _request_row(request: OAuthAuthorizationRequest) -> dict[str, Any]:
    return request.model_dump()


def test_pending_state_tracks_decision_and_expiry() -> None:
    request = _request()

    assert request.is_pending(now=NOW)
    assert not request.is_pending(now=NOW + timedelta(minutes=11))
    assert not request.model_copy(update={"decided_at": NOW}).is_pending(now=NOW)


def test_in_memory_oauth_roundtrip() -> None:
    repo = InMemoryIdentityRepository()
    client = repo.add_oauth_client(
        OAuthClient(client_id="client-1", metadata={"redirect_uris": ["x"]})
    )
    assert repo.get_oauth_client("client-1") == client
    with pytest.raises(OAuthClientNotFoundError):
        repo.get_oauth_client("missing")

    request = repo.add_authorization_request(_request())
    assert repo.get_authorization_request("req-1") == request
    approved = repo.update_authorization_request(
        request.model_copy(update={"code_hash": "hash", "user_id": uuid4()})
    )
    assert repo.get_authorization_request_by_code_hash("hash") == approved
    consumed = repo.consume_authorization_code("req-1", consumed_at=NOW)
    assert consumed.consumed_at == NOW

    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.consume_authorization_code("req-1", consumed_at=NOW)
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.get_authorization_request("missing")
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.get_authorization_request_by_code_hash("missing")
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.update_authorization_request(_request(id="missing"))


@pytest.fixture
def pg(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeConnection, Any]:
    from orcheo.identity import postgres_store as pg_store

    connection = FakeConnection([])
    monkeypatch.setattr(pg_store, "ConnectionPool", fake_pool_class(connection))
    return connection, PostgresIdentityRepository("postgresql://test")


def test_postgres_schema_adds_oauth_columns_and_tables(
    pg: tuple[FakeConnection, Any],
) -> None:
    connection, _ = pg
    statements = "\n".join(query for query, _ in connection.queries)

    assert "ADD COLUMN IF NOT EXISTS oauth_client_id" in statements
    assert "CREATE TABLE IF NOT EXISTS oauth_clients" in statements
    assert "CREATE TABLE IF NOT EXISTS oauth_authorization_requests" in statements


def test_postgres_oauth_clients(pg: tuple[FakeConnection, Any]) -> None:
    connection, repo = pg
    client = OAuthClient(client_id="client-1", metadata={"client_name": "x"})
    connection._responses = [
        {},
        {"row": client.model_dump()},
        {"row": None},
    ]

    repo.add_oauth_client(client)
    assert repo.get_oauth_client("client-1") == client
    with pytest.raises(OAuthClientNotFoundError):
        repo.get_oauth_client("missing")


def test_postgres_authorization_requests(pg: tuple[FakeConnection, Any]) -> None:
    connection, repo = pg
    user_id = uuid4()
    pending = _request()
    approved = pending.model_copy(
        update={
            "user_id": user_id,
            "decided_at": NOW,
            "code_hash": "hash",
            "code_expires_at": NOW + timedelta(minutes=5),
        }
    )
    connection._responses = [
        {},
        {"row": _request_row(pending)},
        {"row": _request_row(approved)},
        {"rowcount": 1},
        {"rowcount": 0},
        {"row": {**_request_row(approved), "consumed_at": NOW}},
        {"row": None},
        {"row": None},
    ]

    repo.add_authorization_request(pending)
    assert repo.get_authorization_request("req-1") == pending
    assert repo.get_authorization_request_by_code_hash("hash") == approved
    assert repo.update_authorization_request(approved) == approved
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.update_authorization_request(approved)
    assert repo.consume_authorization_code("req-1", consumed_at=NOW).consumed_at == NOW
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.consume_authorization_code("req-1", consumed_at=NOW)
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.get_authorization_request("missing")

    insert_params = next(
        params
        for query, params in connection.queries
        if query.startswith("INSERT INTO oauth_authorization_requests")
    )
    assert insert_params[10] is None  # no user until consent


def test_postgres_sessions_persist_oauth_grant(pg: tuple[FakeConnection, Any]) -> None:
    connection, repo = pg
    session = AuthSession(
        user_id=uuid4(),
        refresh_token_hash="h",
        expires_at=NOW,
        oauth_client_id="client-1",
        scopes=["workflows:read"],
    )
    connection._responses = [
        {},
        {"rowcount": 1},
        {"row": {**session.model_dump(), "scopes": ["workflows:read"]}},
    ]

    repo.add_session(session)
    repo.update_session(session)
    loaded = repo.get_session_by_refresh_hash("h")

    insert = next(p for q, p in connection.queries if q.startswith("INSERT INTO auth"))
    assert insert[8] == "client-1"
    assert insert[9].obj == ["workflows:read"]
    assert loaded.oauth_client_id == "client-1"
    assert loaded.scopes == ["workflows:read"]


def test_in_memory_decisions_and_revocations_are_final() -> None:
    repo = InMemoryIdentityRepository()
    request = repo.add_authorization_request(_request())
    repo.update_authorization_request(request.model_copy(update={"decided_at": NOW}))
    session = repo.add_session(
        AuthSession(user_id=uuid4(), refresh_token_hash="h", expires_at=NOW)
    )
    repo.update_session(session.model_copy(update={"revoked_at": NOW}))

    # A racing second decision loses, and a stale rotation keeps the revocation.
    with pytest.raises(OAuthAuthorizationRequestNotFoundError):
        repo.update_authorization_request(
            request.model_copy(update={"decided_at": NOW, "code_hash": "late"})
        )
    rotated = repo.update_session(
        session.model_copy(update={"refresh_token_hash": "n"})
    )

    assert rotated.revoked_at == NOW
    assert repo.get_session(session.id).refresh_token_hash == "n"
    assert repo.get_session(session.id).revoked_at == NOW


def test_postgres_decisions_and_revocations_are_final(
    pg: tuple[FakeConnection, Any],
) -> None:
    connection, repo = pg
    connection._responses = [{"rowcount": 1}, {"rowcount": 1}]

    repo.update_authorization_request(_request())
    repo.update_session(
        AuthSession(user_id=uuid4(), refresh_token_hash="h", expires_at=NOW)
    )

    decide, rotate = (query for query, _ in connection.queries[-2:])
    assert "AND decided_at IS NULL" in decide
    assert "revoked_at = COALESCE(revoked_at, %s)" in rotate
