"""Passkey (WebAuthn) endpoints of the first-party IdP.

Passkey sign-in (``/auth/passkey/login/*``) is public and usernameless and
returns the same session as an emailed code. Adding, listing, renaming, and
removing passkeys need a signed-in Studio user, and adding one also needs a
recent sign-in. Database outages surface as 503 through the app-wide handler.
"""

from __future__ import annotations
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from orcheo.identity.errors import (
    IdentityEmailDomainNotAllowedError,
    PasskeyAlreadyRegisteredError,
    PasskeyChallengeNotFoundError,
    PasskeyNotFoundError,
    UserNotFoundError,
)
from orcheo.identity.models import Passkey
from orcheo_backend.app.authentication import (
    RequestContext,
    get_auth_rate_limiter,
    get_request_context,
)
from orcheo_backend.app.identity.dependencies import (
    PasskeyServiceDep,
    get_client_ip,
)
from orcheo_backend.app.identity.passkeys import (
    PasskeyCeremonyOptions,
    PasskeyServiceError,
    passkey_user_handle,
)
from orcheo_backend.app.identity.router import (
    SessionResponse,
    _email_domain_forbidden,
    _session_response,
)
from orcheo_backend.app.identity.tokens import coerce_user_id


__all__ = ["router"]

router = APIRouter(prefix="/auth", tags=["auth"])

AuthContext = Annotated[RequestContext, Depends(get_request_context)]
ClientIp = Annotated[str | None, Depends(get_client_ip)]


class PasskeyOptionsResponse(BaseModel):
    """Options for one browser passkey ceremony."""

    challenge_id: UUID
    options: dict[str, Any]


class PasskeyCredentialRequest(BaseModel):
    """A browser passkey response to a previously issued challenge."""

    challenge_id: UUID
    credential: dict[str, Any]


class PasskeyRegisterRequest(PasskeyCredentialRequest):
    """A new passkey's registration response, with an optional name."""

    name: str | None = Field(default=None, max_length=200)


class PasskeyRenameRequest(BaseModel):
    """A new name for a passkey."""

    name: str = Field(min_length=1, max_length=200)


class PasskeySummary(BaseModel):
    """A passkey as shown to its owner."""

    id: UUID
    name: str
    credential_id: str
    transports: list[str]
    backup_eligible: bool
    backed_up: bool
    created_at: datetime
    last_used_at: datetime | None = None

    @classmethod
    def from_passkey(cls, passkey: Passkey) -> PasskeySummary:
        """Project a stored passkey onto its public shape."""
        return cls(
            id=passkey.id,
            name=passkey.name,
            credential_id=passkey.credential_id,
            transports=passkey.transports,
            backup_eligible=passkey.backup_eligible,
            backed_up=passkey.backed_up,
            created_at=passkey.created_at,
            last_used_at=passkey.last_used_at,
        )


class PasskeyListResponse(BaseModel):
    """A user's passkeys plus what the browser needs to sync its own list.

    ``rp_id`` is null when this server cannot run passkey ceremonies.
    """

    rp_id: str | None
    user_handle: str
    passkeys: list[PasskeySummary]


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, detail={"code": code, "message": message})


