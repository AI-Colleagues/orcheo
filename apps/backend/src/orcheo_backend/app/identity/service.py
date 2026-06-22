"""First-party identity service: challenges, verification, and token issuance.

This is the orchestration layer of the passwordless email IdP. It issues
single-use magic-link + OTP challenges, verifies them with attempt lockout,
creates-or-finds the internal :class:`User` on first verification, mints the
HS256 access token validated by ``authentication/``, and manages rotating
refresh-token sessions (refresh + server-side revocation/logout).
"""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from orcheo.identity.errors import (
    IdentityChallengeError,
    IdentityChallengeExpiredError,
    IdentityChallengeLockedError,
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
)
from orcheo.identity.models import (
    AuthEmailChallenge,
    AuthSession,
    ChallengePurpose,
    User,
    normalize_email,
)
from orcheo.identity.repository import IdentityRepository
from orcheo.models.base import _utcnow
from orcheo.workspace.email import (
    AuthChallengeEmail,
    AuthChallengeEmailSender,
)
from orcheo_backend.app.authentication.telemetry import (
    AuthEvent,
    AuthTelemetry,
    auth_telemetry,
)
from orcheo_backend.app.identity.config import IdentityConfig
from orcheo_backend.app.identity.tokens import (
    coerce_user_id,
    generate_magic_link_token,
    generate_otp_code,
    generate_refresh_token,
    hash_secret,
    mint_access_token,
    secrets_match,
)


__all__ = ["IdentityService", "IssuedTokens", "VerificationResult"]


@dataclass(frozen=True)
class IssuedTokens:
    """Access + refresh tokens minted for a session."""

    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class VerificationResult:
    """Outcome of a successful challenge verification."""

    user: User
    tokens: IssuedTokens


