"""Attachment resolver protocol and sandbox transport helpers.

Defines the small cross-boundary contract that lets workflow nodes resolve
attachment bytes without importing ``orcheo_backend`` internals.
"""

from __future__ import annotations
import dataclasses
import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
import httpx


_ATTACHMENT_RESOLVER_MARKER = "__orcheo_attachment_resolver__"
_ATTACHMENT_SCOPE_MARKER = "__orcheo_attachment_scope__"


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
    ``RunnableConfig["configurable"]["attachment_resolver"]``. Scope is always
    supplied by the runtime, never by untrusted document payloads.
    """

    async def load_attachment_bytes(
        self,
        attachment_id: str,
        scope: AttachmentScope,
    ) -> AttachmentPayload:
        """Return the attachment payload for *attachment_id* within *scope*."""
        ...


@runtime_checkable
class AttachmentUploader(Protocol):
    """Contract for uploading file content from a workflow to blob storage.

    The backend implements this and injects an instance via
    ``RunnableConfig["configurable"]["attachment_uploader"]``. Scope is fixed
    at injection time; callers supply only content and metadata.
    """

    async def upload_attachment(
        self,
        content: bytes,
        name: str,
        mime_type: str,
    ) -> tuple[str, str]:
        """Persist *content* and return ``(attachment_id, download_url)``."""
        ...


@dataclass(frozen=True, slots=True)
class AttachmentScopeRecord:
    """Concrete attachment scope representation used by sandbox hydration."""

    workspace_id: str
    workflow_id: str | None = None
    thread_id: str | None = None
    upload_session_id: str | None = None


@dataclass(frozen=True, slots=True)
class AttachmentPayloadRecord:
    """Concrete attachment payload returned by sandbox-side resolver proxies."""

    id: str
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    content: bytes
    metadata: dict[str, Any]


class ChatKitAttachmentResolverProxy:
    """Resolve workflow attachments via the public ChatKit download endpoint."""

    def __init__(self, base_url: str) -> None:
        """Store the backend origin used for attachment downloads."""
        self._base_url = base_url.rstrip("/")

    async def load_attachment_bytes(
        self,
        attachment_id: str,
        scope: AttachmentScope,
    ) -> AttachmentPayloadRecord:
        """Fetch an attachment from the backend's public download endpoint."""
        del scope
        url = f"{self._base_url}/api/chatkit/attachments/{attachment_id}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
        if not response.is_success:
            raise RuntimeError(
                f"Failed to load attachment {attachment_id!r}: "
                f"{response.status_code} {response.text}"
            )

        content = bytes(response.content)
        sha256 = hashlib.sha256(content).hexdigest()
        mime_type = response.headers.get("content-type", "application/octet-stream")
        name = _filename_from_content_disposition(
            response.headers.get("content-disposition")
        )
        if not name:
            name = attachment_id
        metadata = {
            "download_url": url,
            "source": "chatkit_download",
        }
        return AttachmentPayloadRecord(
            id=attachment_id,
            name=name,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=sha256,
            content=content,
            metadata=metadata,
        )


