"""Tests for attachment runtime transport helpers."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
import pytest

from orcheo.runtime.attachments import (
    AttachmentScopeRecord,
    ChatKitAttachmentResolverProxy,
    ChatKitAttachmentUploaderProxy,
    hydrate_attachment_runtime_config,
    serialize_attachment_runtime_config,
)


@dataclass
class _ScopeLike:
    workspace_id: str
    workflow_id: str | None = None
    thread_id: str | None = None
    upload_session_id: str | None = None


def test_serialize_attachment_runtime_config_sanitizes_runtime_objects(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ORCHEO_CREDENTIAL_BROKER_URL",
        "http://10.99.0.2:9091/credentials/resolve",
    )
    monkeypatch.setenv(
        "ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", "http://credential-relay:9091"
    )
    config = {
        "configurable": {
            "existing": "value",
            "attachment_resolver": object(),
            "attachment_scope": _ScopeLike(
                workspace_id="ws-1",
                workflow_id="wf-1",
                thread_id="thr-1",
                upload_session_id="ups-1",
            ),
            "attachment_uploader": object(),
        },
        "run_name": "example",
    }

    sanitized = serialize_attachment_runtime_config(config)

    assert sanitized["configurable"]["existing"] == "value"
    assert sanitized["configurable"]["attachment_resolver"] == {
        "__orcheo_attachment_resolver__": {
            "base_urls": [
                "http://10.99.0.2:9091",
                "http://credential-relay:9091",
            ],
        }
    }
    assert sanitized["configurable"]["attachment_scope"] == {
        "__orcheo_attachment_scope__": {
            "workspace_id": "ws-1",
            "workflow_id": "wf-1",
            "thread_id": "thr-1",
            "upload_session_id": "ups-1",
        }
    }
    assert sanitized["configurable"]["attachment_uploader"] == {
        "__orcheo_attachment_uploader__": {
            "base_urls": [
                "http://10.99.0.2:9091",
                "http://credential-relay:9091",
            ],
            "workflow_id": "wf-1",
            "thread_id": "thr-1",
            "upload_session_id": "ups-1",
        }
    }


def test_serialize_attachment_runtime_config_preserves_scope_marker() -> None:
    config = {
        "configurable": {
            "attachment_scope": {
                "__orcheo_attachment_scope__": {
                    "workspace_id": "ws-1",
                    "workflow_id": "wf-1",
                    "thread_id": "thr-1",
                    "upload_session_id": "ups-1",
                }
            }
        }
    }

    sanitized = serialize_attachment_runtime_config(config)

    assert sanitized["configurable"]["attachment_scope"] == {
        "__orcheo_attachment_scope__": {
            "workspace_id": "ws-1",
            "workflow_id": "wf-1",
            "thread_id": "thr-1",
            "upload_session_id": "ups-1",
        }
    }


def test_serialize_attachment_runtime_config_prefers_broker_origin(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ORCHEO_CREDENTIAL_BROKER_URL",
        "http://10.99.0.2:9091/credentials/resolve",
    )

    sanitized = serialize_attachment_runtime_config(
        {"configurable": {"attachment_resolver": object()}}
    )

    assert sanitized["configurable"]["attachment_resolver"] == {
        "__orcheo_attachment_resolver__": {
            "base_urls": [
                "http://10.99.0.2:9091",
                "http://credential-relay:9091",
            ],
        }
    }


def test_hydrate_attachment_runtime_config_restores_proxy_objects(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", "http://credential-relay:9091"
    )
    serialized = {
        "configurable": {
            "attachment_resolver": {
                "__orcheo_attachment_resolver__": {
                    "base_urls": ["http://credential-relay:9091"],
                }
            },
            "attachment_scope": {
                "__orcheo_attachment_scope__": {
                    "workspace_id": "ws-1",
                    "workflow_id": "wf-1",
                    "thread_id": "thr-1",
                    "upload_session_id": "ups-1",
                }
            },
        }
    }

    hydrated = hydrate_attachment_runtime_config(serialized)

    assert isinstance(
        hydrated["configurable"]["attachment_resolver"],
        ChatKitAttachmentResolverProxy,
    )
    assert isinstance(
        hydrated["configurable"]["attachment_scope"],
        AttachmentScopeRecord,
    )
    assert hydrated["configurable"]["attachment_scope"].workspace_id == "ws-1"


def test_hydrate_attachment_runtime_config_supports_legacy_base_url_field() -> None:
    serialized = {
        "configurable": {
            "attachment_resolver": {
                "__orcheo_attachment_resolver__": {
                    "base_url": "http://credential-relay:9091",
                }
            }
        }
    }

    hydrated = hydrate_attachment_runtime_config(serialized)

    assert isinstance(
        hydrated["configurable"]["attachment_resolver"],
        ChatKitAttachmentResolverProxy,
    )


@pytest.mark.asyncio
async def test_chatkit_attachment_resolver_proxy_falls_back_between_urls(
    monkeypatch,
) -> None:
    proxy = ChatKitAttachmentResolverProxy(
        ["http://relay.local:9091", "http://backend.local:2025"]
    )
    calls: list[str] = []

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 30.0

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            calls.append(url)
            if url.startswith("http://relay.local"):
                return httpx.Response(503, text="unavailable")
            return httpx.Response(
                200,
                content=b"alpha,beta\n1,2\n",
                headers={
                    "content-type": "text/csv",
                    "content-disposition": 'attachment; filename="report.csv"',
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    payload = await proxy.load_attachment_bytes(
        "atc_123",
        AttachmentScopeRecord(workspace_id="ws-1"),
    )

    assert payload.id == "atc_123"
    assert payload.name == "report.csv"
    assert payload.content == b"alpha,beta\n1,2\n"
    assert calls == [
        "http://relay.local:9091/api/chatkit/attachments/atc_123",
        "http://backend.local:2025/api/chatkit/attachments/atc_123",
    ]


def test_hydrate_attachment_runtime_config_restores_uploader_proxy() -> None:
    serialized = {
        "configurable": {
            "attachment_uploader": {
                "__orcheo_attachment_uploader__": {
                    "base_urls": ["http://credential-relay:9091"],
                    "workflow_id": "wf-1",
                    "thread_id": "thr-1",
                    "upload_session_id": None,
                }
            }
        }
    }

    hydrated = hydrate_attachment_runtime_config(serialized)

    uploader = hydrated["configurable"]["attachment_uploader"]
    assert isinstance(uploader, ChatKitAttachmentUploaderProxy)
    assert uploader._workflow_id == "wf-1"
    assert uploader._thread_id == "thr-1"
    assert uploader._upload_session_id is None


def test_serialize_attachment_runtime_config_preserves_uploader_marker() -> None:
    config = {
        "configurable": {
            "attachment_uploader": {
                "__orcheo_attachment_uploader__": {
                    "base_urls": ["http://credential-relay:9091"],
                    "workflow_id": "wf-1",
                    "thread_id": None,
                    "upload_session_id": None,
                }
            }
        }
    }

    sanitized = serialize_attachment_runtime_config(config)

    assert sanitized["configurable"]["attachment_uploader"] == {
        "__orcheo_attachment_uploader__": {
            "base_urls": ["http://credential-relay:9091"],
            "workflow_id": "wf-1",
            "thread_id": None,
            "upload_session_id": None,
        }
    }


@pytest.mark.asyncio
async def test_chatkit_attachment_uploader_proxy_posts_to_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORCHEO_BROKER_TOKEN", "test-broker-token")

    proxy = ChatKitAttachmentUploaderProxy(
        ["http://relay.local:9091", "http://backend.local:2025"],
        workflow_id="wf-1",
        thread_id="thr-1",
        upload_session_id=None,
    )
    calls: list[dict] = []

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            assert timeout == 60.0

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            calls.append({"url": url, **kwargs})
            if "relay.local" in url:
                return httpx.Response(503, text="unavailable")
            return httpx.Response(
                200,
                json={
                    "id": "atc_newfile123",
                    "download_url": "http://backend.local:2025/api/chatkit/attachments/atc_newfile123",
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    attachment_id, download_url = await proxy.upload_attachment(
        b"col1,col2\n1,2\n",
        "results.csv",
        "text/csv",
    )

    assert attachment_id == "atc_newfile123"
    assert "atc_newfile123" in download_url
    assert len(calls) == 2
    assert calls[0]["url"] == "http://relay.local:9091/api/chatkit/attachments/upload"
    assert calls[1]["url"] == "http://backend.local:2025/api/chatkit/attachments/upload"
    assert calls[1]["headers"]["Authorization"] == "Bearer test-broker-token"
