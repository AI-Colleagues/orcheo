"""Focused edge-case coverage for Hosted Apps domain services."""

from __future__ import annotations

from collections import deque
from datetime import timedelta
from io import BytesIO
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4
import zipfile

import pytest

from orcheo.hosted_apps import (
    AppAuthError,
    AppAuthService,
    AppBinding,
    AppCollection,
    AppDataConflictError,
    AppDataService,
    AppManifest,
    AppManifestBinding,
    AppRuntimeError,
    AppRuntimeService,
    BundleValidationError,
    DeploymentService,
    FilesystemBundleStore,
    QuotaExceededError,
    QuotaLeaseManager,
    S3BundleStore,
    canonical_app_host,
    derive_client_ip,
    normalize_alias,
    normalize_logical_name,
    pkce_challenge,
    validate_input_schema,
)
from orcheo.hosted_apps.config import HostedAppsSettings, HostedAppsSettingsError
from orcheo.hosted_apps.deployments import UploadNotFoundError
from orcheo.hosted_apps.setup_validation import validate_hosted_apps_setup
from orcheo.hosted_apps.runtime import _validate_schema
from orcheo.hosted_apps.zip_validation import (
    BundleValidationLimits,
    _HtmlPolicyParser,
    _content_type,
    _hash_member,
    _normalize_member_path,
    _parse_app_manifest,
    _parse_html_policy,
    _unique_json_object,
    _verify_archive_size,
    validate_bundle,
)
from orcheo.models.base import _utcnow


def _collection(*, scope: str = "shared", max_records: int = 2) -> AppCollection:
    """Build a collection for service-level tests."""
    return AppCollection(
        workspace_id=uuid4(),
        app_id=uuid4(),
        name="records",
        scope=scope,
        read_access="authenticated" if scope == "user" else "anonymous",
        write_access="authenticated" if scope == "user" else "anonymous",
        max_document_bytes=100,
        max_records=max_records,
    )


def _archive(files: dict[str, bytes]) -> BytesIO:
    """Return an in-memory ZIP archive."""
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        for name, content in files.items():
            bundle.writestr(name, content)
    stream.seek(0)
    return stream


def test_auth_rejects_wrong_pkce_and_handles_unknown_revoke() -> None:
    """PKCE mismatches fail before a code is consumed and revoke is idempotent."""
    service = AppAuthService()
    verifier = "v" * 64
    callback = "https://app.example.test/__orcheo/auth/callback"
    code = service.issue_code(
        app_id=uuid4(),
        workspace_id=uuid4(),
        user_id="member",
        redirect_uri=callback,
        code_challenge=pkce_challenge(verifier),
    )
    with pytest.raises(AppAuthError, match="PKCE"):
        service.exchange(
            raw_code=code,
            verifier="x" * 64,
            app_host="app.example.test",
            redirect_uri=callback,
            runtime_generation=1,
            current_member=True,
        )
    service.revoke("missing")


@pytest.mark.parametrize(
    ("host", "domain"),
    [
        ("app.other.test", "apps.example.test"),
        ("one.two.apps.example.test", "apps.example.test"),
        ("app.apps.example.test:bad", "apps.example.test"),
    ],
)
def test_gateway_host_and_path_guards_reject_ambiguous_inputs(
    host: str, domain: str
) -> None:
    """Gateway host canonicalization accepts only one exact alias label."""
    from orcheo.hosted_apps.gateway import is_safe_app_path

    with pytest.raises(Exception):
        canonical_app_host(host, domain)
    assert canonical_app_host("app.apps.example.test.", domain) == (
        "app.apps.example.test",
        "app",
    )
    assert not is_safe_app_path("/assets//main.js")
    assert not is_safe_app_path("/../secret")
    assert not is_safe_app_path("/__orcheo/auth")
    assert not is_safe_app_path("/assets/%00")


