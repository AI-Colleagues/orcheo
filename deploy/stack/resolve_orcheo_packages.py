"""Resolve the first-party package set for an Orcheo stack image."""

from __future__ import annotations
import argparse
import json
import tomllib
from pathlib import Path
from typing import Any, Literal
from urllib.request import urlopen
from packaging.version import InvalidVersion, Version


ReleaseChannel = Literal["stable", "prerelease"]
PACKAGE_NAMES = ("orcheo", "orcheo-backend", "orcheo-sdk", "agentensor")
PACKAGE_PROJECT_FILES = {
    "orcheo": Path("pyproject.toml"),
    "orcheo-backend": Path("apps/backend/pyproject.toml"),
    "orcheo-sdk": Path("packages/sdk/pyproject.toml"),
    "agentensor": Path("packages/agentensor/pyproject.toml"),
}
STUDIO_PACKAGE_FILE = Path("apps/studio/package.json")
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


def _validate_declared_version(
    package: str,
    raw_version: object,
    channel: ReleaseChannel,
) -> Version:
    """Validate one version declared by the tagged repository revision."""
    if not isinstance(raw_version, str):
        raise ValueError(f"{package} does not declare a string version.")
    try:
        version = Version(raw_version)
    except InvalidVersion as exc:
        raise ValueError(
            f"{package} declares invalid version {raw_version!r}."
        ) from exc
    if version.is_devrelease or version.local is not None:
        raise ValueError(f"{package} declares unpublished version {raw_version!r}.")
    if channel == "stable" and version.is_prerelease:
        raise ValueError(
            f"Stable stack releases cannot include prerelease {package} {raw_version}."
        )
    return version


def resolve_declared_requirements(
    repository_root: Path,
    channel: ReleaseChannel,
) -> list[str]:
    """Resolve deterministic package pins from the tagged source revision."""
    requirements: list[str] = []
    for package, relative_path in PACKAGE_PROJECT_FILES.items():
        project_path = repository_root / relative_path
        project = tomllib.loads(project_path.read_text(encoding="utf-8"))
        version = _validate_declared_version(
            package,
            project.get("project", {}).get("version"),
            channel,
        )
        requirements.append(f"{package}=={version}")
    return requirements


def resolve_declared_studio_version(
    repository_root: Path,
    channel: ReleaseChannel,
) -> str:
    """Return the deterministic Studio package version declared by the tag."""
    package_path = repository_root / STUDIO_PACKAGE_FILE
    package = json.loads(package_path.read_text(encoding="utf-8"))
    if not isinstance(package, dict):
        raise ValueError("Studio package metadata must be a JSON object.")
    raw_version = package.get("version")
    _validate_declared_version(
        "orcheo-studio",
        raw_version,
        channel,
    )
    assert isinstance(raw_version, str)
    return raw_version


def _fetch_package_payload(
    package: str,
    *,
    timeout_seconds: int,
    retries: int,
) -> dict[str, Any]:
    """Fetch PyPI metadata with bounded retries for transient failures."""
    url = _PYPI_PACKAGE_URL.format(package=package)
    last_error: OSError | ValueError | UnicodeError | None = None
    for _ in range(max(1, retries + 1)):
        try:
            with urlopen(url, timeout=timeout_seconds) as response:  # noqa: S310
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Unexpected PyPI response for {package}.")
            return payload
        except (OSError, ValueError, UnicodeError) as exc:
            last_error = exc
    assert last_error is not None
    raise RuntimeError(
        f"Unable to resolve {package} from PyPI after {max(1, retries + 1)} attempts."
    ) from last_error


def resolve_requirements(
    channel: ReleaseChannel,
    *,
    timeout_seconds: int = 15,
    retries: int = 2,
) -> list[str]:
    """Resolve exact first-party requirements for a stack build."""
    requirements: list[str] = []
    for package in PACKAGE_NAMES:
        payload = _fetch_package_payload(
            package,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
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
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--studio-output", type=Path)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    if args.repository_root is not None:
        requirements = resolve_declared_requirements(
            args.repository_root,
            args.channel,
        )
        if args.studio_output is not None:
            studio_version = resolve_declared_studio_version(
                args.repository_root,
                args.channel,
            )
            args.studio_output.write_text(
                f"{studio_version}\n",
                encoding="utf-8",
            )
    else:
        if args.studio_output is not None:
            parser.error("--studio-output requires --repository-root")
        requirements = resolve_requirements(args.channel, retries=args.retries)
    args.output.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    for requirement in requirements:
        print(requirement)


if __name__ == "__main__":
    main()
