"""Tests for resolving first-party packages in published stack images."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from packaging.version import Version


REPO_ROOT = Path(__file__).resolve().parents[2]
RESOLVER_PATH = REPO_ROOT / "deploy" / "stack" / "resolve_orcheo_packages.py"
STACK_DOCKERFILE = REPO_ROOT / "deploy" / "stack" / "Dockerfile.orcheo"
STUDIO_DOCKERFILE = REPO_ROOT / "deploy" / "stack" / "Dockerfile.studio"


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


def test_stack_dockerfiles_accept_release_resolution_build_args() -> None:
    stack_content = STACK_DOCKERFILE.read_text(encoding="utf-8")
    studio_content = STUDIO_DOCKERFILE.read_text(encoding="utf-8")

    assert "ARG ORCHEO_RELEASE_CHANNEL=stable" in stack_content
    assert "ARG ORCHEO_PACKAGE_REQUIREMENTS=" in stack_content
    assert "--prerelease if-necessary-or-explicit" in stack_content
    assert "ARG ORCHEO_STUDIO_VERSION=" in studio_content
    assert "orcheo-studio@${ORCHEO_STUDIO_VERSION}" in studio_content