def test_gateway_client_ip_trust_boundary() -> None:
    """Forwarded IPs are used only when the peer is an explicitly trusted proxy."""
    assert derive_client_ip("198.51.100.2", "203.0.113.4") == "198.51.100.2"
    assert (
        derive_client_ip(
            "10.0.0.2",
            "203.0.113.4, 198.51.100.2",
            trusted_proxy_cidrs=("10.0.0.0/8",),
            trusted_hops=1,
        )
        == "198.51.100.2"
    )
    with pytest.raises(ValueError, match="shorter"):
        derive_client_ip(
            "10.0.0.2",
            "203.0.113.4",
            trusted_proxy_cidrs=("10.0.0.0/8",),
            trusted_hops=2,
        )
    with pytest.raises(ValueError, match="invalid"):
        derive_client_ip("not-an-ip", None)


def test_quota_leases_cover_invalid_missing_limit_expiry_and_release() -> None:
    """Quota leases fail closed and reconcile abandoned reservations."""
    workspace_id = uuid4()
    manager = QuotaLeaseManager({(workspace_id, "upload_bytes"): 2})
    with pytest.raises(ValueError):
        manager.reserve(workspace_id, "unknown", 1)
    with pytest.raises(QuotaExceededError, match="unavailable"):
        manager.reserve(uuid4(), "upload_bytes", 1)
    lease = manager.reserve(workspace_id, "upload_bytes", 2, ttl_seconds=-1)
    assert manager.reconcile() == 1
    with pytest.raises(QuotaExceededError):
        manager.commit(lease)
    manager.release(uuid4())


def test_app_data_limits_scope_serialization_and_cursor_errors() -> None:
    """App data rejects invalid scope, JSON, depth, keys, and opaque cursors."""
    collection = _collection(max_records=1)
    service = AppDataService(max_depth=1, max_keys=10)
    with pytest.raises(ValueError, match="scope"):
        service.get(
            collection,
            workspace_id=uuid4(),
            app_id=collection.app_id,
            key="x",
            subject=None,
        )
    with pytest.raises(ValueError, match="JSON key"):
        AppDataService(max_keys=1).put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="x",
            value={"a": 1, "b": 2},
            subject=None,
        )
    with pytest.raises(ValueError, match="depth"):
        service.put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="x",
            value={"a": {"b": 1}},
            subject=None,
        )
    with pytest.raises(ValueError, match="serializable"):
        service.put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="x",
            value={"value": object()},
            subject=None,
        )
    with pytest.raises(ValueError, match="key"):
        service.put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="\x00",
            value={"x": 1},
            subject=None,
        )
    first = service.put(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        key="a",
        value={"x": 1},
        subject=None,
    )
    with pytest.raises(ValueError, match="record limit"):
        service.put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="b",
            value={"x": 2},
            subject=None,
        )
    assert (
        service.delete(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="missing",
            subject=None,
        )
        is False
    )
    with pytest.raises(ValueError, match="cursor"):
        service.list(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            subject=None,
            cursor="__8",
        )
    assert first.version == 1


