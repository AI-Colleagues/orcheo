"""Authentication helper routes (development utilities)."""

from __future__ import annotations
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel
from orcheo_backend.app.authentication.settings import load_auth_settings


router = APIRouter()


class DevLoginRequest(BaseModel):
    """Payload accepted by the development login endpoint."""

    provider: str = "google"
    email: str | None = None
    name: str | None = None


class DevLoginResponse(BaseModel):
    """Response returned after a successful development login."""

    provider: str
    subject: str
    display_name: str


@router.post(
    "/auth/dev/login",
    response_model=DevLoginResponse,
)
async def dev_login(request: DevLoginRequest, response: Response) -> DevLoginResponse:
    """Set a development session cookie to simulate OAuth login locally."""
    settings = load_auth_settings()
    if not settings.dev_login_enabled or not settings.dev_login_cookie_name:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"message": "Developer login is disabled for this environment."},
        )

    subject = request.email or f"{request.provider}-dev@orcheo.local"
    display_name = request.name or subject.split("@")[0]
    session_value = f"{subject}:{uuid4().hex}"
    response.set_cookie(
        key=settings.dev_login_cookie_name,
        value=session_value,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=7 * 24 * 60 * 60,
        path="/",
    )
    return DevLoginResponse(
        provider=request.provider,
        subject=subject,
        display_name=display_name,
    )


@router.post(
    "/auth/dev/logout",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def dev_logout(response: Response) -> Response:
    """Clear the development session cookie."""
    settings = load_auth_settings()
    if not settings.dev_login_enabled or not settings.dev_login_cookie_name:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"message": "Developer login is disabled for this environment."},
        )
    response.delete_cookie(
        key=settings.dev_login_cookie_name,
        path="/",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
