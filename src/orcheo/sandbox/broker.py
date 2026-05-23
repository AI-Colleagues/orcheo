"""Credential Broker — run-scoped credential delivery for sandboxed code.

The Credential Broker replaces the worker's environment-variable credential
injection for the sandboxed execution path. The lifecycle is:

1. When the worker starts a workflow run, it asks the broker to ``issue`` a
   short-lived token. The token encodes ``workspace_id`` and ``run_id``
   server-side via HMAC; tenant code inside the sandbox cannot mint or alter
   it.
2. The sandbox calls ``POST /internal/credentials/resolve`` with the token
   and a credential name. The broker validates the token, *pins* the
   workspace from the token (never from a tenant-controlled header), and
   resolves the credential against the vault. A cross-workspace request
   returns 403.
3. When the run finishes, the worker calls ``revoke`` so a leaked token
   stops working immediately even if the TTL hasn't elapsed.

This module contains:

- ``CredentialBroker``: token issuance, validation, and resolution against a
  vault. Framework-agnostic — the FastAPI router in the backend wraps it.
- ``BrokerToken``: typed token payload.
- ``BrokerTokenInvalid`` / ``BrokerScopeError``: explicit error types so the
  HTTP layer can map them to 401/403.
"""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Protocol


class CredentialResolverFn(Protocol):
    """Function that resolves a credential name within a workspace scope."""

    def __call__(self, *, workspace_id: str, credential_name: str) -> str:
        """Return the credential value or raise ``KeyError`` if not found."""


class RevocationStore(Protocol):
    """Storage for revoked run identifiers shared by broker processes."""

    def revoke(self, run_id: str, *, ttl_seconds: int) -> None:
        """Mark ``run_id`` revoked until all issued tokens have expired."""

    def is_revoked(self, run_id: str) -> bool:
        """Return whether ``run_id`` is currently revoked."""


class InMemoryRevocationStore:
    """In-memory revocation state for tests and single-process use."""

    def __init__(self, *, clock: object | None = None) -> None:
        """Initialize with an optional injectable time source."""
        self._clock: object = clock if clock is not None else time.time
        self._revoked_until: dict[str, float] = {}

    def revoke(self, run_id: str, *, ttl_seconds: int) -> None:
        """Mark ``run_id`` revoked for ``ttl_seconds``."""
        self._revoked_until[run_id] = (
            float(self._clock()) + ttl_seconds  # type: ignore[operator]
        )

    def is_revoked(self, run_id: str) -> bool:
        """Return whether the revocation has not expired."""
        expires_at = self._revoked_until.get(run_id)
        if expires_at is None:
            return False
        if float(self._clock()) > expires_at:  # type: ignore[operator]
            self._revoked_until.pop(run_id, None)
            return False
        return True


class RedisRevocationStore:
    """Redis-backed revocation state shared by backend and worker brokers."""

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "orcheo:sandbox:credential-revoked:",
        client: object | None = None,
    ) -> None:
        """Initialize Redis key storage with optional test client injection."""
        if client is None:
            import redis

            client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client = client
        self._key_prefix = key_prefix

    def revoke(self, run_id: str, *, ttl_seconds: int) -> None:
        """Persist the revoked run id with bounded retention."""
        self._client.set(  # type: ignore[attr-defined]
            f"{self._key_prefix}{run_id}",
            "1",
            ex=max(1, ttl_seconds),
        )

    def is_revoked(self, run_id: str) -> bool:
        """Return whether a revocation key exists in Redis."""
        return bool(
            self._client.exists(  # type: ignore[attr-defined]
                f"{self._key_prefix}{run_id}"
            )
        )


