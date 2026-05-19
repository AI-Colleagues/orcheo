"""FastAPI router that exposes the Credential Broker to sandboxes.

The router is mounted by the backend factory at ``/internal/credentials`` and
wraps :class:`orcheo.sandbox.broker.CredentialBroker`. It is intentionally
**internal**: the network boundary (nftables) only allows the Envoy forward
proxy and the broker URL to be reachable from a sandbox, never the public
API.

Per the design doc, the workspace context is *pinned by the token*. Tenant
code may include an ``X-Orcheo-Workspace`` header for telemetry, but the
broker never trusts it for authorization decisions — if the header conflicts
with the token's workspace_id, the request is rejected with 403.
"""

from __future__ import annotations
from typing import Annotated
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from orcheo.sandbox.broker import (
    BrokerScopeError,
    BrokerTokenInvalidError,
    CredentialBroker,
)


class ResolveCredentialRequest(BaseModel):
    """Payload posted by sandboxed code to resolve a credential."""

    run_id: str = Field(min_length=1)
    credential_name: str = Field(min_length=1)


class ResolveCredentialResponse(BaseModel):
    """Successful broker response."""

    value: str
    expires_at: float
    workspace_id: str


def build_credential_broker_router(broker: CredentialBroker) -> APIRouter:
    """Build the FastAPI router for the Credential Broker."""
    router = APIRouter(prefix="/internal/credentials", tags=["internal"])

    @router.post(
        "/resolve",
        response_model=ResolveCredentialResponse,
    )
    async def resolve(
        request: Request,
        payload: ResolveCredentialRequest,
        authorization: Annotated[str | None, Header()] = None,
        x_orcheo_workspace: Annotated[str | None, Header()] = None,
    ) -> ResolveCredentialResponse:
        del request  # access pattern is bearer-token only
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        token_str = authorization.split(" ", 1)[1].strip()
        try:
            token, value = broker.resolve(
                token_str,
                credential_name=payload.credential_name,
                requested_workspace_id=x_orcheo_workspace,
            )
        except BrokerTokenInvalidError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except BrokerScopeError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        if token.run_id != payload.run_id:
            raise HTTPException(
                status_code=403,
                detail="run_id in payload does not match token",
            )
        return ResolveCredentialResponse(
            value=value,
            expires_at=token.expires_at,
            workspace_id=token.workspace_id,
        )

    return router
