"""Resolve the first-party package set for an Orcheo stack image."""

from __future__ import annotations
import argparse
import json
from pathlib import Path
from typing import Any, Literal
from urllib.request import urlopen
from packaging.version import InvalidVersion, Version


ReleaseChannel = Literal["stable", "prerelease"]
PACKAGE_NAMES = ("orcheo", "orcheo-backend", "orcheo-sdk", "agentensor")
_PYPI_PACKAGE_URL = "https://pypi.org/pypi/{package}/json"


def _published_versions(payload: dict[str, Any]) -> list[Version]:
    """Return usable public versions advertised by a PyPI project response."""
    releases = payload.get("releases")
    if not isinstance(releases, dict):
        return []

    versions: list[Version] = []
    for raw_version, files in releases.items():
        if not isinstance(raw_version, str) or not isinstance(files, list) or not files:
            continue
        if all(isinstance(item, dict) and item.get("yanked") for item in files):
            continue
        try:
            parsed = Version(raw_version)
        except InvalidVersion:
            continue
        if parsed.is_devrelease or parsed.local is not None:
            continue
        versions.append(parsed)
    return versions


def select_version(payload: dict[str, Any], channel: ReleaseChannel) -> Version:
    """Select the newest stable release or newest public release candidate."""
    versions = _published_versions(payload)
    if channel == "stable":
        versions = [version for version in versions if not version.is_prerelease]
    if not versions:
        raise ValueError(f"No {channel} package versions are available.")
    return max(versions)


def resolve_requirements(
    channel: ReleaseChannel,
    *,
    timeout_seconds: int = 15,
) -> list[str]:
    """Resolve exact first-party requirements for a stack build."""
    requirements: list[str] = []
    for package in PACKAGE_NAMES:
        url = _PYPI_PACKAGE_URL.format(package=package)
        with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected PyPI response for {package}.")
        version = select_version(payload, channel)
        requirements.append(f"{package}=={version}")
    return requirements


def main() -> None:
    """Resolve and write the package requirements used by a stack image."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--channel",
        choices=("stable", "prerelease"),
        required=True,
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    requirements = resolve_requirements(args.channel)
    args.output.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    for requirement in requirements:
        print(requirement)


if __name__ == "__main__":
    main()
