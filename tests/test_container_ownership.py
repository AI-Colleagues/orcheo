"""Ownership repair behavior on shared volumes and image directories."""

from __future__ import annotations
import subprocess
from pathlib import Path
import pytest


@pytest.mark.parametrize(
    ("initialized", "directory", "recursive"),
    [
        ("false", "/data/home", True),
        ("true", "/data/home", False),
        ("true", "/app/.venv", True),
    ],
)
def test_shared_volume_ownership_is_only_walked_during_initialization(
    initialized: str, directory: str, recursive: bool
) -> None:
    """Restarts keep image ownership repair while avoiding repeated volume scans."""
    script = (
        Path(__file__).parents[1] / "deploy/stack/orcheo-entrypoint.sh"
    ).read_text()
    function = script[
        script.index("ensure_dir() {") : script.index("same_path_target() {")
    ]
    result = subprocess.run(
        [
            "sh",
            "-c",
            'runtime_user=orcheo; runtime_group=orcheo; ownership_initialized="$1"; '
            'mkdir() { :; }; chown() { printf "%s\\n" "$*"; }; '
            + function
            + '\nensure_dir "$2" true',
            "ownership-test",
            initialized,
            directory,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == (
        ("-R " if recursive else "") + "orcheo:orcheo " + directory
    )