async def _run[**P, T](func: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T:
    """Run a blocking passkey operation, mapping domain errors to HTTP errors."""
    try:
        return await run_in_threadpool(func, *args, **kwargs)
    except PasskeyServiceError as exc:
        raise _error(exc.status_code, exc.code, str(exc)) from exc
    except PasskeyChallengeNotFoundError as exc:
        raise _error(
            status.HTTP_410_GONE,
            "auth.passkey_challenge_expired",
            "This passkey request has expired. Please try again.",
        ) from exc
    except PasskeyAlreadyRegisteredError as exc:
        raise _error(
            status.HTTP_409_CONFLICT,
            "auth.passkey_already_registered",
            "This passkey is already registered.",
        ) from exc
    except (PasskeyNotFoundError, UserNotFoundError) as exc:
        raise _error(
            status.HTTP_404_NOT_FOUND, "auth.passkey_not_found", "Passkey not found."
        ) from exc
    except IdentityEmailDomainNotAllowedError as exc:
        raise _email_domain_forbidden(exc) from exc


def _signed_in_user(auth: RequestContext) -> UUID:
    """Return the Studio user behind a request, refusing any other identity."""
    if auth.identity_type == "user":
        try:
            return coerce_user_id(auth.subject)
        except ValueError:
            pass
    raise _error(
        status.HTTP_403_FORBIDDEN,
        "auth.user_required",
        "Passkeys can only be managed from a signed-in Studio session.",
    )


def _options_response(options: PasskeyCeremonyOptions) -> PasskeyOptionsResponse:
    return PasskeyOptionsResponse(
        challenge_id=options.challenge_id, options=options.options
    )


@router.post("/passkey/login/options", response_model=PasskeyOptionsResponse)
async def passkey_login_options(
    service: PasskeyServiceDep, ip: ClientIp
) -> PasskeyOptionsResponse:
    """Start a usernameless passkey sign-in."""
    get_auth_rate_limiter().check_ip(ip, now=service.identity.now())
    return _options_response(await _run(service.begin_authentication))


@router.post("/passkey/login/verify", response_model=SessionResponse)
async def passkey_login_verify(
    payload: PasskeyCredentialRequest,
    service: PasskeyServiceDep,
    request: Request,
    ip: ClientIp,
) -> SessionResponse:
    """Verify a passkey sign-in and start a session."""
    get_auth_rate_limiter().check_ip(ip, now=service.identity.now())
    result = await _run(
        service.finish_authentication,
        payload.challenge_id,
        payload.credential,
        user_agent=request.headers.get("User-Agent"),
        ip=ip,
    )
    return _session_response(result.user, result.tokens)


@router.post("/passkey/register/options", response_model=PasskeyOptionsResponse)
async def passkey_register_options(
    service: PasskeyServiceDep, auth: AuthContext
) -> PasskeyOptionsResponse:
    """Start adding a passkey to the signed-in user's account."""
    user_id = _signed_in_user(auth)
    options = await _run(
        service.begin_registration, user_id, auth_time=auth.claims.get("auth_time")
    )
    return _options_response(options)


@router.post(
    "/passkey/register/verify",
    response_model=PasskeySummary,
    status_code=status.HTTP_201_CREATED,
)
async def passkey_register_verify(
    payload: PasskeyRegisterRequest,
    service: PasskeyServiceDep,
    request: Request,
    auth: AuthContext,
) -> PasskeySummary:
    """Verify and add a new passkey to the signed-in user's account."""
    user_id = _signed_in_user(auth)
    passkey = await _run(
        service.finish_registration,
        user_id,
        payload.challenge_id,
        payload.credential,
        name=payload.name,
        user_agent=request.headers.get("User-Agent"),
    )
    return PasskeySummary.from_passkey(passkey)


@router.get("/passkeys", response_model=PasskeyListResponse)
async def list_passkeys(
    service: PasskeyServiceDep, auth: AuthContext
) -> PasskeyListResponse:
    """List the signed-in user's passkeys."""
    user_id = _signed_in_user(auth)
    passkeys = await _run(service.list_passkeys, user_id)
    return PasskeyListResponse(
        rp_id=service.rp_id,
        user_handle=passkey_user_handle(user_id),
        passkeys=[PasskeySummary.from_passkey(passkey) for passkey in passkeys],
    )


@router.patch("/passkeys/{passkey_id}", response_model=PasskeySummary)
async def rename_passkey(
    passkey_id: UUID,
    payload: PasskeyRenameRequest,
    service: PasskeyServiceDep,
    auth: AuthContext,
) -> PasskeySummary:
    """Rename one of the signed-in user's passkeys."""
    user_id = _signed_in_user(auth)
    passkey = await _run(service.rename_passkey, user_id, passkey_id, payload.name)
    return PasskeySummary.from_passkey(passkey)


@router.delete("/passkeys/{passkey_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_passkey(
    passkey_id: UUID, service: PasskeyServiceDep, auth: AuthContext
) -> Response:
    """Remove one of the signed-in user's passkeys."""
    user_id = _signed_in_user(auth)
    await _run(service.delete_passkey, user_id, passkey_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
