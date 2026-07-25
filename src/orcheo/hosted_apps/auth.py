"""Hashed PKCE authorization codes and exact-host app sessions."""

from __future__ import annotations
import base64
import hashlib
import hmac
import secrets
from datetime import timedelta
from threading import RLock
from uuid import UUID
from orcheo.hosted_apps.models import AppSession, AuthorizationCode
from orcheo.models.base import _utcnow


__all__ = ["AppAuthError", "AppAuthService", "IssuedAppSession"]


class AppAuthError(PermissionError):
    """Fail-closed app authorization error."""


class IssuedAppSession(tuple):
    """Raw host-only cookie secret plus its hashed server record."""

    __slots__ = ()

    @property
    def secret(self) -> str:
        """Return the raw session secret exactly once at issuance."""
        return self[0]

    @property
    def session(self) -> AppSession:
        """Return the hashed server-side session record."""
        return self[1]


class AppAuthService:
    """Thread-safe reference implementation of app code exchange and sessions."""

    def __init__(
        self,
        *,
        code_ttl_seconds: int = 300,
        absolute_seconds: int = 43_200,
        idle_seconds: int = 1_800,
    ) -> None:
        """Initialize explicit code, absolute, and idle lifetimes."""
        self._code_ttl = code_ttl_seconds
        self._absolute = absolute_seconds
        self._idle = idle_seconds
        self._lock = RLock()
        self._codes: dict[str, AuthorizationCode] = {}
        self._sessions: dict[str, AppSession] = {}

    def issue_code(
        self,
        *,
        app_id: UUID,
        workspace_id: UUID,
        user_id: str,
        redirect_uri: str,
        code_challenge: str,
    ) -> str:
        """Issue a hashed, short-lived, single-use authorization code."""
        raw = secrets.token_urlsafe(32)
        code = AuthorizationCode(
            code_hash=_hash(raw),
            app_id=app_id,
            workspace_id=workspace_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            expires_at=_utcnow() + timedelta(seconds=self._code_ttl),
        )
        with self._lock:
            self._codes[code.code_hash] = code
        return raw

    def exchange(
        self,
        *,
        raw_code: str,
        verifier: str,
        app_host: str,
        redirect_uri: str,
        runtime_generation: int,
        current_member: bool,
    ) -> IssuedAppSession:
        """Atomically consume a PKCE code and issue an exact-host app session."""
        code_hash = _hash(raw_code)
        with self._lock:
            code = self._codes.get(code_hash)
            now = _utcnow()
            if (
                code is None
                or code.consumed_at is not None
                or code.expires_at <= now
                or code.redirect_uri != redirect_uri
                or not current_member
            ):
                raise AppAuthError("App authorization code is invalid or expired.")
            if not hmac.compare_digest(_pkce_challenge(verifier), code.code_challenge):
                raise AppAuthError("App authorization PKCE verification failed.")
            code.consumed_at = now
            raw_secret = secrets.token_urlsafe(32)
            session = AppSession(
                secret_hash=_hash(raw_secret),
                app_id=code.app_id,
                workspace_id=code.workspace_id,
                app_host=app_host,
                user_id=code.user_id,
                runtime_generation=runtime_generation,
                expires_at=now + timedelta(seconds=self._absolute),
                idle_expires_at=now + timedelta(seconds=self._idle),
                last_seen_at=now,
            )
            self._sessions[session.secret_hash] = session
            return IssuedAppSession((raw_secret, session))

    def introspect(
        self,
        raw_secret: str,
        *,
        app_host: str,
        runtime_generation: int,
        current_member: bool,
    ) -> AppSession:
        """Recheck host, generation, membership, revocation, and both expiries."""
        with self._lock:
            session = self._sessions.get(_hash(raw_secret))
            now = _utcnow()
            if (
                session is None
                or session.revoked_at is not None
                or session.app_host != app_host
                or session.runtime_generation != runtime_generation
                or session.expires_at <= now
                or session.idle_expires_at <= now
                or not current_member
            ):
                raise AppAuthError("App session is invalid or expired.")
            session.last_seen_at = now
            session.idle_expires_at = min(
                session.expires_at, now + timedelta(seconds=self._idle)
            )
            return session

    def revoke(self, raw_secret: str) -> None:
        """Revoke a session idempotently using only its hashed cookie secret."""
        with self._lock:
            session = self._sessions.get(_hash(raw_secret))
            if session is not None and session.revoked_at is None:
                session.revoked_at = _utcnow()


def pkce_challenge(verifier: str) -> str:
    """Return an RFC 7636 S256 base64url challenge without padding."""
    return _pkce_challenge(verifier)


def _hash(value: str) -> str:
    """Hash a raw browser capability before persistence."""
    return hashlib.sha256(value.encode()).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
