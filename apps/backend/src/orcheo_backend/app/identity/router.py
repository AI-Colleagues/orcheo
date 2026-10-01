"""First-party passwordless auth endpoints.

Exposes the email entry point (``/auth/email/start``), sign-in code
verification (``/auth/email/verify``), session refresh/logout, and the
current-user probe (``/auth/me``). The start endpoint is constant-response and
rate limited to avoid acting as an account-existence or email-abuse oracle.
"""

from __future__ import annotations
import logging
from datetime import UTC, datetime
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from orcheo.identity.errors import (
    IdentityChallengeExpiredError,
    IdentityChallengeLockedError,
    IdentityEmailDomainNotAllowedError,
    IdentitySessionNotFoundError,
    UserNotFoundError,
)
from orcheo.identity.models import User
from orcheo_backend.app.authentication import (
    RequestContext,
    get_auth_rate_limiter,
    get_request_context,
)
from orcheo_backend.app.database_errors import DATABASE_UNAVAILABLE_ERRORS
from orcheo_backend.app.identity.dependencies import (
    IdentityServiceDep,
    get_client_ip,
)
from orcheo_backend.app.identity.service import IssuedTokens


logger = logging.getLogger(__name__)


def _database_unavailable() -> HTTPException:
    return HTTPException(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": "auth.service_unavailable",
            "message": "Sign-in is temporarily unavailable. Please try again later.",
        },
    )


router = APIRouter(prefix="/auth", tags=["auth"])


class EmailStartRequest(BaseModel):
    """Payload for the email entry point (serves both signup and login)."""

    email: str
    intent: Literal["login", "signup"] = "login"


class EmailStartResponse(BaseModel):
    """Constant response that never reveals whether an account exists."""

    status: Literal["sent"] = "sent"


class EmailVerifyRequest(BaseModel):
    """Verify the sign-in code emailed to an address."""

    email: str
    code: str


class UserProfile(BaseModel):
    """Public user profile returned to the client."""

    id: str
    email: str
    email_verified: bool
    name: str | None = None

    @classmethod
    def from_user(cls, user: User) -> UserProfile:
        """Project an internal user onto the public profile shape."""
        return cls(
            id=str(user.id),
            email=user.email,
            email_verified=user.email_verified,
            name=user.name,
        )


class SessionResponse(BaseModel):
    """Access + refresh tokens plus the authenticated profile."""

    access_token: str
    refresh_token: str
    expires_in: int
    user: UserProfile


class RefreshRequest(BaseModel):
    """Rotate a refresh token into a fresh access token."""

    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """Access + refresh tokens returned by the refresh endpoint."""

    access_token: str
    refresh_token: str
    expires_in: int


def _enforce_start_rate_limits(
    ip: str | None, email_key: str, *, now: datetime | None = None
) -> None:
    now = now or datetime.now(tz=UTC)
    limiter = get_auth_rate_limiter()
    limiter.check_ip(ip, now=now)
    limiter.check_identity(email_key, now=now)


def _email_domain_forbidden(exc: IdentityEmailDomainNotAllowedError) -> HTTPException:
    return HTTPException(
        status.HTTP_403_FORBIDDEN,
        detail={"code": "auth.email_domain_not_allowed", "message": str(exc)},
    )


@router.post("/email/start", response_model=EmailStartResponse)
async def email_start(
    payload: EmailStartRequest,
    service: IdentityServiceDep,
    ip: Annotated[str | None, Depends(get_client_ip)],
) -> EmailStartResponse:
    """Issue a passwordless challenge; always responds identically."""
    _enforce_start_rate_limits(
        ip, f"auth-email:{payload.email.strip().lower()}", now=service.now()
    )
    try:
        await run_in_threadpool(service.start_challenge, payload.email)
    except IdentityEmailDomainNotAllowedError as exc:
        # The domain allowlist is deployment policy, not account existence,
        # so rejecting it explicitly does not create an enumeration oracle.
        raise _email_domain_forbidden(exc) from exc
    except ValueError:
        # Malformed email — respond identically to avoid a format/existence
        # oracle. Nothing was sent.
        logger.info("Rejected malformed email at auth start")
    except Exception:  # noqa: BLE001 - delivery failures must not leak existence
        logger.exception("Failed to deliver auth challenge email")
    return EmailStartResponse()


