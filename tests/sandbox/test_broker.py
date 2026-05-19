"""Tests for the Credential Broker."""

from __future__ import annotations
import pytest
from orcheo.sandbox.broker import (
    BrokerScopeError,
    BrokerTokenInvalid,
    CredentialBroker,
    generate_broker_secret,
)


def _resolver(store: dict[tuple[str, str], str]) -> object:
    def resolve(*, workspace_id: str, credential_name: str) -> str:
        return store[(workspace_id, credential_name)]

    return resolve


def test_issue_and_resolve_round_trip() -> None:
    """A token issued for a workspace resolves credentials inside that scope."""
    broker = CredentialBroker(
        secret="s",
        resolver=_resolver({("ws", "openai"): "sk-1"}),
    )
    token = broker.issue(workspace_id="ws", run_id="r1")
    parsed, value = broker.resolve(token, credential_name="openai")
    assert parsed.workspace_id == "ws"
    assert parsed.run_id == "r1"
    assert value == "sk-1"


def test_cross_workspace_request_returns_scope_error() -> None:
    """A claimed workspace that differs from the token's is rejected."""
    broker = CredentialBroker(
        secret="s",
        resolver=_resolver({("ws", "openai"): "sk-1"}),
    )
    token = broker.issue(workspace_id="ws", run_id="r1")
    with pytest.raises(BrokerScopeError):
        broker.resolve(token, credential_name="openai", requested_workspace_id="other")


def test_unknown_credential_is_scope_error() -> None:
    """A credential not in the workspace scope returns 403, not 404."""
    broker = CredentialBroker(secret="s", resolver=_resolver({}))
    token = broker.issue(workspace_id="ws", run_id="r1")
    with pytest.raises(BrokerScopeError):
        broker.resolve(token, credential_name="missing")


def test_expired_token_rejected() -> None:
    """Tokens past their TTL are rejected as invalid."""
    now = 1000.0

    def clock() -> float:
        return now

    broker = CredentialBroker(
        secret="s",
        resolver=_resolver({}),
        ttl_seconds=60,
        clock=clock,
    )
    token = broker.issue(workspace_id="ws", run_id="r1")
    now = 1_000_000.0  # advance the clock past the TTL
    with pytest.raises(BrokerTokenInvalid):
        broker.parse(token)


def test_tampered_token_rejected() -> None:
    """Flipping a payload bit invalidates the signature."""
    broker = CredentialBroker(secret="s", resolver=_resolver({}))
    token = broker.issue(workspace_id="ws", run_id="r1")
    head, sig = token.split(".")
    head = "A" + head[1:]
    with pytest.raises(BrokerTokenInvalid):
        broker.parse(f"{head}.{sig}")


def test_malformed_token_rejected() -> None:
    """Tokens without the dot separator fail immediately."""
    broker = CredentialBroker(secret="s", resolver=_resolver({}))
    with pytest.raises(BrokerTokenInvalid):
        broker.parse("not-a-token")


def test_revoke_invalidates_future_uses() -> None:
    """A revoked run_id cannot resolve credentials any more."""
    broker = CredentialBroker(secret="s", resolver=_resolver({("ws", "k"): "v"}))
    token = broker.issue(workspace_id="ws", run_id="r1")
    broker.revoke("r1")
    with pytest.raises(BrokerTokenInvalid):
        broker.resolve(token, credential_name="k")


def test_generate_broker_secret_is_url_safe_string() -> None:
    """Generated secrets are URL-safe base64 strings of useful length."""
    secret = generate_broker_secret()
    assert isinstance(secret, str)
    assert len(secret) >= 32
