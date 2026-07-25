"""Provider-independent tests for the S3-compatible bundle store adapter."""

from __future__ import annotations

from io import BytesIO
from uuid import uuid4
from orcheo.hosted_apps import S3BundleStore


class _Paginator:
    """Simple deterministic object-list paginator for storage tests."""

    def paginate(self, **_kwargs: object) -> list[dict[str, object]]:
        """Return one page containing a single unreachable object."""
        return [{"Contents": [{"Key": "hosted-apps/staging/stale/archive.zip"}]}]


class _Client:
    """Capture the small S3 client surface used by the adapter."""

    def __init__(self) -> None:
        self.uploads: list[str] = []
        self.deleted: list[str] = []
        self.health_checked = False

    def upload_fileobj(self, _source: BytesIO, _bucket: str, key: str) -> None:
        """Record the server-generated upload key."""
        self.uploads.append(key)

    def get_object(self, **_kwargs: object) -> dict[str, BytesIO]:
        """Return a server-side stream without a browser URL."""
        return {"Body": BytesIO(b"object")}

    def get_paginator(self, _name: str) -> _Paginator:
        """Return the deterministic test paginator."""
        return _Paginator()

    def delete_objects(self, **kwargs: object) -> None:
        """Record the private key deletion request."""
        self.deleted.extend(item["Key"] for item in kwargs["Delete"]["Objects"])

    def head_bucket(self, **_kwargs: object) -> None:
        """Record private-bucket health validation."""
        self.health_checked = True


def test_s3_store_uses_private_server_selected_keys() -> None:
    """Staging and deployment object paths cannot be provided by a browser client."""
    client = _Client()
    store = S3BundleStore(client, bucket="private-app-bundles")
    upload_id = uuid4()
    deployment_id = uuid4()
    key = store.write_staged(upload_id, BytesIO(b"archive"))
    store.write_deployment_file(deployment_id, "assets/main.js", BytesIO(b"app"))
    store.write_manifest(deployment_id, BytesIO(b"{}"))
    assert key == f"hosted-apps/staging/{upload_id}/archive.zip"
    assert f"hosted-apps/deployments/{deployment_id}/assets/main.js" in client.uploads
    assert store.open_staged(key).read() == b"object"
    store.delete_prefix("hosted-apps/staging/stale")
    store.healthcheck()
    assert client.deleted == ["hosted-apps/staging/stale/archive.zip"]
    assert client.health_checked is True
