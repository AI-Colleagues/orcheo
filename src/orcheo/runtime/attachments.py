"""Attachment resolver protocol for workflow document loading.

Defines the small cross-boundary contract that lets DocumentLoaderNode resolve
attachment bytes without importing orcheo_backend internals.
"""

from __future__ import annotations
from typing import Any, Protocol, runtime_checkable


class AttachmentScope(Protocol):
    """Trusted scope metadata supplied by the ChatKit runtime per request."""

    workspace_id: str
    workflow_id: str | None
    thread_id: str | None
    upload_session_id: str | None


class AttachmentPayload(Protocol):
    """Resolved attachment content returned by the resolver."""

    id: str
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    content: bytes
    metadata: dict[str, Any]


@runtime_checkable
class AttachmentResolver(Protocol):
    """Contract for resolving attachment bytes from an id with scope enforcement.

    The backend implements this and injects an instance via
    ``RunnableConfig["configurable"]["attachment_resolver"]``.  Scope is always
    supplied by the runtime, never by untrusted document payloads.
    """

    async def load_attachment_bytes(
        self,
        attachment_id: str,
        scope: AttachmentScope,
    ) -> AttachmentPayload:
        """Return the attachment payload for *attachment_id* within *scope*.

        Raises a not-found or permission error without revealing whether the
        attachment exists in a different scope.
        """
        ...


@runtime_checkable
class AttachmentUploader(Protocol):
    """Contract for uploading file content from a workflow to blob storage.

    The backend implements this and injects an instance via
    ``RunnableConfig["configurable"]["attachment_uploader"]``.  Scope is fixed
    at injection time; callers supply only content and metadata.

    Returns ``(attachment_id, download_url)`` so callers can embed the URL
    directly in assistant messages without knowing the backend topology.
    """

    async def upload_attachment(
        self,
        content: bytes,
        name: str,
        mime_type: str,
    ) -> tuple[str, str]:
        """Persist *content* and return ``(attachment_id, download_url)``.

        Raises on storage failure.
        """
        ...


__all__ = [
    "AttachmentPayload",
    "AttachmentResolver",
    "AttachmentScope",
    "AttachmentUploader",
]
