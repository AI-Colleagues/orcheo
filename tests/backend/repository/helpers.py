"""Helper utilities shared across workflow repository tests."""

from __future__ import annotations
import sqlite3
from uuid import UUID
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    SqliteWorkflowRepository,
    WorkflowRepository,
)


async def _remove_version(repository: WorkflowRepository, version_id: UUID) -> None:
    """Remove a workflow version for backend-specific implementations."""

    if isinstance(repository, InMemoryWorkflowRepository):
        repository._versions.pop(version_id, None)  # noqa: SLF001 - test helper
        for versions in repository._workflow_versions.values():  # noqa: SLF001
            if version_id in versions:
                versions.remove(version_id)
        repository._version_runs.pop(version_id, None)  # noqa: SLF001
        return

    if isinstance(repository, SqliteWorkflowRepository):
        conn = sqlite3.connect(str(repository._database_path))  # type: ignore[attr-defined]  # noqa: SLF001
        try:
            conn.execute(
                "DELETE FROM workflow_runs WHERE workflow_version_id = ?",
                (str(version_id),),
            )
            conn.execute(
                "DELETE FROM workflow_versions WHERE id = ?", (str(version_id),)
            )
            conn.commit()
        finally:
            conn.close()
        return

    raise AssertionError(f"Unsupported repository type: {type(repository)!r}")
