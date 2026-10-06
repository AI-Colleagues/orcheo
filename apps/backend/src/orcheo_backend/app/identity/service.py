"""First-party identity service: challenges, verification, and token issuance.

This is the orchestration layer of the passwordless email IdP. It emails
single-use sign-in codes, verifies them with attempt lockout,
creates-or-finds the internal :class:`User` on first verification, mints the
HS256 access token validated by ``authentication/``, and manages rotating
refresh-token sessions (refresh + server-side revocation/logout).
"""

from __future__ import annotations
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID
from orcheo.identity.email_domains import is_email_domain_allowed
from orcheo.identity.errors import (
    IdentityChallengeError,
    IdentityChallengeExpiredError,
    IdentityChallengeLockedError,
    IdentityChallengeNotFoundError,
    IdentityEmailDomainNotAllowedError,
    IdentitySessionNotFoundError,
)
from orcheo.identity.models import (
    AuthEmailChallenge,
    AuthSession,
    ChallengePurpose,
    User,
    UserStatus,
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

    @property
    def config(self) -> IdentityConfig:
        """Return the identity tunables."""
        return self._config

    # -- challenge issuance --------------------------------------------------

    def now(self) -> datetime:
        """Return the service clock value for deterministic auth flows."""
        return self._clock()

    def is_email_allowed(self, email: str) -> bool:
        """Return whether ``email`` is inside the sign-in domain allowlist."""
        return is_email_domain_allowed(email, self._config.allowed_email_domains)

    def _ensure_email_allowed(self, email: str, *, ip: str | None = None) -> None:
        if self.is_email_allowed(email):
            return
        self._record("auth.email_domain_rejected", "failure", ip=ip)
        raise IdentityEmailDomainNotAllowedError(
            "Sign-in is limited to approved email domains."
        )

    def start_challenge(self, email: str) -> None:
        """Email a single-use sign-in code.

        No user row is created here; the account is materialized on first
        verification. The response is constant regardless of account existence
        so callers can keep the endpoint anti-enumerative. Raises ``ValueError``
        only on a malformed email (a format error, not an existence oracle), and
        ``IdentityEmailDomainNotAllowedError`` when the domain is not allowed
        (a published policy, not an account-existence oracle).
        """
        normalized = normalize_email(email)
        self._ensure_email_allowed(normalized)
        now = self.now()
        raw_code = generate_otp_code(self._config.otp_digits)
        challenge = AuthEmailChallenge(
            email=normalized,
            code_hash=hash_secret(raw_code),
            purpose=ChallengePurpose.LOGIN_OR_SIGNUP,
            expires_at=now + timedelta(minutes=self._config.challenge_ttl_minutes),
        )
        self._repository.add_challenge(challenge)
        try:
            self._email_sender.send_auth_challenge(
                AuthChallengeEmail(
                    to=normalized,
                    otp_code=raw_code,
                    expires_in_minutes=self._config.challenge_ttl_minutes,
                )
            )
        except Exception:
            # Surface delivery failures to telemetry; the caller keeps the
            # response constant (anti-enumeration) and re-raises if it chooses.
            self._record("auth.email_delivery_failure", "failure")
            raise
        self._record("auth.challenge_sent", "success")

    # -- challenge verification ----------------------------------------------

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
            updated = self._repository.increment_challenge_attempts(
                challenge.id, now=now, max_attempts=self._config.otp_max_attempts
            )
            self._ensure_not_locked(updated)
            raise IdentityChallengeError("Invalid or expired code.")

        return self._redeem(challenge, user_agent=user_agent, ip=ip)

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
        # Re-check at redemption so a tightened allowlist applies to challenges
        # issued before the change.
        self._ensure_email_allowed(challenge.email, ip=ip)
        now = self._clock()
        try:
            consumed = self._repository.consume_challenge(
                challenge, consumed_at=now, max_attempts=self._config.otp_max_attempts
            )
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

    def start_session(
        self,
        user: User,
        *,
        method: str,
        user_agent: str | None = None,
        ip: str | None = None,
    ) -> VerificationResult:
        """Sign in an existing user who proved their identity without a code.

        Passkey sign-in ends here, in the same session and tokens as an emailed
        code. Callers must already have checked that the user may sign in.
        """
        now = self._clock()
        signed_in = self._repository.update_user(
            user.model_copy(update={"last_login_at": now})
        )
        tokens = self._issue_session(signed_in, user_agent=user_agent, ip=ip, now=now)
        self._record(
            "auth.login", "success", subject=str(user.id), ip=ip, detail=method
        )
        return VerificationResult(user=signed_in, tokens=tokens)

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
            created_at=now,
            expires_at=now + timedelta(days=self._config.session_ttl_days),
            user_agent=user_agent,
            ip=ip,
        )
        self._repository.add_session(session)
        access_token, expires_in = self._mint_access(user, now=now, session=session)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=expires_in,
        )

    def _mint_access(
        self,
        user: User,
        *,
        now: datetime,
        session: AuthSession,
    ) -> tuple[str, int]:
        # A session starts at sign-in and keeps its creation time across
        # refreshes, so it is the token's ``auth_time``.
        if session.oauth_client_id is None:
            return mint_access_token(
                user=user,
                secret=self._config.jwt_secret,
                issuer=self._config.issuer,
                audience=self._config.audience,
                ttl_seconds=self._config.access_ttl_seconds,
                now=now,
                auth_time=session.created_at,
            )
        return mint_access_token(
            user=user,
            secret=self._config.jwt_secret,
            issuer=self._config.issuer,
            audience=self._config.audience,
            ttl_seconds=self._config.access_ttl_seconds,
            now=now,
            scopes=session.scopes or (),
            extra_claims={"client_id": session.oauth_client_id},
            auth_time=session.created_at,
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
        if not session.is_active(now=now) or session.oauth_client_id is not None:
            # OAuth grants rotate only through the token endpoint, bound to
            # their client and approved scopes.
            raise IdentitySessionNotFoundError("Invalid or revoked refresh token.")
        return self._rotate_session(session, now=now)

    def _rotate_session(
        self,
        session: AuthSession,
        *,
        now: datetime,
        scopes: list[str] | None = None,
    ) -> IssuedTokens:
        user = self._repository.get_user(session.user_id)
        if session.oauth_client_id is not None and user.status != UserStatus.ACTIVE:
            raise IdentitySessionNotFoundError("Invalid or revoked refresh token.")
        if not self.is_email_allowed(user.email):
            # The allowlist changed after sign-in: end this session.
            self._repository.revoke_sessions_for_user(user.id)
            self._record("auth.email_domain_rejected", "failure", subject=str(user.id))
            raise IdentitySessionNotFoundError("Invalid or revoked refresh token.")
        raw_refresh = generate_refresh_token()
        update: dict[str, object] = {
            "refresh_token_hash": hash_secret(raw_refresh),
            "expires_at": now + timedelta(days=self._config.session_ttl_days),
        }
        if scopes is not None:
            update["scopes"] = scopes
        rotated = session.model_copy(update=update)
        self._repository.rotate_session(
            rotated, previous_refresh_hash=session.refresh_token_hash, now=now
        )
        access_token, expires_in = self._mint_access(user, now=now, session=rotated)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=expires_in,
        )

    # -- OAuth grants --------------------------------------------------------

    def issue_oauth_session(
        self,
        user_id: UUID,
        *,
        client_id: str,
        scopes: list[str],
    ) -> IssuedTokens:
        """Start a refresh-token session granted to an OAuth client.

        Raises:
            IdentitySessionNotFoundError: If the user can no longer sign in.
        """
        now = self._clock()
        user = self._repository.get_user(user_id)
        if user.status != UserStatus.ACTIVE or not self.is_email_allowed(user.email):
            raise IdentitySessionNotFoundError("User can no longer sign in.")
        raw_refresh = generate_refresh_token()
        session = AuthSession(
            user_id=user.id,
            refresh_token_hash=hash_secret(raw_refresh),
            created_at=now,
            expires_at=now + timedelta(days=self._config.session_ttl_days),
            oauth_client_id=client_id,
            scopes=list(scopes),
        )
        self._repository.add_session(session)
        self._record("auth.oauth_grant", "success", subject=str(user.id))
        access_token, expires_in = self._mint_access(user, now=now, session=session)
        return IssuedTokens(
            access_token=access_token,
            refresh_token=raw_refresh,
            expires_in=expires_in,
        )

    def find_oauth_session(
        self, refresh_token: str, *, client_id: str
    ) -> AuthSession | None:
        """Return the active session behind an OAuth client's refresh token."""
        try:
            session = self._repository.get_session_by_refresh_hash(
                hash_secret(refresh_token)
            )
        except IdentitySessionNotFoundError:
            return None
        if session.oauth_client_id != client_id or not session.is_active(
            now=self._clock()
        ):
            return None
        return session

    def refresh_oauth_session(
        self,
        refresh_token: str,
        *,
        client_id: str,
        scopes: list[str],
    ) -> IssuedTokens:
        """Rotate an OAuth client's refresh token, optionally narrowing scopes."""
        session = self.find_oauth_session(refresh_token, client_id=client_id)
        if session is None:
            raise IdentitySessionNotFoundError("Invalid or revoked refresh token.")
        return self._rotate_session(session, now=self._clock(), scopes=scopes)

    def revoke_oauth_session(self, refresh_token: str, *, client_id: str) -> None:
        """Revoke an OAuth client's grant; unknown tokens are ignored."""
        session = self.find_oauth_session(refresh_token, client_id=client_id)
        if session is not None:
            self._repository.update_session(
                session.model_copy(update={"revoked_at": self._clock()})
            )

    def logout(self, user_id: str | UUID) -> int:
        """Revoke every active session for a user (log out everywhere)."""
        return self._repository.revoke_sessions_for_user(coerce_user_id(user_id))

    def get_user(self, user_id: str | UUID) -> User:
        """Return the user identified by an internal id (the token ``sub``)."""
        return self._repository.get_user(coerce_user_id(user_id))
