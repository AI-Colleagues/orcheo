"""Attachment resolver protocol and sandbox transport helpers.

Defines the small cross-boundary contract that lets workflow nodes resolve
attachment bytes without importing ``orcheo_backend`` internals.
"""

from __future__ import annotations
import dataclasses
import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse
import httpx


_ATTACHMENT_RESOLVER_MARKER = "__orcheo_attachment_resolver__"
_ATTACHMENT_SCOPE_MARKER = "__orcheo_attachment_scope__"
_ATTACHMENT_UPLOADER_MARKER = "__orcheo_attachment_uploader__"


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
        ...  # pragma: no cover


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
        ...  # pragma: no cover


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

    def __init__(self, base_url: str | Sequence[str]) -> None:
        """Store candidate origins used for attachment downloads."""
        if isinstance(base_url, str):
            candidates = [base_url]
        else:
            candidates = list(base_url)
        self._base_urls = tuple(_normalize_base_urls(candidates))

    async def load_attachment_bytes(
        self,
        attachment_id: str,
        scope: AttachmentScope,
    ) -> AttachmentPayloadRecord:
        """Fetch an attachment from the backend's public download endpoint."""
        del scope
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=30.0) as client:
            for base_url in self._base_urls:
                url = f"{base_url}/api/chatkit/attachments/{attachment_id}"
                try:
                    response = await client.get(url)
                except Exception as exc:  # noqa: BLE001 - continue to fallback
                    errors.append(f"{base_url}: {type(exc).__name__}: {exc}")
                    continue
                if not response.is_success:
                    errors.append(f"{base_url}: {response.status_code} {response.text}")
                    continue

                content = bytes(response.content)
                sha256 = hashlib.sha256(content).hexdigest()
                mime_type = response.headers.get(
                    "content-type", "application/octet-stream"
                )
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

        raise RuntimeError(
            f"Failed to load attachment {attachment_id!r}: "
            + "; ".join(errors or ["no download endpoints configured"])
        )


