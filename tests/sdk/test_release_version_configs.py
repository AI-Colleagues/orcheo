"""Tests for repository release-version configuration."""

from __future__ import annotations

import configparser
import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_CONFIGS = (
    REPO_ROOT / ".bumpversion.cfg",
    REPO_ROOT / "apps" / "backend" / ".bumpversion.cfg",
    REPO_ROOT / "packages" / "sdk" / ".bumpversion.cfg",
    REPO_ROOT / "packages" / "agentensor" / ".bumpversion.cfg",
)
SEMVER_CONFIGS = (
    REPO_ROOT / "apps" / "studio" / ".bumpversion.cfg",
    REPO_ROOT / "apps" / "desktop" / ".bumpversion.cfg",
    REPO_ROOT / "deploy" / "stack" / ".bumpversion.cfg",
)


def _read_config(path: Path) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.read(path)
    return config


@pytest.mark.parametrize("path", PYTHON_CONFIGS)
def test_python_release_configs_accept_pep440_prereleases(path: Path) -> None:
    config = _read_config(path)
    settings = config["bumpversion"]
    pattern = re.compile(settings["parse"])

    assert pattern.fullmatch("1.2.3")
    assert pattern.fullmatch("1.2.3a1")
    assert pattern.fullmatch("1.2.3b2")
    assert pattern.fullmatch("1.2.3rc3")
    assert settings.getboolean("commit") is False
    assert settings.getboolean("tag") is False


@pytest.mark.parametrize("path", SEMVER_CONFIGS)
def test_semver_release_configs_accept_prereleases(path: Path) -> None:
    config = _read_config(path)
    settings = config["bumpversion"]
    pattern = re.compile(settings["parse"])

    assert pattern.fullmatch("1.2.3")
    assert pattern.fullmatch("1.2.3-alpha.1")
    assert pattern.fullmatch("1.2.3-beta.2")
    assert pattern.fullmatch("1.2.3-rc.3")
    assert settings.getboolean("commit") is False
    assert settings.getboolean("tag") is False
