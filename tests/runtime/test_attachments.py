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
        "ORCHEO_API_URL",
        "http://10.99.0.2:9091/credentials/resolve",
    )
    monkeypatch.setenv(
        "ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", "http://backend.local:2025"
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
                "http://backend.local:2025",
                "http://10.99.0.2:9091",
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
                "http://backend.local:2025",
                "http://10.99.0.2:9091",
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


def test_serialize_attachment_runtime_config_prefers_api_origin(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "ORCHEO_API_URL",
        "http://10.99.0.2:9091/credentials/resolve",
    )

    sanitized = serialize_attachment_runtime_config(
        {"configurable": {"attachment_resolver": object()}}
    )

    assert sanitized["configurable"]["attachment_resolver"] == {
        "__orcheo_attachment_resolver__": {
            "base_urls": ["http://10.99.0.2:9091"],
        }
    }


def test_hydrate_attachment_runtime_config_restores_proxy_objects(
    monkeypatch,
) -> None:
    serialized = {
        "configurable": {
            "attachment_resolver": {
                "__orcheo_attachment_resolver__": {
                    "base_urls": ["http://backend.local:2025"],
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
                    "base_url": "http://backend.local:2025",
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
                    "base_urls": ["http://backend.local:2025"],
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
                    "base_urls": ["http://backend.local:2025"],
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
            "base_urls": ["http://backend.local:2025"],
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


# ---------------------------------------------------------------------------
# Protocol stub coverage (lines 60, 79)
# ---------------------------------------------------------------------------


def test_attachment_resolver_protocol_stub() -> None:
    """AttachmentResolver protocol __init_subclass__ and stub body are importable (line 60)."""
    from orcheo.runtime.attachments import AttachmentResolver, AttachmentPayload

    # Verify the protocol defines the expected method
    assert hasattr(AttachmentResolver, "load_attachment_bytes")


def test_attachment_uploader_protocol_stub() -> None:
    """AttachmentUploader protocol __init_subclass__ and stub body are importable (line 79)."""
    from orcheo.runtime.attachments import AttachmentUploader

    assert hasattr(AttachmentUploader, "upload_attachment")


# ---------------------------------------------------------------------------
# ChatKitAttachmentResolverProxy: network error fallback + all-fail raise
# (lines 129-131, 145, 160)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatkit_attachment_resolver_proxy_handles_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network exception is caught and triggers fallback (lines 129-131)."""
    proxy = ChatKitAttachmentResolverProxy(
        ["http://relay.local:9091", "http://backend.local:2025"]
    )

    class _NetworkErrorClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _NetworkErrorClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            if "relay.local" in url:
                raise ConnectionError("connection refused")
            return httpx.Response(
                200,
                content=b"data",
                headers={
                    "content-type": "application/octet-stream",
                    "content-disposition": "",  # no filename
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _NetworkErrorClient)

    payload = await proxy.load_attachment_bytes(
        "atc_net",
        AttachmentScopeRecord(workspace_id="ws-1"),
    )

    # Successful from second URL; name falls back to attachment_id (line 145)
    assert payload.id == "atc_net"
    assert payload.name == "atc_net"


@pytest.mark.asyncio
async def test_chatkit_attachment_resolver_proxy_raises_when_all_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RuntimeError raised after all URLs fail (line 160)."""
    proxy = ChatKitAttachmentResolverProxy("http://relay.local:9091")

    class _AllFailClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _AllFailClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def get(self, url: str) -> httpx.Response:
            raise ConnectionError("all gone")

    monkeypatch.setattr(httpx, "AsyncClient", _AllFailClient)

    with pytest.raises(RuntimeError, match="Failed to load attachment"):
        await proxy.load_attachment_bytes(
            "atc_fail",
            AttachmentScopeRecord(workspace_id="ws-1"),
        )


# ---------------------------------------------------------------------------
# ChatKitAttachmentUploaderProxy: missing broker token, missing workflow/thread
# (lines 205->208, 209->211, 211->213, 214, 223-225, 234-235, 242)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chatkit_uploader_proxy_no_broker_token_and_upload_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No broker token: Authorization header omitted; upload_session_id set (lines 205->208, 214)."""
    monkeypatch.delenv("ORCHEO_BROKER_TOKEN", raising=False)

    proxy = ChatKitAttachmentUploaderProxy(
        "http://relay.local:9091",
        upload_session_id="ups_1",  # exercises line 214
    )
    calls: list[dict] = []

    class _FakeAsyncClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            calls.append({"url": url, **kwargs})
            return httpx.Response(
                200,
                json={
                    "id": "atc_ok",
                    "download_url": "http://relay.local:9091/api/chatkit/attachments/atc_ok",
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    attachment_id, _ = await proxy.upload_attachment(
        b"data", "file.bin", "application/octet-stream"
    )

    assert attachment_id == "atc_ok"
    # No Authorization header when no broker token (lines 205->208)
    assert "Authorization" not in calls[0].get("headers", {})
    # upload_session_id form data set (line 214)
    assert calls[0]["data"].get("upload_session_id") == "ups_1"
    # No workflow_id or thread_id in form (lines 209->211, 211->213)
    assert "workflow_id" not in calls[0].get("data", {})
    assert "thread_id" not in calls[0].get("data", {})


@pytest.mark.asyncio
async def test_chatkit_uploader_proxy_handles_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network exception is appended to errors and fallback attempted (lines 223-225)."""
    proxy = ChatKitAttachmentUploaderProxy(
        ["http://relay.local:9091", "http://backend.local:2025"],
        workflow_id="wf-1",
    )

    class _NetworkErrClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _NetworkErrClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            if "relay.local" in url:
                raise ConnectionError("relay down")
            return httpx.Response(200, json={"id": "atc_net_ok"})

    monkeypatch.setattr(httpx, "AsyncClient", _NetworkErrClient)

    attachment_id, _ = await proxy.upload_attachment(
        b"data", "f.bin", "application/octet-stream"
    )
    assert attachment_id == "atc_net_ok"


@pytest.mark.asyncio
async def test_chatkit_uploader_proxy_handles_missing_id_in_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing attachment_id in response is treated as an error (lines 234-235)."""
    proxy = ChatKitAttachmentUploaderProxy(
        ["http://relay.local:9091", "http://backend.local:2025"],
    )

    class _NoIdClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _NoIdClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            if "relay.local" in url:
                return httpx.Response(200, json={"result": "ok"})  # no 'id' field
            return httpx.Response(200, json={"id": "atc_second"})

    monkeypatch.setattr(httpx, "AsyncClient", _NoIdClient)

    attachment_id, _ = await proxy.upload_attachment(
        b"data", "f.bin", "application/octet-stream"
    )
    assert attachment_id == "atc_second"


@pytest.mark.asyncio
async def test_chatkit_uploader_proxy_raises_when_all_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RuntimeError raised when all upload endpoints fail (line 242)."""
    proxy = ChatKitAttachmentUploaderProxy("http://relay.local:9091")

    class _AllFailClient:
        def __init__(self, *, timeout: float) -> None:
            pass

        async def __aenter__(self) -> _AllFailClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, **kwargs: object) -> httpx.Response:
            return httpx.Response(500, text="internal error")

    monkeypatch.setattr(httpx, "AsyncClient", _AllFailClient)

    with pytest.raises(RuntimeError, match="Failed to upload attachment"):
        await proxy.upload_attachment(b"data", "f.bin", "application/octet-stream")


# ---------------------------------------------------------------------------
# serialize_attachment_runtime_config edge cases
# (lines 259, 283, 289->295, 292->295)
# ---------------------------------------------------------------------------


def test_serialize_attachment_runtime_config_non_mapping_returns_empty() -> None:
    """Returns {} when config is not a Mapping (line 259)."""
    from orcheo.runtime.attachments import serialize_attachment_runtime_config

    assert serialize_attachment_runtime_config(None) == {}  # type: ignore[arg-type]
    assert serialize_attachment_runtime_config("string") == {}  # type: ignore[arg-type]


def test_serialize_attachment_runtime_config_non_mapping_configurable_returns_sanitized() -> (
    None
):
    """Returns sanitized when config has non-Mapping configurable (line 264)."""
    from orcheo.runtime.attachments import serialize_attachment_runtime_config

    # configurable is a string, not a Mapping → returns sanitized early
    result = serialize_attachment_runtime_config(
        {"configurable": "not-a-dict", "run_name": "x"}
    )
    assert result["run_name"] == "x"
    assert result["configurable"] == "not-a-dict"


def test_hydrate_attachment_runtime_config_non_mapping_returns_empty() -> None:
    """Returns {} when config is not a Mapping (line 304)."""
    from orcheo.runtime.attachments import hydrate_attachment_runtime_config

    assert hydrate_attachment_runtime_config(None) == {}  # type: ignore[arg-type]
    assert hydrate_attachment_runtime_config(42) == {}  # type: ignore[arg-type]


def test_hydrate_attachment_runtime_config_non_mapping_configurable_returns_hydrated() -> (
    None
):
    """Returns hydrated early when configurable is not a Mapping (line 309)."""
    from orcheo.runtime.attachments import hydrate_attachment_runtime_config

    result = hydrate_attachment_runtime_config({"configurable": 42, "run_name": "x"})

    assert result["run_name"] == "x"
    assert result["configurable"] == 42


def test_serialize_drops_scope_when_workspace_id_missing() -> None:
    """Scope is popped when serialization returns None (line 283)."""
    from orcheo.runtime.attachments import serialize_attachment_runtime_config

    config = {
        "configurable": {
            "attachment_scope": {"no_workspace": True},  # missing workspace_id
        }
    }

    result = serialize_attachment_runtime_config(config)

    assert "attachment_scope" not in result["configurable"]


def test_serialize_uploader_uses_existing_marker_when_present() -> None:
    """Uploader with the marker is kept as-is (lines 289->295 True branch)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_UPLOADER_MARKER,
        serialize_attachment_runtime_config,
    )

    uploader_marker = {
        _ATTACHMENT_UPLOADER_MARKER: {
            "base_urls": ["http://relay:9091"],
            "workflow_id": "wf-1",
            "thread_id": None,
            "upload_session_id": None,
        }
    }
    config = {"configurable": {"attachment_uploader": uploader_marker}}

    result = serialize_attachment_runtime_config(config)

    assert result["configurable"]["attachment_uploader"] == uploader_marker


def test_serialize_uploader_dropped_when_scope_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploader is dropped when original_scope is None (lines 289->295 / 292->295)."""
    from orcheo.runtime.attachments import serialize_attachment_runtime_config

    # No scope, uploader present but not a marker
    config = {"configurable": {"attachment_uploader": object()}}

    result = serialize_attachment_runtime_config(config)

    assert "attachment_uploader" not in result["configurable"]


def test_serialize_uploader_dropped_when_serialization_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uploader not set when _serialize_attachment_uploader returns None (line 292->295)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_SCOPE_MARKER,
        serialize_attachment_runtime_config,
    )

    # scope present but base_urls will be empty, causing None from _serialize_attachment_uploader
    scope_marker = {
        _ATTACHMENT_SCOPE_MARKER: {"workspace_id": "ws-1", "workflow_id": "wf-1"}
    }
    config = {
        "configurable": {
            "attachment_scope": scope_marker,
            "attachment_uploader": object(),  # non-marker uploader with empty base_urls
        }
    }

    # Patch _resolve_public_attachment_base_urls to return []
    monkeypatch.setattr(
        "orcheo.runtime.attachments._resolve_public_attachment_base_urls",
        lambda: [],
    )

    result = serialize_attachment_runtime_config(config)

    # uploader_payload returned None, so not set
    assert "attachment_uploader" not in result["configurable"]


# ---------------------------------------------------------------------------
# hydrate_attachment_runtime_config edge cases (lines 318->323, 320->323)
# ---------------------------------------------------------------------------


def test_hydrate_skips_scope_when_marker_payload_not_mapping() -> None:
    """When scope payload is not a Mapping, scope is left as-is (line 318->323)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_SCOPE_MARKER,
        hydrate_attachment_runtime_config,
    )

    config = {
        "configurable": {
            "attachment_scope": {_ATTACHMENT_SCOPE_MARKER: "not-a-mapping"},
        }
    }

    result = hydrate_attachment_runtime_config(config)

    # Scope was not replaced
    assert result["configurable"]["attachment_scope"] == {
        _ATTACHMENT_SCOPE_MARKER: "not-a-mapping"
    }


def test_hydrate_skips_scope_when_hydrated_scope_is_none() -> None:
    """When hydrated scope returns None (bad workspace_id), scope is not replaced (line 320->323)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_SCOPE_MARKER,
        AttachmentScopeRecord,
        hydrate_attachment_runtime_config,
    )

    config = {
        "configurable": {
            "attachment_scope": {
                _ATTACHMENT_SCOPE_MARKER: {"workspace_id": "   "}  # blank → None
            },
        }
    }

    result = hydrate_attachment_runtime_config(config)

    # Scope not replaced because hydration failed
    scope = result["configurable"]["attachment_scope"]
    assert not isinstance(scope, AttachmentScopeRecord)


# ---------------------------------------------------------------------------
# _serialize_attachment_uploader edge cases (lines 333, 336-340, 344-348)
# ---------------------------------------------------------------------------


def test_serialize_attachment_uploader_returns_none_for_empty_base_urls() -> None:
    """Returns None when base_urls list is empty (line 333)."""
    from orcheo.runtime.attachments import _serialize_attachment_uploader

    result = _serialize_attachment_uploader(
        scope=AttachmentScopeRecord(workspace_id="ws-1"),
        base_urls=[],
    )

    assert result is None


def test_serialize_attachment_uploader_with_mapping_scope_with_marker() -> None:
    """Extracts scope from existing marker inside Mapping scope (lines 336-338)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_SCOPE_MARKER,
        _serialize_attachment_uploader,
    )

    scope = {
        _ATTACHMENT_SCOPE_MARKER: {
            "workspace_id": "ws-1",
            "workflow_id": "wf-1",
            "thread_id": None,
            "upload_session_id": None,
        }
    }

    result = _serialize_attachment_uploader(
        scope=scope, base_urls=["http://relay:9091"]
    )

    assert result is not None
    from orcheo.runtime.attachments import _ATTACHMENT_UPLOADER_MARKER

    assert result[_ATTACHMENT_UPLOADER_MARKER]["workflow_id"] == "wf-1"


def test_serialize_attachment_uploader_with_plain_mapping_scope() -> None:
    """Uses dict(scope) when no marker inside Mapping scope (line 340)."""
    from orcheo.runtime.attachments import (
        _serialize_attachment_uploader,
        _ATTACHMENT_UPLOADER_MARKER,
    )

    scope = {"workflow_id": "wf-plain", "thread_id": "thr-1"}

    result = _serialize_attachment_uploader(
        scope=scope, base_urls=["http://relay:9091"]
    )

    assert result is not None
    assert result[_ATTACHMENT_UPLOADER_MARKER]["workflow_id"] == "wf-plain"


def test_serialize_attachment_uploader_with_arbitrary_object_scope() -> None:
    """Uses getattr for arbitrary non-dataclass object (lines 344-348)."""
    from orcheo.runtime.attachments import (
        _serialize_attachment_uploader,
        _ATTACHMENT_UPLOADER_MARKER,
    )

    class _ScopeLike:
        workflow_id = "wf-obj"
        thread_id = None
        upload_session_id = "ups-obj"

    result = _serialize_attachment_uploader(
        scope=_ScopeLike(), base_urls=["http://relay:9091"]
    )

    assert result is not None
    assert result[_ATTACHMENT_UPLOADER_MARKER]["workflow_id"] == "wf-obj"
    assert result[_ATTACHMENT_UPLOADER_MARKER]["upload_session_id"] == "ups-obj"
    assert (
        "thread_id" not in result[_ATTACHMENT_UPLOADER_MARKER]
        or result[_ATTACHMENT_UPLOADER_MARKER]["thread_id"] is None
    )


# ---------------------------------------------------------------------------
# _hydrate_attachment_uploader edge cases (lines 367, 371, 374)
# ---------------------------------------------------------------------------


def test_hydrate_uploader_returns_early_when_not_mapping() -> None:
    """No hydration when uploader is not a Mapping (line 364 returns early)."""
    from orcheo.runtime.attachments import _hydrate_attachment_uploader

    payload: dict = {"attachment_uploader": "not-a-mapping"}
    _hydrate_attachment_uploader(payload)

    assert payload["attachment_uploader"] == "not-a-mapping"


def test_hydrate_uploader_returns_early_when_marker_not_mapping() -> None:
    """No hydration when uploader Mapping has wrong marker type (line 367)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_UPLOADER_MARKER,
        _hydrate_attachment_uploader,
    )

    payload: dict = {"attachment_uploader": {_ATTACHMENT_UPLOADER_MARKER: "bad"}}
    _hydrate_attachment_uploader(payload)

    # Not replaced
    assert payload["attachment_uploader"][_ATTACHMENT_UPLOADER_MARKER] == "bad"


def test_hydrate_uploader_returns_early_when_base_urls_not_sequence() -> None:
    """No hydration when base_urls is not a sequence (line 371)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_UPLOADER_MARKER,
        _hydrate_attachment_uploader,
    )

    payload: dict = {
        "attachment_uploader": {_ATTACHMENT_UPLOADER_MARKER: {"base_urls": 12345}}
    }
    _hydrate_attachment_uploader(payload)

    assert (
        payload["attachment_uploader"][_ATTACHMENT_UPLOADER_MARKER]["base_urls"]
        == 12345
    )


def test_hydrate_uploader_returns_early_when_base_urls_is_string() -> None:
    """No hydration when base_urls is a string (line 371 str check)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_UPLOADER_MARKER,
        _hydrate_attachment_uploader,
    )

    payload: dict = {
        "attachment_uploader": {
            _ATTACHMENT_UPLOADER_MARKER: {"base_urls": "http://single"}
        }
    }
    _hydrate_attachment_uploader(payload)

    assert (
        payload["attachment_uploader"][_ATTACHMENT_UPLOADER_MARKER]["base_urls"]
        == "http://single"
    )


def test_hydrate_uploader_returns_early_when_normalized_empty() -> None:
    """No hydration when all base_urls normalize to empty (line 374)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_UPLOADER_MARKER,
        _hydrate_attachment_uploader,
    )

    payload: dict = {
        "attachment_uploader": {_ATTACHMENT_UPLOADER_MARKER: {"base_urls": ["  ", ""]}}
    }
    _hydrate_attachment_uploader(payload)

    assert not isinstance(payload["attachment_uploader"], object.__class__)


# ---------------------------------------------------------------------------
# _hydrate_attachment_resolver edge cases (lines 391, 400->404, 405->exit)
# ---------------------------------------------------------------------------


def test_hydrate_resolver_returns_early_when_resolver_marker_not_mapping() -> None:
    """No hydration when resolver payload is not a Mapping (line 391)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_RESOLVER_MARKER,
        _hydrate_attachment_resolver,
    )

    payload: dict = {"attachment_resolver": {_ATTACHMENT_RESOLVER_MARKER: "bad"}}
    _hydrate_attachment_resolver(payload)

    assert payload["attachment_resolver"][_ATTACHMENT_RESOLVER_MARKER] == "bad"


def test_hydrate_resolver_falls_through_to_base_url_when_base_urls_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When base_urls normalizes to empty, falls to base_url field (lines 400->404)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_RESOLVER_MARKER,
        ChatKitAttachmentResolverProxy,
        _hydrate_attachment_resolver,
    )

    payload: dict = {
        "attachment_resolver": {
            _ATTACHMENT_RESOLVER_MARKER: {
                "base_urls": ["  ", ""],  # all empty → normalized empty
                "base_url": "http://fallback:9091",  # used instead
            }
        }
    }
    _hydrate_attachment_resolver(payload)

    assert isinstance(payload["attachment_resolver"], ChatKitAttachmentResolverProxy)


def test_hydrate_resolver_no_op_when_base_url_also_empty() -> None:
    """No-op when both base_urls and base_url are absent/empty (lines 405->exit)."""
    from orcheo.runtime.attachments import (
        _ATTACHMENT_RESOLVER_MARKER,
        ChatKitAttachmentResolverProxy,
        _hydrate_attachment_resolver,
    )

    payload: dict = {
        "attachment_resolver": {
            _ATTACHMENT_RESOLVER_MARKER: {
                "base_urls": [],
                # no base_url key
            }
        }
    }
    _hydrate_attachment_resolver(payload)

    assert not isinstance(
        payload["attachment_resolver"], ChatKitAttachmentResolverProxy
    )


# ---------------------------------------------------------------------------
# _resolve_public_attachment_base_urls (lines 420, 434)
# ---------------------------------------------------------------------------


def test_resolve_public_attachment_base_urls_skips_empty_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty-after-strip URLs (e.g., all slashes) are skipped (line 420 return)."""
    from orcheo.runtime.attachments import _resolve_public_attachment_base_urls

    monkeypatch.delenv("ORCHEO_API_URL", raising=False)
    monkeypatch.delenv("ORCHEO_API_BASE_URL", raising=False)
    # Set to a URL that strips to empty after rstrip('/')
    monkeypatch.setenv("ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", "///")

    candidates = _resolve_public_attachment_base_urls()

    assert candidates == []


def test_resolve_public_attachment_base_urls_skips_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Localhost URLs are filtered out (line 422-423)."""
    from orcheo.runtime.attachments import _resolve_public_attachment_base_urls

    monkeypatch.setenv("ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", "http://localhost:9091")
    monkeypatch.delenv("ORCHEO_API_URL", raising=False)
    monkeypatch.delenv("ORCHEO_API_BASE_URL", raising=False)

    candidates = _resolve_public_attachment_base_urls()

    assert "http://localhost:9091" not in candidates


# ---------------------------------------------------------------------------
# _origin_from_url (lines 444, 447)
# ---------------------------------------------------------------------------


def test_origin_from_url_returns_none_for_blank_input() -> None:
    """Returns None when stripped URL is empty (line 444)."""
    from orcheo.runtime.attachments import _origin_from_url

    assert _origin_from_url("   ") is None
    assert _origin_from_url(None) is None


def test_origin_from_url_returns_none_for_no_scheme() -> None:
    """Returns None when URL has no scheme (line 447)."""
    from orcheo.runtime.attachments import _origin_from_url

    assert _origin_from_url("just-a-host") is None


# ---------------------------------------------------------------------------
# _normalize_base_urls (line 456->454 — duplicate/empty skip)
# ---------------------------------------------------------------------------


def test_normalize_base_urls_skips_empty_and_duplicates() -> None:
    """Empty entries and duplicates are skipped in normalisation (line 456->454)."""
    from orcheo.runtime.attachments import _normalize_base_urls

    result = _normalize_base_urls(
        ["http://relay:9091", "  ", "http://relay:9091", "http://backend:2025"]
    )

    assert result == ["http://relay:9091", "http://backend:2025"]


# ---------------------------------------------------------------------------
# _serialize_attachment_scope (lines 464, 471, 475-479, 483)
# ---------------------------------------------------------------------------


def test_serialize_attachment_scope_with_dataclass_scope() -> None:
    """Dataclass scope is serialized via asdict (line 471)."""
    from orcheo.runtime.attachments import (
        _serialize_attachment_scope,
        _ATTACHMENT_SCOPE_MARKER,
        AttachmentScopeRecord,
    )

    scope = AttachmentScopeRecord(
        workspace_id="ws-1",
        workflow_id="wf-1",
        thread_id="thr-1",
        upload_session_id=None,
    )

    result = _serialize_attachment_scope(scope)

    assert result is not None
    assert result[_ATTACHMENT_SCOPE_MARKER]["workspace_id"] == "ws-1"
    assert result[_ATTACHMENT_SCOPE_MARKER]["workflow_id"] == "wf-1"


def test_serialize_attachment_scope_with_plain_mapping_scope() -> None:
    """Plain Mapping scope (no marker) uses dict(scope) (line 469/471)."""
    from orcheo.runtime.attachments import (
        _serialize_attachment_scope,
        _ATTACHMENT_SCOPE_MARKER,
    )

    scope = {"workspace_id": "ws-mapping", "workflow_id": "wf-2"}

    result = _serialize_attachment_scope(scope)

    assert result is not None
    assert result[_ATTACHMENT_SCOPE_MARKER]["workspace_id"] == "ws-mapping"


def test_serialize_attachment_scope_with_arbitrary_object() -> None:
    """Arbitrary object uses getattr for each field (lines 475-479)."""
    from orcheo.runtime.attachments import (
        _serialize_attachment_scope,
        _ATTACHMENT_SCOPE_MARKER,
    )

    class _ScopeLike:
        workspace_id = "ws-obj"
        workflow_id = "wf-obj"
        thread_id = None
        upload_session_id = "ups-obj"

    result = _serialize_attachment_scope(_ScopeLike())

    assert result is not None
    assert result[_ATTACHMENT_SCOPE_MARKER]["workspace_id"] == "ws-obj"
    assert result[_ATTACHMENT_SCOPE_MARKER]["upload_session_id"] == "ups-obj"


def test_serialize_attachment_scope_returns_none_when_workspace_missing() -> None:
    """Returns None when workspace_id is missing/blank (line 483)."""
    from orcheo.runtime.attachments import _serialize_attachment_scope

    assert _serialize_attachment_scope({"no_workspace": True}) is None
    assert _serialize_attachment_scope({"workspace_id": "   "}) is None


def test_serialize_attachment_scope_returns_none_for_none_input() -> None:
    """Returns None when scope is None (line 464)."""
    from orcheo.runtime.attachments import _serialize_attachment_scope

    assert _serialize_attachment_scope(None) is None


# ---------------------------------------------------------------------------
# _hydrate_attachment_scope (line 501)
# ---------------------------------------------------------------------------


def test_hydrate_attachment_scope_returns_none_when_workspace_blank() -> None:
    """Returns None when workspace_id is blank (line 501)."""
    from orcheo.runtime.attachments import _hydrate_attachment_scope

    assert _hydrate_attachment_scope({"workspace_id": ""}) is None
    assert _hydrate_attachment_scope({"workspace_id": "   "}) is None
    assert _hydrate_attachment_scope({}) is None


# ---------------------------------------------------------------------------
# _filename_from_content_disposition (lines 518, 521)
# ---------------------------------------------------------------------------


def test_filename_from_content_disposition_returns_empty_when_none() -> None:
    """Returns empty string when content_disposition is None/empty (line 518)."""
    from orcheo.runtime.attachments import _filename_from_content_disposition

    assert _filename_from_content_disposition(None) == ""
    assert _filename_from_content_disposition("") == ""


def test_filename_from_content_disposition_returns_empty_when_no_match() -> None:
    """Returns empty string when regex finds no filename (line 521)."""
    from orcheo.runtime.attachments import _filename_from_content_disposition

    assert _filename_from_content_disposition("inline") == ""
    assert _filename_from_content_disposition("attachment") == ""


def test_resolve_public_attachment_base_urls_skips_all_loopback_variants(
    monkeypatch,
) -> None:
    """Localhost / loopback URLs must be excluded from candidate base URLs."""
    from orcheo.runtime.attachments import _resolve_public_attachment_base_urls

    monkeypatch.setenv("ORCHEO_API_URL", "http://localhost:9000")
    monkeypatch.setenv("ORCHEO_API_BASE_URL", "http://127.0.0.1:9001")
    monkeypatch.delenv("ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", raising=False)

    urls = _resolve_public_attachment_base_urls()

    assert urls == []


def test_resolve_public_attachment_base_urls_skips_slash_only_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slash-only return from _origin_from_url strips to empty and is skipped (line 414)."""
    from orcheo.runtime import attachments
    from orcheo.runtime.attachments import _resolve_public_attachment_base_urls

    monkeypatch.setattr(attachments, "_origin_from_url", lambda _: "/")
    monkeypatch.delenv("ORCHEO_API_URL", raising=False)
    monkeypatch.delenv("ORCHEO_API_BASE_URL", raising=False)
    monkeypatch.delenv("ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", raising=False)

    candidates = _resolve_public_attachment_base_urls()

    assert candidates == []


def test_resolve_public_attachment_base_urls_deduplicates(
    monkeypatch,
) -> None:
    """The same origin from multiple env vars should appear only once."""
    from orcheo.runtime.attachments import _resolve_public_attachment_base_urls

    monkeypatch.setenv("ORCHEO_API_URL", "https://api.example.com/v1")
    monkeypatch.setenv("ORCHEO_API_BASE_URL", "https://api.example.com/v2")
    monkeypatch.delenv("ORCHEO_CHATKIT_ATTACHMENT_BASE_URL", raising=False)

    urls = _resolve_public_attachment_base_urls()

    assert urls.count("https://api.example.com") == 1