class ChatKitAttachmentUploaderProxy:
    """Upload workflow-produced attachments via the internal relay endpoint.

    Uses the broker token from the sandbox environment to authenticate against
    the credential-relay, which forwards the request to the backend's internal
    attachment upload endpoint.
    """

    def __init__(
        self,
        base_url: str | Sequence[str],
        *,
        workflow_id: str | None = None,
        thread_id: str | None = None,
        upload_session_id: str | None = None,
    ) -> None:
        """Store candidate origins and optional upload-session context."""
        if isinstance(base_url, str):
            candidates = [base_url]
        else:
            candidates = list(base_url)
        self._base_urls = tuple(_normalize_base_urls(candidates))
        self._workflow_id = workflow_id
        self._thread_id = thread_id
        self._upload_session_id = upload_session_id

    async def upload_attachment(
        self,
        content: bytes,
        name: str,
        mime_type: str,
    ) -> tuple[str, str]:
        """Upload content and return ``(attachment_id, download_url)``."""
        broker_token = os.getenv("ORCHEO_BROKER_TOKEN", "").strip()
        errors: list[str] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for base_url in self._base_urls:
                url = f"{base_url}/api/chatkit/attachments/upload"
                headers: dict[str, str] = {}
                if broker_token:
                    headers["Authorization"] = f"Bearer {broker_token}"

                form_data: dict[str, str] = {}
                if self._workflow_id:
                    form_data["workflow_id"] = self._workflow_id
                if self._thread_id:
                    form_data["thread_id"] = self._thread_id
                if self._upload_session_id:
                    form_data["upload_session_id"] = self._upload_session_id

                try:
                    response = await client.post(
                        url,
                        files={"file": (name, content, mime_type)},
                        data=form_data,
                        headers=headers,
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{base_url}: {type(exc).__name__}: {exc}")
                    continue

                if not response.is_success:
                    errors.append(f"{base_url}: {response.status_code} {response.text}")
                    continue

                result = response.json()
                attachment_id = result.get("id") or result.get("attachment_id")
                if not attachment_id:
                    errors.append(f"{base_url}: missing attachment id in response")
                    continue

                download_url = result.get("download_url") or (
                    f"{base_url}/api/chatkit/attachments/{attachment_id}"
                )
                return str(attachment_id), download_url

        raise RuntimeError(
            f"Failed to upload attachment {name!r}: "
            + "; ".join(errors or ["no upload endpoints configured"])
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
    original_scope = payload.get("attachment_scope")

    resolver = payload.get("attachment_resolver")
    if resolver is not None:
        payload["attachment_resolver"] = {
            _ATTACHMENT_RESOLVER_MARKER: {
                "base_urls": _resolve_public_attachment_base_urls(),
            }
        }

    scope = payload.get("attachment_scope")
    if scope is not None:
        scope_payload = _serialize_attachment_scope(scope)
        if scope_payload is not None:
            payload["attachment_scope"] = scope_payload
        else:
            payload.pop("attachment_scope", None)

    uploader = payload.pop("attachment_uploader", None)
    if uploader is not None:
        if isinstance(uploader, Mapping) and _ATTACHMENT_UPLOADER_MARKER in uploader:
            payload["attachment_uploader"] = uploader
        elif original_scope is not None:
            base_urls = _resolve_public_attachment_base_urls()
            uploader_payload = _serialize_attachment_uploader(original_scope, base_urls)
            if uploader_payload is not None:
                payload["attachment_uploader"] = uploader_payload

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
    _hydrate_attachment_resolver(payload)
    _hydrate_attachment_uploader(payload)

    scope = payload.get("attachment_scope")
    if isinstance(scope, Mapping):
        scope_payload = scope.get(_ATTACHMENT_SCOPE_MARKER)
        if isinstance(scope_payload, Mapping):
            hydrated_scope = _hydrate_attachment_scope(scope_payload)
            if hydrated_scope is not None:
                payload["attachment_scope"] = hydrated_scope

    hydrated["configurable"] = payload
    return hydrated


def _serialize_attachment_uploader(
    scope: Any,
    base_urls: list[str],
) -> dict[str, Any] | None:
    """Return a sandbox-safe marker for an attachment uploader bound to *scope*."""
    if not base_urls:
        return None

    if isinstance(scope, Mapping):
        marker_payload = scope.get(_ATTACHMENT_SCOPE_MARKER)
        if isinstance(marker_payload, Mapping):
            scope_data: dict[str, Any] = dict(marker_payload)
        else:
            scope_data = dict(scope)
    elif dataclasses.is_dataclass(scope) and not isinstance(scope, type):
        scope_data = dataclasses.asdict(scope)
    else:
        scope_data = {}
        for key in ("workflow_id", "thread_id", "upload_session_id"):
            value = getattr(scope, key, None)
            if value is not None:
                scope_data[key] = value

    return {
        _ATTACHMENT_UPLOADER_MARKER: {
            "base_urls": base_urls,
            "workflow_id": scope_data.get("workflow_id"),
            "thread_id": scope_data.get("thread_id"),
            "upload_session_id": scope_data.get("upload_session_id"),
        }
    }


def _hydrate_attachment_uploader(payload: dict[str, Any]) -> None:
    """Hydrate a serialized attachment uploader marker in place."""
    uploader = payload.get("attachment_uploader")
    if not isinstance(uploader, Mapping):
        return
    uploader_payload = uploader.get(_ATTACHMENT_UPLOADER_MARKER)
    if not isinstance(uploader_payload, Mapping):
        return

    base_urls_raw = uploader_payload.get("base_urls")
    if not isinstance(base_urls_raw, Sequence) or isinstance(base_urls_raw, str):
        return
    normalized = [u.strip() for u in base_urls_raw if isinstance(u, str) and u.strip()]
    if not normalized:
        return

    payload["attachment_uploader"] = ChatKitAttachmentUploaderProxy(
        normalized,
        workflow_id=uploader_payload.get("workflow_id"),
        thread_id=uploader_payload.get("thread_id"),
        upload_session_id=uploader_payload.get("upload_session_id"),
    )


def _hydrate_attachment_resolver(payload: dict[str, Any]) -> None:
    """Hydrate a serialized attachment resolver marker in place."""
    resolver = payload.get("attachment_resolver")
    if not isinstance(resolver, Mapping):
        return
    resolver_payload = resolver.get(_ATTACHMENT_RESOLVER_MARKER)
    if not isinstance(resolver_payload, Mapping):
        return

    base_urls = resolver_payload.get("base_urls")
    if isinstance(base_urls, Sequence) and not isinstance(base_urls, str):
        normalized = [
            base_url.strip()
            for base_url in base_urls
            if isinstance(base_url, str) and base_url.strip()
        ]
        if normalized:
            payload["attachment_resolver"] = ChatKitAttachmentResolverProxy(normalized)
            return

    base_url = resolver_payload.get("base_url")
    if isinstance(base_url, str) and base_url.strip():
        payload["attachment_resolver"] = ChatKitAttachmentResolverProxy(
            base_url.strip()
        )


def _resolve_public_attachment_base_urls() -> list[str]:
    """Return candidate child-facing origins for sandbox attachment resolution."""
    candidates: list[str] = []

    def _add_candidate(raw: str | None) -> None:
        if not raw:
            return
        normalized = raw.strip().rstrip("/")
        if not normalized:
            return
        host = urlparse(normalized).hostname
        if host in {"localhost", "127.0.0.1", "::1"}:
            return
        if normalized not in candidates:
            candidates.append(normalized)

    _add_candidate(_origin_from_url(os.environ.get("ORCHEO_CREDENTIAL_BROKER_URL")))
    _add_candidate(os.environ.get("ORCHEO_CHATKIT_ATTACHMENT_BASE_URL"))
    _add_candidate(os.environ.get("ORCHEO_API_URL"))
    _add_candidate(os.environ.get("ORCHEO_API_BASE_URL"))
    _add_candidate("http://credential-relay:9091")

    if not candidates:  # pragma: no cover
        candidates.append("http://credential-relay:9091")
    return candidates


def _origin_from_url(raw: str | None) -> str | None:
    """Return the scheme/authority portion of ``raw`` when valid."""
    if not raw:
        return None
    normalized = raw.strip().rstrip("/")
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _normalize_base_urls(base_urls: Sequence[str]) -> list[str]:
    """Normalize a sequence of candidate URLs while preserving order."""
    normalized: list[str] = []
    for base_url in base_urls:
        candidate = base_url.strip().rstrip("/")
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _serialize_attachment_scope(scope: Any) -> dict[str, Any] | None:
    """Serialize an attachment scope object to a JSON-safe payload."""
    if scope is None:
        return None

    if isinstance(scope, Mapping):
        marker_payload = scope.get(_ATTACHMENT_SCOPE_MARKER)
        if isinstance(marker_payload, Mapping):
            payload = dict(marker_payload)
        else:
            payload = dict(scope)
    elif dataclasses.is_dataclass(scope) and not isinstance(scope, type):
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
    "ChatKitAttachmentUploaderProxy",
    "hydrate_attachment_runtime_config",
    "serialize_attachment_runtime_config",
]