def test_filesystem_and_s3_stores_cover_cleanup_and_namespace_guards(
    tmp_path: Path,
) -> None:
    """Both bundle-store adapters keep writes inside their private namespace."""
    store = FilesystemBundleStore(tmp_path / "bundles")
    missing = tmp_path / "bundles" / "staging" / "missing"
    store.delete_prefix(str(missing.relative_to(tmp_path / "bundles")))
    key = store.write_staged(uuid4(), BytesIO(b"archive"))
    store.delete_prefix(key)
    with pytest.raises(RuntimeError):
        broken = FilesystemBundleStore(tmp_path / "broken")
        broken._root.rmdir()  # noqa: SLF001
        broken.healthcheck()

    class FakeS3:
        def __init__(self) -> None:
            self.calls: list[tuple[str, object]] = []

        def upload_fileobj(self, source, bucket, key):
            self.calls.append(("upload", (bucket, key, source.read())))

        def get_object(self, **kwargs):
            self.calls.append(("get", kwargs))
            return {"Body": BytesIO(b"asset")}

        def get_paginator(self, _name):
            return SimpleNamespace(
                paginate=lambda **_: [{"Contents": [{"Key": "hosted/p"}]}]
            )

        def delete_objects(self, **kwargs):
            self.calls.append(("delete", kwargs))

        def head_bucket(self, **kwargs):
            self.calls.append(("head", kwargs))

    client = FakeS3()
    s3 = S3BundleStore(client, bucket="bucket", prefix="hosted")
    upload_id = uuid4()
    assert s3.write_staged(upload_id, BytesIO(b"zip")).startswith("hosted/")
    assert s3.open_staged("/hosted/staging/key").read() == b"asset"
    deployment = uuid4()
    s3.write_deployment_file(deployment, "index.html", BytesIO(b"html"))
    s3.write_manifest(deployment, BytesIO(b"{}"))
    assert s3.open_deployment_file(deployment, "index.html").read() == b"asset"
    s3.delete_prefix("hosted/staging")
    s3.healthcheck()
    with pytest.raises(ValueError):
        s3.open_staged("other/key")
    with pytest.raises(ValueError):
        S3BundleStore(client, bucket="", prefix="hosted")
    nested = tmp_path / "bundles" / "staging" / "with-link"
    nested.mkdir(parents=True)
    (nested / "target").write_bytes(b"x")
    (nested / "child").mkdir()
    (nested / "link").symlink_to(nested / "target")
    store.delete_prefix("staging/with-link")


def test_deployment_service_covers_archive_mismatch_expiry_and_unknown_upload() -> None:
    """Staged deployments record failure state for size/checksum/replay errors."""
    store = FilesystemBundleStore(Path("/tmp/orcheo-coverage-bundles"))
    service = DeploymentService(
        store, limits=BundleValidationLimits(max_archive_bytes=10_000)
    )
    with pytest.raises(BundleValidationError):
        service.initiate(
            workspace_id=uuid4(),
            app_id=uuid4(),
            created_by="test",
            expected_size_bytes=20_000,
        )
    with pytest.raises(UploadNotFoundError):
        service.stage(uuid4(), BytesIO())
    archive = _archive({"index.html": b"<h1>ok</h1>"})
    upload, deployment = service.initiate(
        workspace_id=uuid4(),
        app_id=uuid4(),
        created_by="test",
        expected_size_bytes=archive.getbuffer().nbytes + 1,
    )
    service.stage(upload.id, archive)
    with pytest.raises(BundleValidationError, match="size"):
        service.complete(upload.id)
    assert deployment.status.value == "failed"
    with pytest.raises(BundleValidationError, match="already"):
        service.complete(upload.id)


@pytest.mark.parametrize(
    "environment",
    [
        {"ORCHEO_HOSTED_APPS_ENABLED": "true"},
        {
            "ORCHEO_HOSTED_APPS_ENABLED": "true",
            "ORCHEO_APPS_BASE_DOMAIN": "apps.test",
            "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
            "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": "/tmp/bundles",
            "ORCHEO_DEPLOYMENT_MODE": "hosted",
        },
    ],
)
def test_settings_reject_invalid_feature_configuration(
    environment: dict[str, str],
) -> None:
    """Invalid flags, storage, and deployment combinations fail closed."""
    with pytest.raises(HostedAppsSettingsError):
        HostedAppsSettings.from_environment(environment)


