"""Convenience commands exposed as uv-friendly entry points."""

from __future__ import annotations
import subprocess
from collections.abc import Sequence


def _run(command: Sequence[str]) -> None:
    """Run a shell command and exit if it fails."""
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:  # pragma: no cover - propagated via SystemExit
        raise SystemExit(completed.returncode)


def dev_server() -> None:
    """Start the FastAPI development server."""
    _run(
        [
            "uvicorn",
            "--app-dir",
            "apps/backend/src",
            "orcheo_backend.app:app",
            "--reload",
            "--port",
            "2025",
        ]
    )


def lint() -> None:
    """Run linting and type checks."""
    _run(["ruff", "check", "src/orcheo", "packages/sdk/src", "apps/backend/src"])
    _run(
        [
            "mypy",
            "src/orcheo",
            "packages/sdk/src",
            "apps/backend/src",
        ]
    )
    _run(["ruff", "format", ".", "--check"])


def format_code() -> None:
    """Format Python code and organize imports."""
    _run(["ruff", "format", "."])
    _run(["ruff", "check", ".", "--select", "I001", "--fix"])
    _run(["ruff", "check", ".", "--select", "F401", "--fix"])


def test() -> None:
    """Run the pytest suite with coverage reporting."""
    _run(["pytest", "--cov", "--cov-report", "term-missing", "tests/"])


def studio_lint() -> None:
    """Lint the React studio application."""
    _run(["npm", "--prefix", "apps/studio", "run", "lint"])


def studio_dev() -> None:
    """Start the React studio development server."""
    _run(["npm", "--prefix", "apps/studio", "run", "dev"])