@dataclass(frozen=True)
class BrokerToken:
    """Run-scoped token payload."""

    workspace_id: str
    run_id: str
    issued_at: float
    expires_at: float

    def to_json(self) -> str:
        """Serialize the payload as canonical JSON."""
        return json.dumps(
            {
                "workspace_id": self.workspace_id,
                "run_id": self.run_id,
                "issued_at": self.issued_at,
                "expires_at": self.expires_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str) -> BrokerToken:
        """Parse a payload back into a ``BrokerToken``."""
        data = json.loads(raw)
        return cls(
            workspace_id=str(data["workspace_id"]),
            run_id=str(data["run_id"]),
            issued_at=float(data["issued_at"]),
            expires_at=float(data["expires_at"]),
        )


class BrokerError(Exception):
    """Base for broker errors."""


class BrokerTokenInvalidError(BrokerError):
    """Token is malformed, signature failed, or it has expired."""


# Back-compat alias for callers expecting the original name.
BrokerTokenInvalid = BrokerTokenInvalidError


class BrokerScopeError(BrokerError):
    """Token is valid but cannot reach the requested credential."""


class CredentialBroker:
    """Issue and validate run-scoped credential tokens.

    Tokens are ``<b64 payload>.<b64 signature>`` where the signature is
    ``HMAC-SHA256(secret, payload_bytes)``. Validation re-derives the
    signature with the broker's secret, so a tenant cannot forge a token or
    alter its ``workspace_id``.

    A revocation store holds revoked ``run_id``s so leaked tokens stop working
    immediately across processes. Unit tests default to an in-memory store.
    """

    def __init__(
        self,
        secret: bytes | str,
        resolver: CredentialResolverFn,
        *,
        ttl_seconds: int = 300,
        clock: object | None = None,
        revocation_store: RevocationStore | None = None,
    ) -> None:
        """Initialize the broker.

        Args:
            secret: HMAC secret. Strings are encoded as UTF-8.
            resolver: Function that resolves a credential by workspace + name.
            ttl_seconds: Default token TTL.
            clock: Optional injectable clock (callable returning seconds).
            revocation_store: Optional shared revoked-run storage.
        """
        self._secret = secret.encode("utf-8") if isinstance(secret, str) else secret
        self._resolver = resolver
        self._ttl = ttl_seconds
        self._clock: object = clock if clock is not None else time.time
        self._revocations = revocation_store or InMemoryRevocationStore(
            clock=self._clock
        )

    def issue(self, *, workspace_id: str, run_id: str) -> str:
        """Mint a new run-scoped token string."""
        now = float(self._clock())  # type: ignore[operator]
        token = BrokerToken(
            workspace_id=workspace_id,
            run_id=run_id,
            issued_at=now,
            expires_at=now + self._ttl,
        )
        payload = token.to_json().encode("utf-8")
        signature = self._sign(payload)
        return (
            base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
            + "."
            + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
        )

    def revoke(self, run_id: str) -> None:
        """Revoke every outstanding token for ``run_id``."""
        self._revocations.revoke(run_id, ttl_seconds=self._ttl)

    def parse(self, token_str: str) -> BrokerToken:
        """Validate the signature and return the payload."""
        try:
            payload_b64, signature_b64 = token_str.split(".", 1)
        except ValueError as exc:
            msg = "Token is not in <payload>.<signature> form"
            raise BrokerTokenInvalid(msg) from exc
        payload = _b64decode(payload_b64)
        signature = _b64decode(signature_b64)
        expected = self._sign(payload)
        if not hmac.compare_digest(signature, expected):
            msg = "Token signature mismatch"
            raise BrokerTokenInvalid(msg)
        try:
            token = BrokerToken.from_json(payload.decode("utf-8"))
        except (ValueError, KeyError) as exc:
            msg = "Token payload is malformed"
            raise BrokerTokenInvalid(msg) from exc
        now = float(self._clock())  # type: ignore[operator]
        if now > token.expires_at:
            msg = "Token has expired"
            raise BrokerTokenInvalid(msg)
        if self._revocations.is_revoked(token.run_id):
            msg = "Token has been revoked"
            raise BrokerTokenInvalid(msg)
        return token

    def resolve(
        self,
        token_str: str,
        *,
        credential_name: str,
        requested_workspace_id: str | None = None,
    ) -> tuple[BrokerToken, str]:
        """Resolve ``credential_name`` for the workspace pinned by the token.

        ``requested_workspace_id`` is the value tenant code *claims* — it is
        ignored if it doesn't match the token. If it conflicts, the broker
        raises ``BrokerScopeError`` (HTTP 403). This is the explicit
        anti-spoof defense that the design calls out.

        Returns:
            Tuple of (token, resolved value).
        """
        token = self.parse(token_str)
        if (
            requested_workspace_id is not None
            and requested_workspace_id != token.workspace_id
        ):
            msg = (
                "Cross-workspace credential request rejected: "
                f"token={token.workspace_id} requested={requested_workspace_id}"
            )
            raise BrokerScopeError(msg)
        try:
            value = self._resolver(
                workspace_id=token.workspace_id,
                credential_name=credential_name,
            )
        except KeyError as exc:
            msg = f"Credential '{credential_name}' not found in workspace scope"
            raise BrokerScopeError(msg) from exc
        return token, value

    def _sign(self, payload: bytes) -> bytes:
        """Compute the HMAC-SHA256 signature over ``payload``."""
        return hmac.new(self._secret, payload, hashlib.sha256).digest()


def _b64decode(value: str) -> bytes:
    """URL-safe base64 decode with automatic padding."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def generate_broker_secret() -> str:
    """Return a fresh 256-bit secret encoded as URL-safe base64."""
    return secrets.token_urlsafe(32)


def _cli() -> None:  # pragma: no cover - tiny CLI helper
    """Tiny CLI for ``python -m orcheo.sandbox.broker --gen-secret``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Credential Broker helpers")
    parser.add_argument(
        "--gen-secret",
        action="store_true",
        help="Print a fresh URL-safe 256-bit secret suitable for "
        "ORCHEO_CREDENTIAL_BROKER_SECRET.",
    )
    args = parser.parse_args()
    if args.gen_secret:
        sys.stdout.write(generate_broker_secret() + "\n")
        return
    parser.print_help()


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    _cli()
