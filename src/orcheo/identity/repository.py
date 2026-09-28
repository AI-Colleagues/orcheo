"""Identity repository protocol and an in-memory reference implementation."""

from __future__ import annotations
from datetime import datetime
from typing import Protocol
from uuid import UUID
from orcheo.identity.errors import (
    IdentityChallengeNotFoundError,
    IdentitySessionNotFoundError,
    OAuthAuthorizationRequestNotFoundError,
    OAuthClientNotFoundError,
    UserNotFoundError,
)
from orcheo.identity.models import (
    AuthEmailChallenge,
    AuthSession,
    OAuthAuthorizationRequest,
    OAuthClient,
    User,
    normalize_email,
)
from orcheo.models.base import _utcnow


__all__ = [
    "IdentityRepository",
    "InMemoryIdentityRepository",
]


class IdentityRepository(Protocol):
    """Storage protocol for users, email challenges, and sessions."""

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
        self, challenge: AuthEmailChallenge, *, consumed_at: datetime
    ) -> AuthEmailChallenge:
        """Atomically mark an unconsumed challenge as consumed."""

    def add_session(self, session: AuthSession) -> AuthSession:
        """Persist a new refresh-token session."""

    def get_session(self, session_id: UUID) -> AuthSession:
        """Return the session identified by `session_id`."""

    def get_session_by_refresh_hash(self, refresh_token_hash: str) -> AuthSession:
        """Return the session matching a refresh-token hash."""

    def update_session(self, session: AuthSession) -> AuthSession:
        """Persist rotation/revocation changes for an existing session."""

    def revoke_sessions_for_user(self, user_id: UUID) -> int:
        """Revoke every active session for a user; return the count revoked."""

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
        """Persist the user's decision on an authorization request."""

    def consume_authorization_code(
        self, request_id: str, *, consumed_at: datetime
    ) -> OAuthAuthorizationRequest:
        """Atomically mark an unconsumed authorization code as consumed."""


class InMemoryIdentityRepository:
    """In-memory identity repository used for tests and embedded deployments."""

    def __init__(self) -> None:
        """Initialize empty in-memory storage."""
        self._users: dict[UUID, User] = {}
        self._email_index: dict[str, UUID] = {}
        self._challenges: dict[UUID, AuthEmailChallenge] = {}
        self._sessions: dict[UUID, AuthSession] = {}
        self._oauth_clients: dict[str, OAuthClient] = {}
        self._authorization_requests: dict[str, OAuthAuthorizationRequest] = {}

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
        if challenge.id not in self._challenges:
            raise IdentityChallengeNotFoundError(str(challenge.id))
        self._challenges[challenge.id] = challenge
        return challenge

    def consume_challenge(
        self, challenge: AuthEmailChallenge, *, consumed_at: datetime
    ) -> AuthEmailChallenge:
        """Atomically mark an unconsumed challenge as consumed."""
        current = self._challenges.get(challenge.id)
        if current is None or current.consumed_at is not None:
            raise IdentityChallengeNotFoundError(str(challenge.id))
        consumed = current.model_copy(update={"consumed_at": consumed_at})
        self._challenges[challenge.id] = consumed
        return consumed

    def add_session(self, session: AuthSession) -> AuthSession:
        """Persist a new refresh-token session."""
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
        for session in self._sessions.values():
            if session.refresh_token_hash == refresh_token_hash:
                return session
        raise IdentitySessionNotFoundError(refresh_token_hash)

    def update_session(self, session: AuthSession) -> AuthSession:
        """Persist rotation/revocation changes for an existing session."""
        if session.id not in self._sessions:
            raise IdentitySessionNotFoundError(str(session.id))
        self._sessions[session.id] = session
        return session

    def revoke_sessions_for_user(self, user_id: UUID) -> int:
        """Revoke every active session for a user; return the count revoked."""
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
        """Persist the user's decision on an authorization request."""
        if request.id not in self._authorization_requests:
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
