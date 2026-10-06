"""Extra coverage for first-party identity token helpers."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest

from orcheo.identity.models import User
from orcheo_backend.app.identity.tokens import (
    generate_otp_code,
    mint_access_token,
)


def test_generate_otp_code_rejects_short_length() -> None:
    with pytest.raises(ValueError, match="at least 4 digits"):
        generate_otp_code(3)


def test_mint_access_token_applies_extra_claims() -> None:
    user = User(
        id=uuid4(), email="alice@example.com", email_verified=True, name="Alice"
    )
    now = datetime.now(tz=UTC)

    token, expires_in = mint_access_token(
        user=user,
        secret="secret",
        issuer="https://issuer.example.com",
        audience="audience",
        ttl_seconds=300,
        now=now,
        extra_claims={"workspace_id": "ws-1", "purpose": "login"},
    )

    payload = jwt.decode(
        token,
        "secret",
        algorithms=["HS256"],
        audience="audience",
        issuer="https://issuer.example.com",
    )
    assert expires_in == 300
    assert payload["workspace_id"] == "ws-1"
    assert payload["purpose"] == "login"


def test_mint_access_token_carries_the_sign_in_time() -> None:
    user = User(email="alice@example.com")
    signed_in = datetime.now(tz=UTC) - timedelta(hours=2)

    token, _ = mint_access_token(
        user=user,
        secret="secret",
        issuer="https://issuer.example.com",
        audience=None,
        ttl_seconds=300,
        now=datetime.now(tz=UTC),
        auth_time=signed_in,
    )
    untimed, _ = mint_access_token(
        user=user,
        secret="secret",
        issuer="https://issuer.example.com",
        audience=None,
        ttl_seconds=300,
        now=datetime.now(tz=UTC),
    )

    claims = jwt.decode(token, "secret", algorithms=["HS256"])
    assert claims["auth_time"] == int(signed_in.timestamp())
    assert "auth_time" not in jwt.decode(untimed, "secret", algorithms=["HS256"])
