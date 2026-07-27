"""Central authenticated authorization endpoint for Hosted Apps PKCE."""

from __future__ import annotations
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from orcheo.workspace import WorkspaceMembershipError
from orcheo_backend.app.authentication import RequestContext, authenticate_request
from orcheo_backend.app.hosted_apps.auth_store import get_app_auth_service
from orcheo_backend.app.hosted_apps.internal import _resolve_descriptor
from orcheo_backend.app.hosted_apps.store import get_hosted_apps_repository
from orcheo_backend.app.workspace.dependencies import get_workspace_repository


router = APIRouter(prefix="/hosted-apps/auth", tags=["hosted-apps-auth"])


class AppAuthorizeRequest(BaseModel):
    """Authenticated central authorization request created by the gateway."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(min_length=1, max_length=255)
    redirect_uri: str = Field(min_length=1, max_length=2048)
    code_challenge: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    state: str = Field(min_length=32, max_length=256)


class AppAuthorizeResponse(BaseModel):
    """Exact app callback containing a short-lived single-use code."""

    redirect_url: str


@router.post("/authorize", response_model=AppAuthorizeResponse)
async def authorize_app(
    body: AppAuthorizeRequest,
    auth: Annotated[RequestContext, Depends(authenticate_request)],
    repository: Annotated[Any, Depends(get_hosted_apps_repository)],
) -> AppAuthorizeResponse:
    """Authorize a current workspace member for one exact published app host."""
    try:
        descriptor = await run_in_threadpool(_resolve_descriptor, body.host, repository)
        workspace_id = UUID(descriptor["workspace_id"])
        app_id = UUID(descriptor["app_id"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Hosted app was not found.",
        ) from exc

    allowed_callbacks = {f"https://{body.host}/__orcheo/auth/callback"}
    if body.host.endswith(".localhost"):
        allowed_callbacks.add(f"http://{body.host}/__orcheo/auth/callback")
    if body.redirect_uri not in allowed_callbacks:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Hosted app redirect URI is invalid.",
        )
    try:
        await run_in_threadpool(
            get_workspace_repository().get_membership,
            workspace_id,
            auth.subject,
        )
    except WorkspaceMembershipError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Current workspace membership is required.",
        ) from exc

    code = await run_in_threadpool(
        get_app_auth_service(repository).issue_code,
        app_id=app_id,
        workspace_id=workspace_id,
        user_id=auth.subject,
        redirect_uri=body.redirect_uri,
        code_challenge=body.code_challenge,
    )
    query = urlencode({"code": code, "state": body.state})
    return AppAuthorizeResponse(redirect_url=f"{body.redirect_uri}?{query}")
