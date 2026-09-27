#!/usr/bin/env python3
"""Bump a package version with bump2version, with opt-in prerelease support.

``major``, ``minor`` and ``patch`` produce a stable version unless ``--pre`` is
given, in which case the bumped version starts the requested prerelease phase
(``a1``/``b1``/``rc1`` for Python packages, ``-alpha.1``/``-beta.1``/``-rc.1``
for SemVer ones). ``prerel`` and ``prerel_num`` are passed straight through.
Unrecognised options (``--dry-run``, ``--list``, ``--no-tag`` ...) are
forwarded to bump2version.

Examples:
    uv run scripts/bump_version.py core patch               # 0.45.1 -> 0.45.2
    uv run scripts/bump_version.py core minor --pre alpha   # 0.45.1 -> 0.46.0a1
    uv run scripts/bump_version.py studio minor --pre rc    # -> 0.26.0-rc.1
    uv run scripts/bump_version.py core prerel_num          # 0.46.0a1 -> 0.46.0a2
    uv run scripts/bump_version.py core prerel              # 0.46.0a2 -> 0.46.0b1
"""

from __future__ import annotations
import argparse
import configparser
import re
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = {
    "core": ".",
    "backend": "apps/backend",
    "sdk": "packages/sdk",
    "agentensor": "packages/agentensor",
    "studio": "apps/studio",
    "desktop": "apps/desktop",
    "stack": "deploy/stack",
    "lean": "deploy/lean",
}
NUMBER_PARTS = ("major", "minor", "patch")
PRERELEASE_PARTS = ("prerel", "prerel_num")
PHASES = ("alpha", "beta", "rc")


def prerelease_version(config_path: Path, part: str, phase: str) -> str:
    """Return the first ``phase`` prerelease after bumping ``part``."""
    config = configparser.ConfigParser(interpolation=None)
    config.read(config_path)
    main = config["bumpversion"]
    current = main["current_version"]
    match = re.fullmatch(main["parse"], current)
    if match is None:
        raise SystemExit(f"Cannot parse current version {current!r}")

    numbers = {name: int(match[name]) for name in NUMBER_PARTS}
    index = NUMBER_PARTS.index(part)
    numbers[part] += 1
    for lower in NUMBER_PARTS[index + 1 :]:
        numbers[lower] = 0

    # The prerel values are ordered like PHASES, followed by the optional "final".
    labels = [
        value
        for value in config["bumpversion:part:prerel"]["values"].split()
        if value != "final"
    ]
    serialize = main["serialize"].strip().splitlines()[0].strip()
    return serialize.format(**numbers, prerel=labels[PHASES.index(phase)], prerel_num=1)


def main() -> int:
    """Parse arguments and run bump2version in the package directory."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("package", choices=PACKAGES)
    parser.add_argument("part", choices=NUMBER_PARTS + PRERELEASE_PARTS)
    parser.add_argument(
        "--pre",
        choices=PHASES,
        help="start a prerelease of the bumped version (major/minor/patch only)",
    )
    args, passthrough = parser.parse_known_args()

    package_dir = REPO_ROOT / PACKAGES[args.package]
    command = [sys.executable, "-m", "bumpversion", *passthrough]
    if args.pre:
        if args.part not in NUMBER_PARTS:
            parser.error("--pre only applies to major, minor, or patch")
        new_version = prerelease_version(
            package_dir / ".bumpversion.cfg", args.part, args.pre
        )
        command += ["--new-version", new_version]
    command.append(args.part)

    return subprocess.run(command, cwd=package_dir, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
