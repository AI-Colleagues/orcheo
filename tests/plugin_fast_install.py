"""Fast plugin fixture installation helpers for tests."""

from __future__ import annotations

import shutil
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest


def install_fast_fixture_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch plugin installs for local test fixtures.

    The plugin manager normally shells out to ``uv venv`` and ``uv pip install``.
    That is useful coverage in a few command-wrapper tests, but most plugin
    lifecycle tests only need an importable package with entry point metadata.
    """

    from orcheo.plugins import manager as manager_module

    real_install_refs = manager_module._install_refs_into_venv

    def fast_ensure_venv(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        bin_dir = path / "bin"
        bin_dir.mkdir(exist_ok=True)
        python = bin_dir / "python"
        if not python.exists():
            python.write_text(
                f'#! /bin/sh\nexec {sys.executable!r} "$@"\n',
                encoding="utf-8",
            )
            python.chmod(0o755)
        _site_packages(path).mkdir(parents=True, exist_ok=True)

    def fast_install_refs(venv_dir: Path, refs: list[str]) -> None:
        fallback_refs: list[str] = []
        for ref in refs:
            source = Path(ref)
            if _is_fixture_plugin(source):
                _install_fixture_plugin(venv_dir, source)
            else:
                fallback_refs.append(ref)
        if fallback_refs:
            real_install_refs(venv_dir, fallback_refs)

    monkeypatch.setattr(manager_module, "_ensure_venv", fast_ensure_venv)
    monkeypatch.setattr(manager_module, "_install_refs_into_venv", fast_install_refs)


def _site_packages(venv_dir: Path) -> Path:
    return (
        venv_dir
        / "lib"
        / f"python{sys.version_info.major}.{sys.version_info.minor}"
        / "site-packages"
    )


def _is_fixture_plugin(source: Path) -> bool:
    src_dir = source / "src"
    packages = (
        [path for path in src_dir.iterdir() if path.is_dir()]
        if src_dir.exists()
        else []
    )
    return (
        source.is_dir()
        and (source / "pyproject.toml").exists()
        and len(packages) == 1
        and (packages[0] / "orcheo_plugin.toml").exists()
    )


def _install_fixture_plugin(venv_dir: Path, source: Path) -> None:
    pyproject = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject["project"]
    package = _package_name(source)
    site_packages = _site_packages(venv_dir)
    site_packages.mkdir(parents=True, exist_ok=True)

    destination = site_packages / package
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source / "src" / package,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    name = str(project["name"])
    version = str(project["version"])
    dist_info = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
    if dist_info.exists():
        shutil.rmtree(dist_info)
    dist_info.mkdir()

    (dist_info / "METADATA").write_text(_metadata(project), encoding="utf-8")
    (dist_info / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: orcheo-tests\nRoot-Is-Purelib: true\n"
        "Tag: py3-none-any\n",
        encoding="utf-8",
    )
    (dist_info / "entry_points.txt").write_text(
        _entry_points(project), encoding="utf-8"
    )
    _write_record(site_packages, [destination, dist_info])


def _package_name(source: Path) -> str:
    packages = [path.name for path in (source / "src").iterdir() if path.is_dir()]
    if len(packages) != 1:
        msg = f"Expected exactly one fixture package in {source / 'src'}"
        raise AssertionError(msg)
    return packages[0]


def _metadata(project: dict[str, Any]) -> str:
    author = ""
    authors = project.get("authors")
    if isinstance(authors, list) and authors:
        first_author = authors[0]
        if isinstance(first_author, dict):
            author = str(first_author.get("name", ""))
    return (
        "Metadata-Version: 2.1\n"
        f"Name: {project['name']}\n"
        f"Version: {project['version']}\n"
        f"Summary: {project.get('description', '')}\n"
        f"Author: {author}\n"
        f"Requires-Python: {project.get('requires-python', '')}\n"
    )


def _entry_points(project: dict[str, Any]) -> str:
    groups = project.get("entry-points", {})
    plugin_entries = groups.get("orcheo.plugins", {})
    lines = ["[orcheo.plugins]"]
    for name, value in plugin_entries.items():
        lines.append(f"{name} = {value}")
    return "\n".join(lines) + "\n"


def _write_record(site_packages: Path, roots: Iterable[Path]) -> None:
    rows: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                rows.append(f"{path.relative_to(site_packages).as_posix()},,")
    record = next(path for path in roots if path.name.endswith(".dist-info")) / "RECORD"
    rows.append(f"{record.relative_to(site_packages).as_posix()},,")
    record.write_text("\n".join(rows) + "\n", encoding="utf-8")
