"""Tests for resolving first-party packages in published stack images."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from packaging.version import Version


REPO_ROOT = Path(__file__).resolve().parents[2]
RESOLVER_PATH = REPO_ROOT / "deploy" / "stack" / "resolve_orcheo_packages.py"
STACK_DOCKERFILE = REPO_ROOT / "deploy" / "stack" / "Dockerfile.orcheo"
STUDIO_DOCKERFILE = REPO_ROOT / "deploy" / "stack" / "Dockerfile.studio"
APP_GATEWAY_DOCKERFILE = REPO_ROOT / "Dockerfile.app-gateway"
RELEASE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


def _load_resolver() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "resolve_orcheo_packages",
        RESOLVER_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _payload(*versions: str) -> dict[str, object]:
    return {
        "releases": {
            version: [{"filename": f"package-{version}.whl", "yanked": False}]
            for version in versions
        }
    }


def test_stable_channel_selects_latest_stable_version() -> None:
    resolver = _load_resolver()

    selected = resolver.select_version(
        _payload("1.0.0", "1.1.0a1", "1.0.1", "1.1.0rc1"),
        "stable",
    )

    assert selected == Version("1.0.1")


def test_prerelease_channel_selects_newer_prerelease_when_available() -> None:
    resolver = _load_resolver()

    selected = resolver.select_version(
        _payload("1.0.0", "1.1.0a1", "1.1.0b2", "1.1.0rc1"),
        "prerelease",
    )

    assert selected == Version("1.1.0rc1")


def test_prerelease_channel_falls_back_to_newest_stable() -> None:
    resolver = _load_resolver()

    selected = resolver.select_version(
        _payload("1.0.0a1", "1.0.0", "1.0.1"),
        "prerelease",
    )

    assert selected == Version("1.0.1")


def test_resolver_ignores_yanked_invalid_and_development_versions() -> None:
    resolver = _load_resolver()
    payload = _payload("1.0.0", "1.1.0.dev1", "not-a-version")
    payload["releases"]["2.0.0"] = [{"filename": "yanked.whl", "yanked": True}]

    selected = resolver.select_version(payload, "prerelease")

    assert selected == Version("1.0.0")


def test_stable_channel_fails_without_stable_package() -> None:
    resolver = _load_resolver()

    with pytest.raises(ValueError, match="No stable package versions"):
        resolver.select_version(_payload("1.0.0a1", "1.0.0rc1"), "stable")


def _write_project_version(root: Path, relative_path: str, version: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[project]\nname = "fixture"\nversion = "{version}"\n',
        encoding="utf-8",
    )


def test_declared_versions_make_tagged_stack_rebuilds_deterministic(
    tmp_path: Path,
) -> None:
    resolver = _load_resolver()
    _write_project_version(tmp_path, "pyproject.toml", "1.2.3")
    _write_project_version(tmp_path, "apps/backend/pyproject.toml", "2.3.4")
    _write_project_version(tmp_path, "packages/sdk/pyproject.toml", "3.4.5")
    _write_project_version(tmp_path, "packages/agentensor/pyproject.toml", "4.5.6")
    studio_package = tmp_path / "apps/studio/package.json"
    studio_package.parent.mkdir(parents=True)
    studio_package.write_text('{"version": "5.6.7-rc.1"}\n', encoding="utf-8")

    requirements = resolver.resolve_declared_requirements(tmp_path, "stable")
    studio_version = resolver.resolve_declared_studio_version(tmp_path, "prerelease")

    assert requirements == [
        "orcheo==1.2.3",
        "orcheo-backend==2.3.4",
        "orcheo-sdk==3.4.5",
        "agentensor==4.5.6",
    ]
    assert studio_version == "5.6.7-rc.1"


def test_stable_stack_rejects_declared_prerelease(tmp_path: Path) -> None:
    resolver = _load_resolver()
    _write_project_version(tmp_path, "pyproject.toml", "1.2.3rc1")
    for relative_path in (
        "apps/backend/pyproject.toml",
        "packages/sdk/pyproject.toml",
        "packages/agentensor/pyproject.toml",
    ):
        _write_project_version(tmp_path, relative_path, "1.2.3")

    with pytest.raises(ValueError, match="Stable stack releases"):
        resolver.resolve_declared_requirements(tmp_path, "stable")


def test_registry_fetch_retries_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = _load_resolver()
    attempts = 0

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *args: Any) -> None:
            return None

        def read(self) -> bytes:
            return b'{"releases": {"1.0.0": [{"yanked": false}]}}'

    def fake_urlopen(url: str, timeout: int) -> Response:
        nonlocal attempts
        del url, timeout
        attempts += 1
        if attempts < 3:
            raise OSError("temporary registry failure")
        return Response()

    monkeypatch.setattr(resolver, "urlopen", fake_urlopen)

    payload = resolver._fetch_package_payload(
        "orcheo",
        timeout_seconds=1,
        retries=2,
    )

    assert attempts == 3
    assert resolver.select_version(payload, "stable") == Version("1.0.0")


def test_stack_dockerfiles_accept_release_resolution_build_args() -> None:
    stack_content = STACK_DOCKERFILE.read_text(encoding="utf-8")
    studio_content = STUDIO_DOCKERFILE.read_text(encoding="utf-8")
    gateway_content = APP_GATEWAY_DOCKERFILE.read_text(encoding="utf-8")
    workflow_content = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "ARG ORCHEO_RELEASE_CHANNEL=stable" in stack_content
    assert "ARG ORCHEO_PACKAGE_REQUIREMENTS=" in stack_content
    assert "--prerelease if-necessary-or-explicit" in stack_content
    assert "ARG ORCHEO_STUDIO_VERSION=" in studio_content
    assert "orcheo-studio@${ORCHEO_STUDIO_VERSION}" in studio_content
    assert "COPY apps/app_gateway/src/orcheo_app_gateway" in gateway_content
    assert "app_gateway_repo=ghcr.io/${owner_lc}/orcheo-app-gateway" in workflow_content
    assert "file: Dockerfile.app-gateway" in workflow_content
