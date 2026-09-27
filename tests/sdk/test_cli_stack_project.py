"""Tests for locating the installed full or lean stack."""

from __future__ import annotations
from pathlib import Path
import pytest
from orcheo_sdk.cli.stack_project import (
    StackProject,
    resolve_installed_stack,
    resolve_lean_project_dir,
)


def _install(project_dir: Path) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "docker-compose.yml").write_text("services: {}\n")
    return project_dir


@pytest.fixture()
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.delenv("ORCHEO_STACK_DIR", raising=False)
    monkeypatch.delenv("ORCHEO_LEAN_DIR", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


def test_resolve_lean_project_dir_default_and_env(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert resolve_lean_project_dir() == home / ".orcheo" / "lean"
    monkeypatch.setenv("ORCHEO_LEAN_DIR", str(home / "custom"))
    assert resolve_lean_project_dir() == home / "custom"


def test_resolve_installed_stack_prefers_full_stack(home: Path) -> None:
    stack_dir = _install(home / ".orcheo" / "stack")
    _install(home / ".orcheo" / "lean")

    assert resolve_installed_stack(stack_dir) == StackProject(stack_dir, lean=False)


def test_resolve_installed_stack_falls_back_to_lean(home: Path) -> None:
    lean_dir = _install(home / ".orcheo" / "lean")

    project = resolve_installed_stack(home / ".orcheo" / "stack")

    assert project == StackProject(lean_dir, lean=True)
    assert project is not None
    assert project.compose_file == lean_dir / "docker-compose.yml"


def test_resolve_installed_stack_returns_none_without_install(home: Path) -> None:
    assert resolve_installed_stack(home / ".orcheo" / "stack") is None


def test_resolve_installed_stack_env_pins_full_stack(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install(home / ".orcheo" / "lean")
    stack_dir = home / "custom-stack"
    monkeypatch.setenv("ORCHEO_STACK_DIR", str(stack_dir))

    assert resolve_installed_stack(stack_dir) is None
    _install(stack_dir)
    assert resolve_installed_stack(stack_dir) == StackProject(stack_dir, lean=False)


def test_resolve_installed_stack_env_pins_lean_stack(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack_dir = _install(home / ".orcheo" / "stack")
    lean_dir = _install(home / "custom-lean")
    monkeypatch.setenv("ORCHEO_LEAN_DIR", str(lean_dir))

    assert resolve_installed_stack(stack_dir) == StackProject(lean_dir, lean=True)