def test_setup_preflight_checks_security_and_storage_contracts(
    tmp_path: Path, monkeypatch
) -> None:
    """Preflight validates secrets, queue, proxy, TLS, S3, and DNS requirements."""
    base = {
        "ORCHEO_HOSTED_APPS_ENABLED": "true",
        "ORCHEO_APPS_BASE_DOMAIN": "apps.test",
        "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
        "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": "/tmp/bundles",
        "ORCHEO_APP_GATEWAY_SECRET": "s" * 32,
        "ORCHEO_POSTGRES_DSN": "postgresql://test",
        "ORCHEO_HOSTED_APPS_VALIDATION_QUEUE": "hosted-apps",
    }
    assert (
        validate_hosted_apps_setup(base, check_dns=False)[0] == "base_domain=apps.test"
    )
    for key, value in (
        ("ORCHEO_APP_GATEWAY_SECRET", "short"),
        ("ORCHEO_POSTGRES_DSN", ""),
        ("ORCHEO_HOSTED_APPS_VALIDATION_QUEUE", ""),
        ("ORCHEO_APP_TRUSTED_PROXY_CIDRS", "10.0.0.0/8"),
        ("ORCHEO_APP_TLS_METHOD", "invalid"),
        ("ORCHEO_APP_DNS_PROVIDER", ""),
    ):
        env = dict(base)
        env[key] = value
        if key == "ORCHEO_APP_TRUSTED_PROXY_CIDRS":
            env["ORCHEO_APP_TRUSTED_PROXY_HOPS"] = "0"
        if key == "ORCHEO_APP_DNS_PROVIDER":
            env["ORCHEO_APP_TLS_METHOD"] = "dns-01"
        with pytest.raises(ValueError):
            validate_hosted_apps_setup(env, check_dns=False)
    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    cert.write_text("cert")
    key.write_text("key")
    provided = dict(
        base,
        ORCHEO_APP_TLS_METHOD="provided",
        ORCHEO_APP_TLS_CERT_FILE=str(cert),
        ORCHEO_APP_TLS_KEY_FILE=str(key),
    )
    assert (
        validate_hosted_apps_setup(provided, check_dns=False)[3]
        == "tls_method=provided"
    )
    monkeypatch.setattr(
        "orcheo.hosted_apps.setup_validation.socket.getaddrinfo",
        lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("dns")),
    )
    with pytest.raises(ValueError, match="DNS"):
        validate_hosted_apps_setup(base)


def test_zip_validation_covers_html_manifest_paths_and_limits() -> None:
    """ZIP validation rejects unsafe constructs and records CSP metadata."""
    valid = _archive(
        {
            "index.html": b"<script>foo &amp; bar</script>",
            "assets/app.js": b"console.log(1)",
        }
    )
    manifest = validate_bundle(valid)
    assert manifest.html_policy["index.html"]["inline_script_hashes"]
    cases = {
        "nested.zip": "Nested archives",
        "../index.html": "unsafe asset path",
        "__orcheo/x.js": "reserved __orcheo",
        "index.exe": "server executable",
        "main.js": "root index.html",
    }
    for name, code in cases.items():
        archive = _archive({name: b"MZ" if name.endswith(".exe") else b"x"})
        with pytest.raises(BundleValidationError, match=code):
            validate_bundle(archive)
    with pytest.raises(BundleValidationError, match="too many files"):
        validate_bundle(
            _archive({"index.html": b"x", "a.js": b"x"}),
            limits=BundleValidationLimits(max_file_count=1),
        )
    parser = _HtmlPolicyParser()
    with pytest.raises(BundleValidationError, match="unsupported executable"):
        parser.feed('<div onclick="go()"></div>')
    parser = _HtmlPolicyParser()
    with pytest.raises(BundleValidationError, match="unsupported executable"):
        parser.feed('<a href="javascript:go()">x</a>')
    parser = _HtmlPolicyParser()
    with pytest.raises(BundleValidationError, match="unsupported executable"):
        parser.feed("<script>oops")
        parser.close()


