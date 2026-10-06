"""Identity repository protocol and an in-memory reference implementation."""

from __future__ import annotations
from datetime import datetime
from threading import RLock
from typing import Protocol
from uuid import UUID
from orcheo.identity.errors import (
    IdentityChallengeLockedError,
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
    OAuthAuthorizationRequestNotFoundError,
    OAuthClientNotFoundError,
    PasskeyAlreadyRegisteredError,
    PasskeyChallengeNotFoundError,
    PasskeyNotFoundError,
    UserNotFoundError,
)
from orcheo.identity.models import (
    AuthEmailChallenge,
    AuthSession,
    OAuthAuthorizationRequest,
    OAuthClient,
    Passkey,
    PasskeyCeremony,
    PasskeyChallenge,
    User,
    normalize_email,
)
from orcheo.models.base import _utcnow


__all__ = [
    "IdentityRepository",
    "InMemoryIdentityRepository",
]


class IdentityRepository(Protocol):
    """Storage protocol for users, email challenges, sessions, and passkeys."""

    def create_user(self, user: User) -> User:
        """Persist a new user; raises on a duplicate email."""

    def get_user(self, user_id: UUID) -> User:
        """Return the user identified by `user_id`."""

    def get_user_by_email(self, email: str) -> User | None:
        """Return the user with a matching normalized email, or None."""

    def update_user(self, user: User) -> User:
        """Persist mutable user fields (verification, name, status, login)."""

    def add_challenge(self, challenge: AuthEmailChallenge) -> AuthEmailChallenge:
        """Persist a new email challenge."""

    def get_challenge(self, challenge_id: UUID) -> AuthEmailChallenge:
        """Return the challenge identified by `challenge_id`."""

    def find_active_challenge_for_email(
        self, email: str, *, now: datetime
    ) -> AuthEmailChallenge | None:
        """Return the newest unconsumed, unexpired challenge for an email."""

    def update_challenge(self, challenge: AuthEmailChallenge) -> AuthEmailChallenge:
        """Persist attempt/consumption changes for an existing challenge."""

    def consume_challenge(
        self,
        challenge: AuthEmailChallenge,
        *,
        consumed_at: datetime,
        max_attempts: int | None = None,
    ) -> AuthEmailChallenge:
        """Atomically consume an unexpired challenge below the attempt limit."""

    def increment_challenge_attempts(
        self, challenge_id: UUID, *, now: datetime, max_attempts: int
    ) -> AuthEmailChallenge:
        """Atomically count a failed guess on an active, unlocked challenge."""

    def add_session(self, session: AuthSession) -> AuthSession:
        """Persist a new refresh-token session."""

    def get_session(self, session_id: UUID) -> AuthSession:
        """Return the session identified by `session_id`."""

    def get_session_by_refresh_hash(self, refresh_token_hash: str) -> AuthSession:
        """Return the session matching a refresh-token hash."""

    def update_session(self, session: AuthSession) -> AuthSession:
        """Persist rotation/revocation changes for an existing session.

        Revocation is permanent: a stale copy written by a racing refresh
        cannot clear ``revoked_at``.
        """

    def revoke_sessions_for_user(self, user_id: UUID) -> int:
        """Revoke every active session for a user; return the count revoked."""

    def rotate_session(
        self, session: AuthSession, *, previous_refresh_hash: str, now: datetime
    ) -> AuthSession:
        """Replace a matching, unrevoked, unexpired refresh hash atomically."""

    def add_oauth_client(self, client: OAuthClient) -> OAuthClient:
        """Persist a dynamically registered OAuth client."""

    def get_oauth_client(self, client_id: str) -> OAuthClient:
        """Return the OAuth client identified by `client_id`."""

    def add_authorization_request(
        self, request: OAuthAuthorizationRequest
    ) -> OAuthAuthorizationRequest:
        """Persist a pending OAuth authorization request."""

    def get_authorization_request(self, request_id: str) -> OAuthAuthorizationRequest:
        """Return the authorization request identified by `request_id`."""

    def get_authorization_request_by_code_hash(
        self, code_hash: str
    ) -> OAuthAuthorizationRequest:
        """Return the authorization request that issued a code hash."""

    def update_authorization_request(
        self, request: OAuthAuthorizationRequest
    ) -> OAuthAuthorizationRequest:
        """Persist the user's decision on a still-undecided authorization request.

        Raises:
            OAuthAuthorizationRequestNotFoundError: If the request is unknown
                or already decided.
        """

    def consume_authorization_code(
        self, request_id: str, *, consumed_at: datetime
    ) -> OAuthAuthorizationRequest:
        """Atomically mark an unconsumed authorization code as consumed."""

    def add_passkey(self, passkey: Passkey) -> Passkey:
        """Persist a newly registered passkey.

        Raises:
            PasskeyAlreadyRegisteredError: If the credential id is registered.
        """

    def list_passkeys(self, user_id: UUID) -> list[Passkey]:
        """Return a user's passkeys, oldest first."""

    def get_passkey_by_credential_id(self, credential_id: str) -> Passkey | None:
        """Return the passkey registered under a credential id, or None."""

    def record_passkey_use(
        self,
        passkey_id: UUID,
        *,
        sign_count: int,
        backed_up: bool,
        used_at: datetime,
    ) -> Passkey:
        """Record a verified sign-in; the stored counter never moves backwards.

        Raises:
            PasskeyNotFoundError: If the passkey no longer exists.
        """

    def rename_passkey(self, user_id: UUID, passkey_id: UUID, name: str) -> Passkey:
        """Rename one of a user's passkeys.

        Raises:
            PasskeyNotFoundError: If the user has no such passkey.
        """

    def delete_passkey(self, user_id: UUID, passkey_id: UUID) -> Passkey:
        """Delete one of a user's passkeys and return it.

        Raises:
            PasskeyNotFoundError: If the user has no such passkey.
        """

    def add_passkey_challenge(self, challenge: PasskeyChallenge) -> PasskeyChallenge:
        """Persist a ceremony challenge, purging challenges that have expired."""

    def consume_passkey_challenge(
        self,
        challenge_id: UUID,
        *,
        ceremony: PasskeyCeremony,
        user_id: UUID | None,
        now: datetime,
    ) -> PasskeyChallenge:
        """Atomically remove and return an unexpired challenge.

        The challenge must have been issued for ``ceremony`` and to
        ``user_id`` (None for sign-in challenges), so it can be used once.

        Raises:
            PasskeyChallengeNotFoundError: If no such challenge is still valid.
        """