def serialize_attachment_runtime_config(
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a sandbox-safe copy of a runtime config mapping.

    Attachment resolver and scope values are converted into plain JSON-safe
    payloads so the config can cross the sandbox dispatch boundary. Attachment
    uploaders are intentionally dropped in sandboxed runs because the current
    sandbox image does not expose a supported upload proxy.
    """
    if not isinstance(config, Mapping):
        return {}

    sanitized = dict(config)
    configurable = sanitized.get("configurable")
    if not isinstance(configurable, Mapping):
        return sanitized

    payload = dict(configurable)
    resolver = payload.get("attachment_resolver")
    if resolver is not None:
        payload["attachment_resolver"] = {
            _ATTACHMENT_RESOLVER_MARKER: {
                "base_url": _resolve_public_backend_base_url(),
            }
        }

    scope = payload.get("attachment_scope")
    if scope is not None:
        scope_payload = _serialize_attachment_scope(scope)
        if scope_payload is not None:
            payload["attachment_scope"] = scope_payload
        else:
            payload.pop("attachment_scope", None)

    payload.pop("attachment_uploader", None)
    sanitized["configurable"] = payload
    return sanitized


def hydrate_attachment_runtime_config(
    config: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Hydrate sandbox-safe attachment descriptors back into runtime objects."""
    if not isinstance(config, Mapping):
        return {}

    hydrated = dict(config)
    configurable = hydrated.get("configurable")
    if not isinstance(configurable, Mapping):
        return hydrated

    payload = dict(configurable)
    resolver = payload.get("attachment_resolver")
    if isinstance(resolver, Mapping):
        resolver_payload = resolver.get(_ATTACHMENT_RESOLVER_MARKER)
        if isinstance(resolver_payload, Mapping):
            base_url = resolver_payload.get("base_url")
            if isinstance(base_url, str) and base_url.strip():
                payload["attachment_resolver"] = ChatKitAttachmentResolverProxy(
                    base_url.strip()
                )

    scope = payload.get("attachment_scope")
    if isinstance(scope, Mapping):
        scope_payload = scope.get(_ATTACHMENT_SCOPE_MARKER)
        if isinstance(scope_payload, Mapping):
            hydrated_scope = _hydrate_attachment_scope(scope_payload)
            if hydrated_scope is not None:
                payload["attachment_scope"] = hydrated_scope

    hydrated["configurable"] = payload
    return hydrated


def _resolve_public_backend_base_url() -> str:
    """Return the backend origin used for attachment download links."""
    raw = os.environ.get("ORCHEO_API_URL", "").strip()
    if not raw:
        raw = os.environ.get("ORCHEO_API_BASE_URL", "").strip()
    if not raw:
        raw = "http://localhost:2025"
    return raw.rstrip("/")


def _serialize_attachment_scope(scope: Any) -> dict[str, Any] | None:
    """Serialize an attachment scope object to a JSON-safe payload."""
    if scope is None:
        return None

    if isinstance(scope, Mapping):
        payload = dict(scope)
    elif dataclasses.is_dataclass(scope):
        payload = dataclasses.asdict(scope)
    else:
        payload = {}
        for key in ("workspace_id", "workflow_id", "thread_id", "upload_session_id"):
            value = getattr(scope, key, None)
            if value is not None:
                payload[key] = value

    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return None

    return {
        _ATTACHMENT_SCOPE_MARKER: {
            "workspace_id": workspace_id.strip(),
            "workflow_id": payload.get("workflow_id"),
            "thread_id": payload.get("thread_id"),
            "upload_session_id": payload.get("upload_session_id"),
        }
    }


def _hydrate_attachment_scope(
    payload: Mapping[str, Any],
) -> AttachmentScopeRecord | None:
    """Hydrate a serialized attachment scope payload."""
    workspace_id = payload.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id.strip():
        return None
    workflow_id = payload.get("workflow_id")
    thread_id = payload.get("thread_id")
    upload_session_id = payload.get("upload_session_id")
    return AttachmentScopeRecord(
        workspace_id=workspace_id.strip(),
        workflow_id=workflow_id if isinstance(workflow_id, str) else None,
        thread_id=thread_id if isinstance(thread_id, str) else None,
        upload_session_id=(
            upload_session_id if isinstance(upload_session_id, str) else None
        ),
    )


def _filename_from_content_disposition(content_disposition: str | None) -> str:
    """Extract a safe filename from a Content-Disposition header."""
    if not content_disposition:
        return ""
    match = re.search(r'filename="?([^";]+)"?', content_disposition)
    if match is None:
        return ""
    return match.group(1).strip()


__all__ = [
    "AttachmentPayload",
    "AttachmentPayloadRecord",
    "AttachmentResolver",
    "AttachmentScope",
    "AttachmentScopeRecord",
    "AttachmentUploader",
    "ChatKitAttachmentResolverProxy",
    "hydrate_attachment_runtime_config",
    "serialize_attachment_runtime_config",
]