def test_model_manifest_and_name_validators_cover_normalization() -> None:
    """Domain model validators normalize accepted values and reject malformed ones."""
    assert normalize_alias(" Portal ") == "portal"
    assert normalize_logical_name(" greeting_1 ") == "greeting_1"
    with pytest.raises(Exception):
        normalize_alias("api")
    with pytest.raises(Exception):
        normalize_logical_name("Bad Name")
    with pytest.raises(ValueError, match="empty"):
        AppManifestBinding(workflow=" ", version=1, access_mode="anonymous")
    with pytest.raises(ValueError, match="projection"):
        AppManifestBinding(
            workflow="flow",
            version=1,
            access_mode="anonymous",
            output_projection={"bad": []},
        )
    assert (
        AppManifestBinding(
            workflow="flow", version=1, access_mode="anonymous", output_projection={}
        ).output_projection
        == {}
    )
    with pytest.raises(ValueError, match="limits"):
        AppManifestBinding(
            workflow="flow",
            version=1,
            access_mode="anonymous",
            limits={"max_concurrency": 0},
        )
    with pytest.raises(ValueError, match="Names must start"):
        AppManifest.model_validate(
            {
                "schema_version": 1,
                "bindings": {
                    "Bad Name": {
                        "workflow": "flow",
                        "version": 1,
                        "access_mode": "anonymous",
                    }
                },
            }
        )
    with pytest.raises(ValueError, match="normalized"):
        AppManifest.model_validate(
            {
                "schema_version": 1,
                "bindings": {
                    "flow ": {
                        "workflow": "flow",
                        "version": 1,
                        "access_mode": "anonymous",
                    }
                },
            }
        )


def test_runtime_service_covers_identity_limits_timeout_and_schema_paths() -> None:
    """Runtime acceptance enforces every visitor-visible boundary before dispatch."""
    binding = AppBinding(
        workspace_id=uuid4(),
        app_id=uuid4(),
        name="run",
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        workflow_execution_sha256="a" * 64,
        access_mode="anonymous",
        input_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 2}},
            "required": ["name"],
            "additionalProperties": False,
        },
        output_projection={"fields": ["answer"]},
        visitor_can_read_output=True,
        visitor_can_read_sanitized_errors=True,
    )
    service = AppRuntimeService(max_input_bytes=100, max_output_bytes=5)
    common = {
        "workspace_id": binding.workspace_id,
        "app_id": binding.app_id,
        "release_id": uuid4(),
        "deployment_id": uuid4(),
        "binding_snapshot_sha256": "b" * 64,
        "idempotency_key": "key",
        "runtime_generation": 1,
        "visitor_user_id": None,
        "session_id": None,
        "anonymous_visitor_id": "a" * 64,
    }
    with pytest.raises(AppRuntimeError, match="binding"):
        service.accept(
            binding,
            payload={"name": "ok"},
            workspace_id=uuid4(),
            app_id=binding.app_id,
            **{k: v for k, v in common.items() if k not in {"workspace_id", "app_id"}},
        )
    with pytest.raises(AppRuntimeError, match="Idempotency"):
        service.accept(
            binding, payload={"name": "ok"}, **dict(common, idempotency_key="")
        )
    with pytest.raises(AppRuntimeError, match="JSON"):
        service.accept(
            binding, payload=object(), **dict(common, idempotency_key="json")
        )
    with pytest.raises(AppRuntimeError, match="byte"):
        service.accept(
            binding,
            payload={"name": "x" * 200},
            **dict(common, idempotency_key="large"),
        )
    with pytest.raises(AppRuntimeError, match="schema"):
        service.accept(
            binding, payload={"name": "x"}, **dict(common, idempotency_key="schema")
        )
    with pytest.raises(AppRuntimeError, match="identity"):
        service.accept(
            binding,
            payload={"name": "ok"},
            **dict(common, anonymous_visitor_id=None, idempotency_key="identity"),
        )
    accepted = service.accept(
        binding, payload={"name": "ok"}, **dict(common, idempotency_key="accepted")
    )
    service.complete(accepted.handle, output={"answer": "long-output"})
    assert (
        service.status(
            accepted.handle,
            workspace_id=binding.workspace_id,
            app_id=binding.app_id,
            runtime_generation=1,
            visitor_user_id=None,
            session_id=None,
        ).error
        == "Workflow output exceeded the configured byte limit."
    )
    service.cancel(accepted.handle)
    fresh = service.accept(
        binding, payload={"name": "ok"}, **dict(common, idempotency_key="cancel")
    )
    service.cancel(fresh.handle)
    assert (
        service.status(
            fresh.handle,
            workspace_id=binding.workspace_id,
            app_id=binding.app_id,
            runtime_generation=1,
            visitor_user_id=None,
            session_id=None,
        ).status
        == "cancelled"
    )
    with pytest.raises(KeyError):
        service.cancel("missing")
    with pytest.raises(KeyError):
        service.complete("missing")
    state = service._runs[accepted.handle]  # noqa: SLF001
    state.timeout_at = _utcnow()
    assert (
        service.status(
            accepted.handle,
            workspace_id=binding.workspace_id,
            app_id=binding.app_id,
            runtime_generation=1,
            visitor_user_id=None,
            session_id=None,
        ).status
        == "failed"
    )
    with pytest.raises(AppRuntimeError):
        validate_input_schema({"type": "object", "unsupported": True})
    with pytest.raises(AppRuntimeError):
        validate_input_schema({"type": "object", "properties": {"x": "bad"}})
    service._invocation_windows[f"app:{binding.app_id}"] = deque(  # noqa: SLF001
        [_utcnow() - timedelta(minutes=2)]
    )
    service._enforce_invocation_limits(  # noqa: SLF001
        binding.model_copy(update={"limits": {"per_app_per_minute": 2}}),
        app_id=binding.app_id,
        client_ip=None,
        session_id=None,
        now=_utcnow(),
    )
    _validate_schema({}, {})
    with pytest.raises(AppRuntimeError):
        _validate_schema({}, {"unsupported": True})
    _validate_schema(3, {"type": "number", "minimum": 2, "maximum": 4})
    _validate_schema([], {"type": "array"})
    _validate_schema({"name": "ok"}, {"type": "object", "properties": {"name": {}}})
    _validate_schema({"other": 1}, {"type": "object", "properties": {"name": {}}})
    validate_input_schema(
        {
            "type": "object",
            "properties": {"child": {"type": "string"}},
            "items": {"type": "string"},
        }
    )