class InMemoryIdentityRepository:
    """In-memory identity repository used for tests and embedded deployments."""

    def __init__(self) -> None:
        """Initialize empty in-memory storage."""
        self._lock = RLock()
        self._users: dict[UUID, User] = {}
        self._email_index: dict[str, UUID] = {}
        self._challenges: dict[UUID, AuthEmailChallenge] = {}
        self._sessions: dict[UUID, AuthSession] = {}
        self._oauth_clients: dict[str, OAuthClient] = {}
        self._authorization_requests: dict[str, OAuthAuthorizationRequest] = {}
        self._passkeys: dict[UUID, Passkey] = {}
        self._passkey_challenges: dict[UUID, PasskeyChallenge] = {}

    def create_user(self, user: User) -> User:
        """Persist a new user; raises on a duplicate email."""
        normalized = normalize_email(user.email)
        if normalized in self._email_index:
            msg = f"A user already exists for {normalized}"
            raise ValueError(msg)
        self._users[user.id] = user
        self._email_index[normalized] = user.id
        return user

    def get_user(self, user_id: UUID) -> User:
        """Return the user identified by `user_id`."""
        user = self._users.get(user_id)
        if user is None:
            raise UserNotFoundError(str(user_id))
        return user

    def get_user_by_email(self, email: str) -> User | None:
        """Return the user with a matching normalized email, or None."""
        normalized = normalize_email(email)
        user_id = self._email_index.get(normalized)
        if user_id is None:
            return None
        return self._users.get(user_id)

    def update_user(self, user: User) -> User:
        """Persist mutable user fields (verification, name, status, login)."""
        if user.id not in self._users:
            raise UserNotFoundError(str(user.id))
        self._users[user.id] = user
        self._email_index[normalize_email(user.email)] = user.id
        return user

    def add_challenge(self, challenge: AuthEmailChallenge) -> AuthEmailChallenge:
        """Persist a new email challenge."""
        with self._lock:
            self._challenges[challenge.id] = challenge
            return challenge

    def get_challenge(self, challenge_id: UUID) -> AuthEmailChallenge:
        """Return the challenge identified by `challenge_id`."""
        challenge = self._challenges.get(challenge_id)
        if challenge is None:
            raise IdentityChallengeNotFoundError(str(challenge_id))
        return challenge

    def find_active_challenge_for_email(
        self, email: str, *, now: datetime
    ) -> AuthEmailChallenge | None:
        """Return the newest unconsumed, unexpired challenge for an email."""
        with self._lock:
            normalized = normalize_email(email)
            candidates = [
                challenge
                for challenge in self._challenges.values()
                if challenge.email == normalized
                and not challenge.is_consumed()
                and not challenge.is_expired(now=now)
            ]
            if not candidates:
                return None
            return max(candidates, key=lambda c: c.created_at)

    def update_challenge(self, challenge: AuthEmailChallenge) -> AuthEmailChallenge:
        """Persist attempt/consumption changes for an existing challenge."""
        with self._lock:
            if challenge.id not in self._challenges:
                raise IdentityChallengeNotFoundError(str(challenge.id))
            self._challenges[challenge.id] = challenge
            return challenge

    def consume_challenge(
        self,
        challenge: AuthEmailChallenge,
        *,
        consumed_at: datetime,
        max_attempts: int | None = None,
    ) -> AuthEmailChallenge:
        """Atomically consume an unexpired challenge below the attempt limit."""
        with self._lock:
            current = self._challenges.get(challenge.id)
            if (
                current is None
                or current.is_consumed()
                or current.is_expired(now=consumed_at)
            ):
                raise IdentityChallengeNotFoundError(str(challenge.id))
            if max_attempts is not None and current.attempts >= max_attempts:
                raise IdentityChallengeLockedError(
                    "Too many attempts; request a new code."
                )
            consumed = current.model_copy(update={"consumed_at": consumed_at})
            self._challenges[challenge.id] = consumed
            return consumed

    def increment_challenge_attempts(
        self, challenge_id: UUID, *, now: datetime, max_attempts: int
    ) -> AuthEmailChallenge:
        """Atomically count a failed guess on an active, unlocked challenge."""
        with self._lock:
            current = self._challenges.get(challenge_id)
            if current is None or current.is_consumed() or current.is_expired(now=now):
                raise IdentityChallengeNotFoundError(str(challenge_id))
            if current.attempts >= max_attempts:
                raise IdentityChallengeLockedError(
                    "Too many attempts; request a new code."
                )
            updated = current.model_copy(update={"attempts": current.attempts + 1})
            self._challenges[challenge_id] = updated
            return updated

    def add_session(self, session: AuthSession) -> AuthSession:
        """Persist a new refresh-token session."""
        with self._lock:
            self._sessions[session.id] = session
            return session

    def get_session(self, session_id: UUID) -> AuthSession:
        """Return the session identified by `session_id`."""
        session = self._sessions.get(session_id)
        if session is None:
            raise IdentitySessionNotFoundError(str(session_id))
        return session

    def get_session_by_refresh_hash(self, refresh_token_hash: str) -> AuthSession:
        """Return the session matching a refresh-token hash."""
        with self._lock:
            for session in self._sessions.values():
                if session.refresh_token_hash == refresh_token_hash:
                    return session
            raise IdentitySessionNotFoundError(refresh_token_hash)

    def update_session(self, session: AuthSession) -> AuthSession:
        """Persist rotation/revocation changes; revocation is permanent."""
        with self._lock:
            current = self._sessions.get(session.id)
            if current is None:
                raise IdentitySessionNotFoundError(str(session.id))
            if current.revoked_at is not None:
                session = session.model_copy(update={"revoked_at": current.revoked_at})
            self._sessions[session.id] = session
            return session

    def rotate_session(
        self, session: AuthSession, *, previous_refresh_hash: str, now: datetime
    ) -> AuthSession:
        """Replace an active session's refresh hash exactly once."""
        with self._lock:
            current = self._sessions.get(session.id)
            if (
                current is None
                or current.refresh_token_hash != previous_refresh_hash
                or not current.is_active(now=now)
            ):
                raise IdentitySessionNotFoundError(str(session.id))
            self._sessions[session.id] = session
            return session

    def revoke_sessions_for_user(self, user_id: UUID) -> int:
        """Revoke every active session for a user; return the count revoked."""
        with self._lock:
            now = _utcnow()
            revoked = 0
            for session_id, session in self._sessions.items():
                if session.user_id == user_id and session.revoked_at is None:
                    self._sessions[session_id] = session.model_copy(
                        update={"revoked_at": now}
                    )
                    revoked += 1
            return revoked

    def add_oauth_client(self, client: OAuthClient) -> OAuthClient:
        """Persist a dynamically registered OAuth client."""
        self._oauth_clients[client.client_id] = client
        return client

    def get_oauth_client(self, client_id: str) -> OAuthClient:
        """Return the OAuth client identified by `client_id`."""
        client = self._oauth_clients.get(client_id)
        if client is None:
            raise OAuthClientNotFoundError(client_id)
        return client

    def add_authorization_request(
        self, request: OAuthAuthorizationRequest
    ) -> OAuthAuthorizationRequest:
        """Persist a pending OAuth authorization request."""
        self._authorization_requests[request.id] = request
        return request

    def get_authorization_request(self, request_id: str) -> OAuthAuthorizationRequest:
        """Return the authorization request identified by `request_id`."""
        request = self._authorization_requests.get(request_id)
        if request is None:
            raise OAuthAuthorizationRequestNotFoundError(request_id)
        return request

    def get_authorization_request_by_code_hash(
        self, code_hash: str
    ) -> OAuthAuthorizationRequest:
        """Return the authorization request that issued a code hash."""
        for request in self._authorization_requests.values():
            if request.code_hash == code_hash:
                return request
        raise OAuthAuthorizationRequestNotFoundError(code_hash)

    def update_authorization_request(
        self, request: OAuthAuthorizationRequest
    ) -> OAuthAuthorizationRequest:
        """Persist the user's decision on a still-undecided authorization request."""
        current = self._authorization_requests.get(request.id)
        if current is None or current.decided_at is not None:
            raise OAuthAuthorizationRequestNotFoundError(request.id)
        self._authorization_requests[request.id] = request
        return request

    def consume_authorization_code(
        self, request_id: str, *, consumed_at: datetime
    ) -> OAuthAuthorizationRequest:
        """Atomically mark an unconsumed authorization code as consumed."""
        current = self._authorization_requests.get(request_id)
        if current is None or current.consumed_at is not None:
            raise OAuthAuthorizationRequestNotFoundError(request_id)
        consumed = current.model_copy(update={"consumed_at": consumed_at})
        self._authorization_requests[request_id] = consumed
        return consumed

    def add_passkey(self, passkey: Passkey) -> Passkey:
        """Persist a newly registered passkey; raises on a duplicate credential."""
        with self._lock:
            if self.get_passkey_by_credential_id(passkey.credential_id) is not None:
                raise PasskeyAlreadyRegisteredError(passkey.credential_id)
            self._passkeys[passkey.id] = passkey
            return passkey

    def list_passkeys(self, user_id: UUID) -> list[Passkey]:
        """Return a user's passkeys, oldest first."""
        with self._lock:
            owned = [p for p in self._passkeys.values() if p.user_id == user_id]
        return sorted(owned, key=lambda p: (p.created_at, str(p.id)))

    def get_passkey_by_credential_id(self, credential_id: str) -> Passkey | None:
        """Return the passkey registered under a credential id, or None."""
        with self._lock:
            return next(
                (
                    passkey
                    for passkey in self._passkeys.values()
                    if passkey.credential_id == credential_id
                ),
                None,
            )

    def record_passkey_use(
        self,
        passkey_id: UUID,
        *,
        sign_count: int,
        backed_up: bool,
        used_at: datetime,
    ) -> Passkey:
        """Record a verified sign-in; the stored counter never moves backwards."""
        with self._lock:
            current = self._passkeys.get(passkey_id)
            if current is None:
                raise PasskeyNotFoundError(str(passkey_id))
            updated = current.model_copy(
                update={
                    "sign_count": max(current.sign_count, sign_count),
                    "backed_up": backed_up,
                    "last_used_at": used_at,
                }
            )
            self._passkeys[passkey_id] = updated
            return updated

    def rename_passkey(self, user_id: UUID, passkey_id: UUID, name: str) -> Passkey:
        """Rename one of a user's passkeys."""
        with self._lock:
            renamed = self._owned_passkey(user_id, passkey_id).model_copy(
                update={"name": name}
            )
            self._passkeys[passkey_id] = renamed
            return renamed

    def delete_passkey(self, user_id: UUID, passkey_id: UUID) -> Passkey:
        """Delete one of a user's passkeys and return it."""
        with self._lock:
            removed = self._owned_passkey(user_id, passkey_id)
            del self._passkeys[passkey_id]
            return removed

    def _owned_passkey(self, user_id: UUID, passkey_id: UUID) -> Passkey:
        passkey = self._passkeys.get(passkey_id)
        if passkey is None or passkey.user_id != user_id:
            raise PasskeyNotFoundError(str(passkey_id))
        return passkey

    def add_passkey_challenge(self, challenge: PasskeyChallenge) -> PasskeyChallenge:
        """Persist a ceremony challenge, purging challenges that have expired."""
        with self._lock:
            for challenge_id, existing in list(self._passkey_challenges.items()):
                if existing.is_expired(now=challenge.created_at):
                    del self._passkey_challenges[challenge_id]
            self._passkey_challenges[challenge.id] = challenge
            return challenge

    def consume_passkey_challenge(
        self,
        challenge_id: UUID,
        *,
        ceremony: PasskeyCeremony,
        user_id: UUID | None,
        now: datetime,
    ) -> PasskeyChallenge:
        """Atomically remove and return an unexpired challenge."""
        with self._lock:
            challenge = self._passkey_challenges.get(challenge_id)
            if (
                challenge is None
                or challenge.ceremony != ceremony
                or challenge.user_id != user_id
                or challenge.is_expired(now=now)
            ):
                raise PasskeyChallengeNotFoundError(str(challenge_id))
            del self._passkey_challenges[challenge_id]
            return challenge
