"""Opt-in batching checks against disposable PostgreSQL tables."""

from __future__ import annotations
import os
from uuid import uuid4
import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from orcheo.models import WorkflowVersion
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository


@pytest.mark.asyncio
async def test_postgres_summary_query_latest_version_and_workspace_isolation() -> None:
    """Execute the batch SQL with real joins, empty versions, and another tenant."""
    dsn = os.getenv("ORCHEO_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Requires an isolated ORCHEO_TEST_POSTGRES_DSN.")
    schema = "summary_test_" + uuid4().hex
    scoped_dsn = make_conninfo(dsn, options=f"-c search_path={schema}")
    repo = PostgresWorkflowRepository(scoped_dsn)
    repo._initialized = True
    ids = [uuid4() for _ in range(3)]
    version = WorkflowVersion(workflow_id=ids[0], version=2, created_by="test")
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with psycopg.connect(scoped_dsn) as conn:
            conn.execute(
                "CREATE TABLE workflows (id text PRIMARY KEY, workspace_id text)"
            )
            conn.execute(
                "CREATE TABLE workflow_versions "
                "(workflow_id text, version integer, payload jsonb)"
            )
            conn.execute("CREATE TABLE cron_triggers (workflow_id text PRIMARY KEY)")
            for index, workflow_id in enumerate(ids):
                conn.execute(
                    "INSERT INTO workflows VALUES (%s, %s)",
                    (str(workflow_id), "workspace-b" if index == 2 else "workspace-a"),
                )
            for number in (1, 2):
                item = version.model_copy(update={"version": number})
                conn.execute(
                    "INSERT INTO workflow_versions VALUES (%s, %s, %s)",
                    (str(ids[0]), number, Jsonb(item.model_dump(mode="json"))),
                )
            conn.execute("INSERT INTO cron_triggers VALUES (%s)", (str(ids[0]),))
        summaries = await repo.get_workflow_summaries(ids, workspace_id="workspace-a")
        assert set(summaries) == set(ids[:2])
        latest, scheduled = summaries[ids[0]]
        assert latest is not None and latest.version == 2
        assert scheduled
        assert summaries[ids[1]] == (None, False)
    finally:
        await repo.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