def test_configuration_and_storage_validation_edges(tmp_path: Path) -> None:
    """Cover strict scalar settings and private filesystem cleanup branches."""
    base = {
        "ORCHEO_HOSTED_APPS_ENABLED": "true",
        "ORCHEO_APPS_BASE_DOMAIN": "apps.test",
        "ORCHEO_APP_BUNDLE_BACKEND": "filesystem",
        "ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT": str(tmp_path / "bundles"),
    }
    for key, value in (
        ("ORCHEO_HOSTED_APPS_ENABLED", "maybe"),
        ("ORCHEO_APP_MAX_FILE_COUNT", "not-an-int"),
        ("ORCHEO_APP_MAX_FILE_COUNT", "0"),
        ("ORCHEO_APPS_BASE_DOMAIN", "https://apps.test"),
        ("ORCHEO_APPS_BASE_DOMAIN", "apps_test"),
    ):
        with pytest.raises(HostedAppsSettingsError):
            HostedAppsSettings.from_environment(dict(base, **{key: value}))
    with pytest.raises(HostedAppsSettingsError, match="EXPANDED"):
        HostedAppsSettings.from_environment(
            dict(
                base,
                ORCHEO_APP_MAX_ARCHIVE_BYTES="20",
                ORCHEO_APP_MAX_EXPANDED_BYTES="10",
            )
        )
    store = FilesystemBundleStore(tmp_path / "store")
    directory = tmp_path / "store" / "staging" / "nested"
    directory.mkdir(parents=True)
    (directory / "asset").write_bytes(b"x")
    store.delete_prefix("staging/nested")
    assert not directory.exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "store" / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        store._resolve("link/asset")  # noqa: SLF001


