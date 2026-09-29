"""Locate the installed Docker Compose stack that the CLI manages."""

from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path


_COMPOSE_FILE = "docker-compose.yml"
LEAN_COMPOSE_WAIT_TIMEOUT = "120"

STACK_NOT_FOUND_MESSAGE = (
    "Stack docker-compose file not found. Run 'orcheo install --yes' or "
    "'orcheo install --lean' first."
)


@dataclass(frozen=True, slots=True)
class StackProject:
    """An installed compose project: the full stack or the lean stack."""

    project_dir: Path
    lean: bool

    @property
    def compose_file(self) -> Path:
        """Return the project's compose file."""
        return self.project_dir / _COMPOSE_FILE


def resolve_lean_project_dir() -> Path:
    """Return the ``orcheo install --lean`` directory."""
    configured = os.getenv("ORCHEO_LEAN_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".orcheo" / "lean"


def resolve_installed_stack(stack_dir: Path) -> StackProject | None:
    """Return the installed stack, preferring the full stack in ``stack_dir``.

    ``ORCHEO_STACK_DIR`` pins the full stack and ``ORCHEO_LEAN_DIR`` pins the
    lean stack, so either picks one when both are installed.
    """
    full = StackProject(stack_dir, lean=False)
    lean = StackProject(resolve_lean_project_dir(), lean=True)
    if os.getenv("ORCHEO_STACK_DIR"):
        candidates: tuple[StackProject, ...] = (full,)
    elif os.getenv("ORCHEO_LEAN_DIR"):
        candidates = (lean,)
    else:
        candidates = (full, lean)
    return next(
        (project for project in candidates if project.compose_file.is_file()),
        None,
    )


__all__ = [
    "LEAN_COMPOSE_WAIT_TIMEOUT",
    "STACK_NOT_FOUND_MESSAGE",
    "StackProject",
    "resolve_installed_stack",
    "resolve_lean_project_dir",
]