@router.post("/email/verify", response_model=SessionResponse)
async def email_verify(
    payload: EmailVerifyRequest,
    service: IdentityServiceDep,
    request: Request,
    ip: Annotated[str | None, Depends(get_client_ip)],
) -> SessionResponse:
    """Verify an emailed sign-in code and start a session."""
    get_auth_rate_limiter().check_ip(ip, now=service.now())
    user_agent = request.headers.get("User-Agent")
    try:
        result = await run_in_threadpool(
            service.verify_code,
            payload.email,
            payload.code,
            user_agent=user_agent,
            ip=ip,
        )
    except DATABASE_UNAVAILABLE_ERRORS as exc:
        logger.warning("Identity database operation unavailable", exc_info=True)
        raise _database_unavailable() from exc
    except IdentityChallengeLockedError as exc:
        raise HTTPException(
            status.HTTP_423_LOCKED, detail={"message": str(exc)}
        ) from exc
    except IdentityChallengeExpiredError as exc:
        raise HTTPException(status.HTTP_410_GONE, detail={"message": str(exc)}) from exc
    except IdentityEmailDomainNotAllowedError as exc:
        raise _email_domain_forbidden(exc) from exc
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"message": str(exc)}
        ) from exc
    except Exception as exc:  # noqa: BLE001 - invalid challenge family
        # Covers IdentityChallengeError / not-found: an invalid challenge.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid or expired challenge."},
        ) from exc

    return _session_response(result.user, result.tokens)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshRequest,
    service: IdentityServiceDep,
    ip: Annotated[str | None, Depends(get_client_ip)],
) -> TokenResponse:
    """Rotate a refresh token and mint a new access token."""
    get_auth_rate_limiter().check_ip(ip, now=service.now())
    try:
        tokens = await run_in_threadpool(service.refresh, payload.refresh_token)
    except DATABASE_UNAVAILABLE_ERRORS as exc:
        logger.warning("Identity database operation unavailable", exc_info=True)
        raise _database_unavailable() from exc
    except IdentitySessionNotFoundError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"message": "Invalid or revoked refresh token."},
        ) from exc
    return TokenResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    service: IdentityServiceDep,
    auth: Annotated[RequestContext, Depends(get_request_context)],
) -> Response:
    """Revoke the authenticated user's sessions (log out everywhere)."""
    try:
        await run_in_threadpool(service.logout, auth.subject)
    except DATABASE_UNAVAILABLE_ERRORS as exc:
        logger.warning("Identity database operation unavailable", exc_info=True)
        raise _database_unavailable() from exc
    except (ValueError, UserNotFoundError):
        # Idempotent: an unknown/odd subject still yields a clean logout.
        logger.info("Logout for unresolved subject; clearing client session")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserProfile)
async def me(
    service: IdentityServiceDep,
    auth: Annotated[RequestContext, Depends(get_request_context)],
) -> UserProfile:
    """Return the authenticated user's profile."""
    try:
        user = await run_in_threadpool(service.get_user, auth.subject)
    except DATABASE_UNAVAILABLE_ERRORS as exc:
        logger.warning("Identity database operation unavailable", exc_info=True)
        raise _database_unavailable() from exc
    except (ValueError, UserNotFoundError) as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"message": "User not found."}
        ) from exc
    return UserProfile.from_user(user)


def _session_response(user: User, tokens: IssuedTokens) -> SessionResponse:
    return SessionResponse(
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_in=tokens.expires_in,
        user=UserProfile.from_user(user),
    )


__all__ = ["router"]