def test_data_deployments_and_gateway_error_boundaries(tmp_path: Path) -> None:
    """Exercise size, optimistic-concurrency, expiry, and proxy error contracts."""
    collection = _collection(scope="shared", max_records=2)
    service = AppDataService()
    with pytest.raises(ValueError, match="byte"):
        AppDataService().put(
            collection.model_copy(update={"max_document_bytes": 1}),
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="big",
            value={"x": 1},
            subject=None,
        )
    with pytest.raises(AppDataConflictError):
        service.put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="missing",
            value={"x": 1},
            subject=None,
            expected_version=1,
        )
    updated = service.put(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        key="existing",
        value={"x": 1},
        subject=None,
    )
    assert (
        service.put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="existing",
            value={"x": 2},
            subject=None,
            expected_version=updated.version,
        ).version
        == 2
    )
    assert (
        service.delete(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="existing",
            subject=None,
            expected_version=2,
        )
        is True
    )
    stale = service.put(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        key="stale",
        value={"x": 1},
        subject=None,
    )
    with pytest.raises(AppDataConflictError):
        service.delete(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="stale",
            subject=None,
            expected_version=stale.version + 1,
        )
    with pytest.raises(ValueError, match="no longer"):
        service.get(
            collection.model_copy(update={"deleted_at": _utcnow()}),
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="x",
            subject=None,
        )
    user_collection = _collection(scope="user")
    with pytest.raises(PermissionError):
        service.get(
            user_collection,
            workspace_id=user_collection.workspace_id,
            app_id=user_collection.app_id,
            key="x",
            subject=None,
        )
    anonymous_read_user_collection = user_collection.model_copy(
        update={"read_access": "anonymous"}
    )
    with pytest.raises(PermissionError, match="User-scoped"):
        service.get(
            anonymous_read_user_collection,
            workspace_id=user_collection.workspace_id,
            app_id=user_collection.app_id,
            key="x",
            subject=None,
        )
    with pytest.raises(ValueError, match="maximum JSON depth"):
        AppDataService(max_depth=0).put(
            collection,
            workspace_id=collection.workspace_id,
            app_id=collection.app_id,
            key="list",
            value=[{"x": 1}],
            subject=None,
        )
    service.put(
        collection,
        workspace_id=collection.workspace_id,
        app_id=collection.app_id,
        key="empty-list",
        value=[],
        subject=None,
    )
    store = FilesystemBundleStore(tmp_path / "deployments")
    deployment_service = DeploymentService(store, limits=BundleValidationLimits())
    archive = _archive({"index.html": b"ok"})
    expected = archive.getvalue()
    upload, deployment = deployment_service.initiate(
        workspace_id=uuid4(),
        app_id=uuid4(),
        created_by="test",
        expected_size_bytes=len(expected),
        expected_sha256="0" * 64,
    )
    deployment_service.stage(upload.id, BytesIO(expected))
    with pytest.raises(BundleValidationError, match="checksum"):
        deployment_service.complete(upload.id)
    assert deployment.status.value == "failed"
    with pytest.raises(UploadNotFoundError):
        deployment_service.get_deployment(uuid4())
    expired, _ = deployment_service.initiate(
        workspace_id=uuid4(), app_id=uuid4(), created_by="test", expected_size_bytes=1
    )
    expired.expires_at = _utcnow()
    with pytest.raises(BundleValidationError, match="expired"):
        deployment_service.stage(expired.id, BytesIO())
    with pytest.raises(ValueError, match="invalid"):
        derive_client_ip(
            "10.0.0.1",
            "not-an-ip",
            trusted_proxy_cidrs=("10.0.0.0/8",),
            trusted_hops=1,
        )
    with pytest.raises(Exception):
        canonical_app_host("app.apps.test:bad", "apps.test")


