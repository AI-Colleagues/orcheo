"""Tests for attachment runtime transport helpers."""

from __future__ import annotations

from dataclasses import dataclass

from orcheo.runtime.attachments import (
    AttachmentScopeRecord,
    ChatKitAttachmentResolverProxy,
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
    monkeypatch.setenv("ORCHEO_API_URL", "https://api.example.com")
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
            "base_url": "https://api.example.com",
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
    assert "attachment_uploader" not in sanitized["configurable"]


def test_hydrate_attachment_runtime_config_restores_proxy_objects(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ORCHEO_API_URL", "https://api.example.com")
    serialized = {
        "configurable": {
            "attachment_resolver": {
                "__orcheo_attachment_resolver__": {
                    "base_url": "https://api.example.com",
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