class IdentityService:
    """Coordinate the passwordless email login/signup lifecycle."""

    def __init__(
        self,
        repository: IdentityRepository,
        *,
        email_sender: AuthChallengeEmailSender,
        config: IdentityConfig,
        clock: Callable[[], datetime] = _utcnow,
        telemetry: AuthTelemetry | None = None,
    ) -> None:
        """Bind the service to its storage, email transport, and config."""
        self._repository = repository
        self._email_sender = email_sender
        self._config = config
        self._clock = clock
        self._telemetry = telemetry or auth_telemetry

    def _record(
        self,
        event: str,
        status: str,
        *,
        subject: str | None = None,
        ip: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Record an identity telemetry event on the shared auth sink."""
        self._telemetry.record(
            AuthEvent(
                event=event,
                status=status,  # type: ignore[arg-type]
                subject=subject,
                identity_type="user",
                token_id=None,
                ip=ip,
                detail=detail,
            )
        )

    @property
    def repository(self) -> IdentityRepository:
        """Return the backing identity repository."""
        return self._repository

    # -- challenge issuance --------------------------------------------------

    def start_challenge(self, email: str) -> None:
        """Issue a magic-link + OTP challenge and email it.

        No user row is created here; the account is materialized on first
        verification. The response is constant regardless of account existence
        so callers can keep the endpoint anti-enumerative. Raises ``ValueError``
        only on a malformed email (a format error, not an existence oracle).
        """
        normalized = normalize_email(email)
        now = self._clock()
        raw_token = generate_magic_link_token()
        raw_code = generate_otp_code(self._config.otp_digits)
        expires_at = now + timedelta(minutes=self._config.challenge_ttl_minutes)
        challenge = AuthEmailChallenge(
            email=normalized,
            token_hash=hash_secret(raw_token),
            code_hash=hash_secret(raw_code),
            purpose=ChallengePurpose.LOGIN_OR_SIGNUP,
            expires_at=expires_at,
        )
        self._repository.add_challenge(challenge)
        base = self._config.verify_base_url.rstrip("/")
        magic_link_url = f"{base}/auth/verify?token={raw_token}"
        try:
            self._email_sender.send_auth_challenge(
                AuthChallengeEmail(
                    to=normalized,
                    magic_link_url=magic_link_url,
                    otp_code=raw_code,
                    expires_at=expires_at,
                )
            )
        except Exception:
            # Surface delivery failures to telemetry; the caller keeps the
            # response constant (anti-enumeration) and re-raises if it chooses.
            self._record("auth.email_delivery_failure", "failure")
            raise
        self._record("auth.challenge_sent", "success")

    # -- challenge verification ----------------------------------------------

    def verify_token(
        self,
        token: str,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> VerificationResult:
        """Verify a magic-link token and return an authenticated session."""
        try:
            challenge = self._repository.get_challenge_by_token_hash(hash_secret(token))
        except IdentityChallengeNotFoundError as exc:
            raise IdentityChallengeError("Invalid or expired link.") from exc
        self._ensure_redeemable(challenge)
        return self._redeem(challenge, user_agent=user_agent, ip=ip)

    def verify_code(
        self,
        email: str,
        code: str,
        *,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> VerificationResult:
        """Verify an OTP code for an email, applying attempt lockout."""
        normalized = normalize_email(email)
        now = self._clock()
        challenge = self._repository.find_active_challenge_for_email(
            normalized, now=now
        )
        if challenge is None:
            self._record("auth.verify_expired", "failure", ip=ip)
            raise IdentityChallengeError("Invalid or expired code.")
        self._ensure_not_locked(challenge)

        if not secrets_match(code, challenge.code_hash):
            updated = challenge.model_copy(update={"attempts": challenge.attempts + 1})
            self._repository.update_challenge(updated)
            self._ensure_not_locked(updated)
            raise IdentityChallengeError("Invalid or expired code.")

        return self._redeem(challenge, user_agent=user_agent, ip=ip)

    def _ensure_redeemable(self, challenge: AuthEmailChallenge) -> None:
        now = self._clock()
        if challenge.is_consumed() or challenge.is_expired(now=now):
            self._record("auth.verify_expired", "failure")
            raise IdentityChallengeExpiredError("Invalid or expired link.")
        self._ensure_not_locked(challenge)

    def _ensure_not_locked(self, challenge: AuthEmailChallenge) -> None:
        if challenge.attempts >= self._config.otp_max_attempts:
            raise IdentityChallengeLockedError("Too many attempts; request a new code.")

    def _redeem(
        self,
        challenge: AuthEmailChallenge,
        *,
        user_agent: str | None,
        ip: str | None,
    ) -> VerificationResult:
        now = self._clock()
        try:
            consumed = self._repository.consume_challenge(challenge, consumed_at=now)
        except IdentityChallengeNotFoundError as exc:
            self._record("auth.verify_expired", "failure", ip=ip)
            raise IdentityChallengeExpiredError(
                "Invalid or expired challenge."
            ) from exc
        user, created = self._find_or_create_user(consumed.email, now=now)
        tokens = self._issue_session(user, user_agent=user_agent, ip=ip, now=now)
        if created:
            self._record("auth.signup", "success", subject=str(user.id), ip=ip)
        self._record("auth.login", "success", subject=str(user.id), ip=ip)
        return VerificationResult(user=user, tokens=tokens)

    def _find_or_create_user(self, email: str, *, now: datetime) -> tuple[User, bool]:
        existing = self._repository.get_user_by_email(email)
        if existing is None:
            user = User(email=email, email_verified=True, last_login_at=now)
            return self._repository.create_user(user), True
        updated = existing.model_copy(
            update={"email_verified": True, "last_login_at": now}
        )
        return self._repository.update_user(updated), False

    # -- sessions & tokens ---------------------------------------------------

    def _issue_session(
        self,
        user: User,
        *,
        user_agent: str | None,
        ip: str | None,
        now: datetime,
    ) -> IssuedTokens:
        raw_refresh = generate_refresh_token()
        session = AuthSession(
            user_id=user.id,
            refresh_token_hash=hash_secret(raw_refresh),
            expires_at=now + timedelta(days=self._config.session_ttl_days),
            user_agent=user_agent,
            ip=ip,
        )
        self._repository.add_session(session)
        access_token, expires_in = self._mint_access(user, now=now)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=expires_in,
        )

    def _mint_access(self, user: User, *, now: datetime) -> tuple[str, int]:
        return mint_access_token(
            user=user,
            secret=self._config.jwt_secret,
            issuer=self._config.issuer,
            audience=self._config.audience,
            ttl_seconds=self._config.access_ttl_seconds,
            now=now,
        )

    def refresh(self, refresh_token: str) -> IssuedTokens:
        """Rotate a refresh token and mint a new access token.

        The presented refresh token is single-use: a valid presentation rotates
        it (the old hash is replaced), so replay of a consumed token fails.
        """
        now = self._clock()
        try:
            session = self._repository.get_session_by_refresh_hash(
                hash_secret(refresh_token)
            )
        except IdentitySessionNotFoundError as exc:
            raise IdentitySessionNotFoundError(
                "Invalid or revoked refresh token."
            ) from exc
        if not session.is_active(now=now):
            raise IdentitySessionNotFoundError("Invalid or revoked refresh token.")

        user = self._repository.get_user(session.user_id)
        raw_refresh = generate_refresh_token()
        rotated = session.model_copy(
            update={
                "refresh_token_hash": hash_secret(raw_refresh),
                "expires_at": now + timedelta(days=self._config.session_ttl_days),
            }
        )
        self._repository.update_session(rotated)
        access_token, expires_in = self._mint_access(user, now=now)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=expires_in,
        )

    def logout(self, user_id: str | UUID) -> int:
        """Revoke every active session for a user (log out everywhere)."""
        return self._repository.revoke_sessions_for_user(coerce_user_id(user_id))

    def get_user(self, user_id: str | UUID) -> User:
        """Return the user identified by an internal id (the token ``sub``)."""
        return self._repository.get_user(coerce_user_id(user_id))