def test_zip_private_helpers_cover_archive_and_manifest_failures() -> None:
    """Validate malformed archives, manifests, paths, and parser edge cases."""
    with pytest.raises(BundleValidationError, match="valid ZIP"):
        validate_bundle(BytesIO(b"not a zip"))
    with pytest.raises(BundleValidationError, match="archive"):
        validate_bundle(
            BytesIO(b"x"), limits=BundleValidationLimits(max_archive_bytes=0)
        )
    with pytest.raises(BundleValidationError, match="UTF-8"):
        _parse_html_policy(b"\xff")
    with pytest.raises(BundleValidationError, match="manifest"):
        _parse_app_manifest(b"\xff")
    with pytest.raises(BundleValidationError, match="manifest"):
        _parse_app_manifest(b'{"bindings": {"x": {}}}')
    with pytest.raises(ValueError, match="Duplicate"):
        _unique_json_object([("x", 1), ("x", 2)])
    with pytest.raises(BundleValidationError, match="expands"):
        _hash_member(
            BytesIO(b"1234"),
            keep_content=False,
            max_file_bytes=10,
            max_expanded_bytes=3,
        )

    class Chunked:
        def __init__(self) -> None:
            self.chunks = iter((b"12345678", b"9", b""))

        def read(self, _size: int) -> bytes:
            return next(self.chunks)

    _hash_member(
        Chunked(),
        keep_content=False,
        max_file_bytes=100,
        max_expanded_bytes=100,
    )
    assert _content_type("asset.unknown") == "application/octet-stream"
    with pytest.raises(BundleValidationError, match="too deep"):
        validate_bundle(
            _archive({"index.html": b"ok", "a/b/c": b"x"}),
            limits=BundleValidationLimits(max_path_depth=2),
        )
    with pytest.raises(BundleValidationError, match="not a regular"):
        info = zipfile.ZipInfo("index.html")
        info.external_attr = 0o120777 << 16
        stream = BytesIO()
        with zipfile.ZipFile(stream, "w") as bundle:
            bundle.writestr(info, b"x")
        stream.seek(0)
        validate_bundle(stream)
    parser = _HtmlPolicyParser()
    parser.handle_startendtag("br", [])
    parser._inline_script_parts = []  # noqa: SLF001
    parser.handle_entityref("amp")
    parser.handle_charref("65")
    assert parser._inline_script_parts == ["&amp;", "&#65;"]  # noqa: SLF001
    parser.handle_endtag("script")
    parser.handle_entityref("ignored")
    parser.handle_charref("66")
    with pytest.raises(BundleValidationError, match="unsupported"):
        parser.handle_starttag("script", [])
        parser.handle_starttag("script", [])
    parser = _HtmlPolicyParser()
    with pytest.raises(BundleValidationError, match="unsupported"):
        parser.feed('<script type="text/plain">x</script>')
    parser = _HtmlPolicyParser()
    with pytest.raises(BundleValidationError, match="unsupported"):
        parser.handle_startendtag("script", [])
    parser = _HtmlPolicyParser()
    parser.feed("<script>&amp;&#65;</script>")
    assert parser.inline_script_hashes
    with pytest.raises(BundleValidationError, match="unsafe"):
        _normalize_member_path("bad\\path", BundleValidationLimits())
    with pytest.raises(BundleValidationError, match="unsafe"):
        _normalize_member_path("bad\x00path", BundleValidationLimits())
    with pytest.raises(BundleValidationError, match="exceeds"):
        validate_bundle(
            _archive({"index.html": b"1234"}),
            limits=BundleValidationLimits(max_file_bytes=2),
        )
    with pytest.raises(BundleValidationError, match="manifest"):
        validate_bundle(
            _archive({"index.html": b"ok", "orcheo.app.json": b"{}"}),
            limits=BundleValidationLimits(max_app_manifest_bytes=1, max_file_bytes=100),
        )
    with pytest.raises(BundleValidationError, match="exceeds"):
        _hash_member(
            BytesIO(b"123456789"),
            keep_content=True,
            max_file_bytes=8,
            max_expanded_bytes=100,
        )
    digest, content, first, size = _hash_member(
        BytesIO(b"123456789"),
        keep_content=True,
        max_file_bytes=100,
        max_expanded_bytes=100,
    )
    assert len(first) == 8 and content == b"123456789" and size == 9 and digest

    directory_archive = BytesIO()
    with zipfile.ZipFile(directory_archive, "w") as bundle:
        bundle.writestr("assets/", b"")
        bundle.writestr("index.html", b"ok")
    directory_archive.seek(0)
    assert validate_bundle(directory_archive).files["index.html"]

    class NonSeekable:
        def tell(self):
            raise OSError("not seekable")

    _verify_archive_size(NonSeekable(), BundleValidationLimits())
