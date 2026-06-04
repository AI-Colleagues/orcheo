"""Internal attachment upload endpoint for sandboxed workflow runs.

Authenticated via the per-run credential broker token so sandbox child
processes can upload workflow-produced files to blob storage without
requiring a ChatKit JWT or database credentials.
"""

from __future__ import annotations
import logging
import os
from typing import Annotated, Any
from fastapi import APIRouter, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse
from orcheo.sandbox.broker import (
    BrokerScopeError,
    BrokerTokenInvalidError,
    CredentialBroker,
)


logger = logging.getLogger(__name__)


def _resolve_download_base_url() -> str:
    raw = os.environ.get("ORCHEO_API_URL", "").strip()
    if not raw:
        raw = os.environ.get("ORCHEO_API_BASE_URL", "").strip()
    return raw.rstrip("/")


def _get_attachment_service() -> Any | None:
    """Return the ChatKit attachment service, or None if unavailable."""
    try:
        from orcheo_backend.app import get_chatkit_server  # noqa: PLC0415

        server = get_chatkit_server()
        if server is None:
            return None
        return getattr(server.store, "attachment_service", None)
    except Exception:  # noqa: BLE001
        return None


def build_internal_attachment_router(broker: CredentialBroker) -> APIRouter:
    """Build the FastAPI router for internal sandbox attachment uploads."""
    router = APIRouter(prefix="/internal/attachments", tags=["internal"])

    @router.post("/upload")
    async def upload_attachment(
        file: UploadFile,
        workflow_id: Annotated[str | None, Form()] = None,  # noqa: B008
        thread_id: Annotated[str | None, Form()] = None,  # noqa: B008
        upload_session_id: Annotated[str | None, Form()] = None,  # noqa: B008
        authorization: Annotated[str | None, Header()] = None,
        x_orcheo_workspace: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        """Upload a workflow-produced file from inside a sandbox container."""
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing bearer token",
            )
        token_str = authorization.split(" ", 1)[1].strip()
        try:
            token = broker.parse(token_str)
        except BrokerTokenInvalidError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
            ) from exc
        except BrokerScopeError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
            ) from exc

        workspace_id = token.workspace_id
        if x_orcheo_workspace is not None and x_orcheo_workspace != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-workspace upload rejected",
            )

        service = _get_attachment_service()
        if service is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Attachment storage unavailable",
            )

        content = await file.read()
        safe_name = (file.filename or "upload").strip() or "upload"
        mime_type = file.content_type or "application/octet-stream"

        try:
            attachment_id, _ = await service.save_attachment(
                workspace_id=workspace_id,
                workflow_id=workflow_id or "",
                thread_id=thread_id or None,
                upload_session_id=upload_session_id or None,
                auth_mode="sandbox",
                actor_subject=None,
                attachment_type="file",
                name=safe_name,
                mime_type=mime_type,
                content=content,
                blob_backend=service.blob_backend,
            )
        except Exception as exc:
            logger.error(
                "Internal sandbox attachment upload failed",
                extra={
                    "workspace_id": workspace_id,
                    "workflow_id": workflow_id,
                    "error": str(exc),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to store attachment",
            ) from exc

        base = _resolve_download_base_url()
        download_url = f"{base}/api/chatkit/attachments/{attachment_id}"

        logger.info(
            "Sandbox attachment uploaded",
            extra={
                "attachment_id": attachment_id,
                "workspace_id": workspace_id,
                "workflow_id": workflow_id,
                "size_bytes": len(content),
            },
        )
        return JSONResponse(
            content={"id": attachment_id, "download_url": download_url},
            status_code=status.HTTP_200_OK,
        )

    return router
