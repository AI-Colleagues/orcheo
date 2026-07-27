"""PostgreSQL Hosted Apps bundle-object storage tests."""

from __future__ import annotations

from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import pytest

from orcheo.hosted_apps import PostgresBundleStore, migrate_filesystem_bundles


class _Result:
    def __init__(self, row=None, *, rowcount: int = 0) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def execute(self, sql: str, params=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("CREATE ") or normalized.startswith("SELECT 1 "):
            return _Result()
        if normalized.startswith("INSERT INTO hosted_app_bundle_objects"):
            key, content = params
            existing = self.objects.setdefault(key, bytes(content))
            return _Result({"content": existing})
        if normalized.startswith("SELECT content"):
            row = self.objects.get(params[0])
            return _Result(None if row is None else {"content": row})
        if normalized.startswith("DELETE FROM hosted_app_bundle_objects"):
            prefix = params[0]
            keys = [
                key
                for key in self.objects
                if key == prefix or key.startswith(f"{prefix}/")
            ]
            for key in keys:
                del self.objects[key]
            return _Result(rowcount=len(keys))
        raise AssertionError(normalized)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


class _Pool:
    def __init__(self) -> None:
        self.connection_value = _Connection()

    @contextmanager
    def connection(self):
        yield self.connection_value


def test_postgres_bundle_store_round_trip_and_immutability() -> None:
    pool = _Pool()
    store = PostgresBundleStore("", pool=pool)
    upload_id = uuid4()
    deployment_id = uuid4()

    staging_key = store.write_staged(upload_id, BytesIO(b"archive"))
    store.write_deployment_file(deployment_id, "assets/main.js", BytesIO(b"app"))
    store.write_manifest(deployment_id, BytesIO(b'{"files":{}}'))

    assert store.open_staged(staging_key).read() == b"archive"
    assert store.open_deployment_file(deployment_id, "assets/main.js").read() == b"app"
    with pytest.raises(ValueError, match="immutable"):
        store.write_deployment_file(
            deployment_id, "assets/main.js", BytesIO(b"different")
        )
    store.delete_prefix(f"deployments/{deployment_id}")
    with pytest.raises(FileNotFoundError):
        store.open_deployment_file(deployment_id, "assets/main.js")


def test_migrate_filesystem_bundles_to_postgres(tmp_path: Path) -> None:
    upload_id = uuid4()
    deployment_id = uuid4()
    staging = tmp_path / "staging" / str(upload_id)
    deployment = tmp_path / "deployments" / str(deployment_id)
    staging.mkdir(parents=True)
    deployment.mkdir(parents=True)
    (staging / "archive.zip").write_bytes(b"archive")
    (deployment / "index.html").write_bytes(b"<h1>App</h1>")
    (deployment / "__manifest__.json").write_bytes(b'{"files":{}}')
    store = PostgresBundleStore("", pool=_Pool())

    assert migrate_filesystem_bundles(tmp_path, store) == 3
    assert store.open_staged(f"staging/{upload_id}/archive.zip").read() == b"archive"
    assert store.open_deployment_file(deployment_id, "index.html").read() == (
        b"<h1>App</h1>"
    )
    assert not staging.exists()
    assert migrate_filesystem_bundles(tmp_path, store) == 2
