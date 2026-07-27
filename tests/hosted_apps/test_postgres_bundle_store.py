"""PostgreSQL Hosted Apps bundle-object storage tests."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
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
        self.created_at: dict[str, datetime] = {}
        self.execute_log: list[str] = []
        self.rollback_called = False

    def execute(self, sql: str, params=()):
        normalized = " ".join(sql.split())
        self.execute_log.append(normalized)
        if normalized.startswith("CREATE ") or normalized.startswith("SELECT 1 "):
            return _Result()
        if normalized.startswith("INSERT INTO hosted_app_bundle_objects"):
            key, content = params
            existing = self.objects.setdefault(key, bytes(content))
            self.created_at.setdefault(key, datetime.now())
            return _Result({"content": existing})
        if normalized.startswith("SELECT content"):
            row = self.objects.get(params[0])
            return _Result(None if row is None else {"content": row})
        if normalized.startswith("DELETE FROM hosted_app_bundle_objects"):
            if "created_at" in normalized:
                cutoff = params[0]
                keys = [
                    key
                    for key, created in self.created_at.items()
                    if key.startswith("staging/") and created < cutoff
                ]
            else:
                prefix = params[0]
                keys = [
                    key
                    for key in self.objects
                    if key == prefix or key.startswith(f"{prefix}/")
                ]
            for key in keys:
                del self.objects[key]
                self.created_at.pop(key, None)
            return _Result(rowcount=len(keys))
        raise AssertionError(normalized)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        self.rollback_called = True


class _Pool:
    def __init__(self) -> None:
        self.connection_value = _Connection()
        self.closed = False

    @contextmanager
    def connection(self):
        yield self.connection_value

    def close(self) -> None:
        self.closed = True


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


def test_postgres_bundle_store_requires_dsn_without_pool() -> None:
    with pytest.raises(ValueError, match="requires a database DSN"):
        PostgresBundleStore("   ", pool=None)


def test_postgres_bundle_store_skips_schema_creation_when_disabled() -> None:
    pool = _Pool()
    PostgresBundleStore("", pool=pool, ensure_schema=False)
    assert pool.connection_value.execute_log == []


def test_postgres_bundle_store_connect_rolls_back_on_error() -> None:
    pool = _Pool()
    store = PostgresBundleStore("", pool=pool)
    with pytest.raises(RuntimeError, match="boom"):
        with store._connect():
            raise RuntimeError("boom")
    assert pool.connection_value.rollback_called is True


def test_postgres_bundle_store_deletes_expired_staging_objects() -> None:
    pool = _Pool()
    store = PostgresBundleStore("", pool=pool)
    upload_id = uuid4()
    key = store.write_staged(upload_id, BytesIO(b"archive"))
    pool.connection_value.created_at[key] = datetime.now() - timedelta(days=2)

    deleted = store.delete_expired_staging(datetime.now() - timedelta(days=1))

    assert deleted == 1
    with pytest.raises(FileNotFoundError):
        store.open_staged(key)


def test_postgres_bundle_store_healthcheck_queries_table() -> None:
    pool = _Pool()
    store = PostgresBundleStore("", pool=pool)
    store.healthcheck()
    assert any(
        log.startswith("SELECT 1 FROM hosted_app_bundle_objects")
        for log in pool.connection_value.execute_log
    )


def test_postgres_bundle_store_close_respects_pool_ownership() -> None:
    pool = _Pool()
    store = PostgresBundleStore("", pool=pool)

    store.close()
    assert pool.closed is False

    store._owns_pool = True
    store.close()
    assert pool.closed is True


def test_row_content_raises_for_missing_row() -> None:
    with pytest.raises(FileNotFoundError):
        PostgresBundleStore._row_content(None)


def test_validate_owned_key_rejects_unknown_namespace() -> None:
    with pytest.raises(ValueError, match="owned namespace"):
        PostgresBundleStore._validate_owned_key("other/path")


def test_validate_owned_key_rejects_namespace_mismatch() -> None:
    with pytest.raises(ValueError, match="wrong namespace"):
        PostgresBundleStore._validate_owned_key(
            "deployments/x", expected_root="staging"
        )
