"""Lint-style tests for tenant-aware repository queries."""

from __future__ import annotations
import inspect
from orcheo_backend.app.repository_postgres import _persistence as postgres_persistence
from orcheo_backend.app.repository_postgres import _runs as postgres_runs
from orcheo_backend.app.repository_sqlite import _persistence as sqlite_persistence
from orcheo_backend.app.repository_sqlite import _runs as sqlite_runs


def test_run_repository_sources_reference_tenant_id() -> None:
    """Run persistence helpers should explicitly reference tenant_id."""
    sources = {
        "sqlite_create_run": inspect.getsource(
            sqlite_persistence.SqlitePersistenceMixin._create_run_locked
        ),
        "postgres_create_run": inspect.getsource(
            postgres_persistence.PostgresPersistenceMixin._create_run_locked
        ),
        "sqlite_get_run": inspect.getsource(sqlite_runs.WorkflowRunMixin.get_run),
        "postgres_get_run": inspect.getsource(postgres_runs.WorkflowRunMixin.get_run),
        "sqlite_list_runs": inspect.getsource(
            sqlite_runs.WorkflowRunMixin.list_runs_for_workflow
        ),
        "postgres_list_runs": inspect.getsource(
            postgres_runs.WorkflowRunMixin.list_runs_for_workflow
        ),
    }

    for name, source in sources.items():
        assert "tenant_id" in source, name
