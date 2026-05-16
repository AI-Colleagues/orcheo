"""Lint-style tests for workspace-aware repository queries."""

from __future__ import annotations
import inspect
from orcheo_backend.app.repository_postgres import _persistence as postgres_persistence
from orcheo_backend.app.repository_postgres import _runs as postgres_runs


def test_run_repository_sources_reference_workspace_id() -> None:
    """Run persistence helpers should explicitly reference workspace_id."""
    sources = {
        "postgres_create_run": inspect.getsource(
            postgres_persistence.PostgresPersistenceMixin._create_run_locked
        ),
        "postgres_get_run": inspect.getsource(postgres_runs.WorkflowRunMixin.get_run),
        "postgres_list_runs": inspect.getsource(
            postgres_runs.WorkflowRunMixin.list_runs_for_workflow
        ),
    }

    for name, source in sources.items():
        assert "workspace_id" in source, name
