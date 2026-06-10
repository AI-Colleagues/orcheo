from __future__ import annotations
from typing import Any
from orcheo_sdk.services.workflows import publish as publish_module


class DummyClient:
    """Minimal client stub for publish service tests."""

    def __init__(
        self,
        *,
        base_url: str,
        response: Any,
        public_base_url: str | None = None,
    ) -> None:
        self.base_url = base_url
        self.public_base_url = public_base_url
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def post(self, path: str, *, json_body: dict[str, Any]) -> Any:
        self.calls.append({"path": path, "json_body": json_body})
        return self.response


def test_build_share_url_strips_api_segment() -> None:
    url = publish_module._build_share_url("http://host/api", "wf-123")
    assert url == "http://host/chat/wf-123"
    url_with_slash = publish_module._build_share_url("http://host/api/", "wf-456")
    assert url_with_slash == "http://host/chat/wf-456"


def test_build_share_url_prefers_public_base_url() -> None:
    url = publish_module._build_share_url(
        "http://host/api",
        "wf-789",
        public_base_url="https://studio.test",
    )
    assert url == "https://studio.test/chat/wf-789"


def test_enrich_workflow_preserves_share_url_when_public() -> None:
    workflow = {"id": "wf-1", "is_public": True}
    enriched = publish_module._enrich_workflow("http://api.example.com/api", workflow)
    assert enriched["share_url"] == "http://api.example.com/chat/wf-1"


def test_enrich_workflow_hides_share_url_when_private() -> None:
    workflow = {"id": "wf-1", "is_public": False}
    enriched = publish_module._enrich_workflow("http://api.example.com/api", workflow)
    assert enriched["share_url"] is None


def test_publish_workflow_data_returns_enriched_payload() -> None:
    payload = {
        "workflow": {"id": "wf-1", "is_public": True, "require_login": True},
        "message": "Published",
    }
    client = DummyClient(base_url="http://api.example.com/api", response=payload)

    result = publish_module.publish_workflow_data(
        client,
        "wf-1",
        require_login=True,
        actor="cli",
    )

    assert result["share_url"] == "http://api.example.com/chat/wf-1"
    assert result["message"] == "Published"
    assert client.calls == [
        {
            "path": "/api/workflows/wf-1/publish",
            "json_body": {"require_login": True, "actor": "cli"},
        }
    ]


def test_publish_workflow_data_prefers_explicit_public_base() -> None:
    payload = {
        "workflow": {"id": "wf-2", "is_public": True, "require_login": False},
        "message": "Published",
    }
    client = DummyClient(base_url="http://api.example.com/api", response=payload)

    result = publish_module.publish_workflow_data(
        client,
        "wf-2",
        require_login=False,
        actor="cli",
        public_base_url="https://studio.example",
    )

    assert result["share_url"] == "https://studio.example/chat/wf-2"


def test_publish_workflow_data_uses_server_share_url_when_available() -> None:
    payload = {
        "workflow": {
            "id": "wf-10",
            "is_public": True,
            "require_login": False,
            "share_url": "https://studio.example/chat/wf-10",
        },
        "share_url": "https://studio.example/chat/wf-10",
        "message": "Published",
    }
    client = DummyClient(base_url="http://api.example.com/api", response=payload)

    result = publish_module.publish_workflow_data(
        client,
        "wf-10",
        require_login=False,
        actor="cli",
    )

    assert result["share_url"] == "https://studio.example/chat/wf-10"
    assert result["workflow"]["share_url"] == "https://studio.example/chat/wf-10"


def test_unpublish_workflow_returns_enriched_workflow() -> None:
    workflow = {"id": "wf-3", "is_public": False}
    client = DummyClient(base_url="http://api.test", response=workflow)

    result = publish_module.unpublish_workflow_data(client, "wf-3", actor="cli")

    assert result["workflow"]["share_url"] is None
    assert result["share_url"] is None
    assert client.calls == [
        {
            "path": "/api/workflows/wf-3/publish/revoke",
            "json_body": {"actor": "cli"},
        }
    ]


def test_enrich_workflow_publish_metadata_derives_share_url() -> None:
    workflow = {"id": "wf-4", "is_public": True}
    enriched = publish_module.enrich_workflow_publish_metadata(
        DummyClient(base_url="http://api.test/api", response={}),
        workflow,
    )

    assert enriched["share_url"] == "http://api.test/chat/wf-4"


def test_enrich_workflow_publish_metadata_uses_public_override() -> None:
    workflow = {"id": "wf-override", "is_public": True}
    enriched = publish_module.enrich_workflow_publish_metadata(
        DummyClient(
            base_url="http://api.test/api",
            response={},
            public_base_url="https://studio.example",
        ),
        workflow,
    )

    assert enriched["share_url"] == "https://studio.example/chat/wf-override"
