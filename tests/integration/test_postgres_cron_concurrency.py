"""Exercise cron transactions using independent pools against disposable tables."""

from __future__ import annotations
import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import Mock
from uuid import uuid4
import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb
from orcheo.models import Workflow, WorkflowRun, WorkflowVersion
from orcheo.triggers.cron import CronTriggerConfig
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository
from orcheo_backend.app.repository_postgres import _triggers
from orcheo_backend.app.repository_postgres._base import POSTGRES_SCHEMA


NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)


@dataclass
class CronDatabase:
    dsn: str
    repositories: tuple[PostgresWorkflowRepository, PostgresWorkflowRepository]
    workflow: Workflow
    publish: Mock


@pytest_asyncio.fixture
async def cron_database(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CronDatabase]:
    dsn = os.getenv("ORCHEO_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Requires an isolated ORCHEO_TEST_POSTGRES_DSN.")
    schema = "cron_test_" + uuid4().hex
    scoped = make_conninfo(dsn, options=f"-c search_path={schema}")
    repositories = tuple(
        PostgresWorkflowRepository(
            make_conninfo(scoped, application_name=f"dispatcher-{index}"),
            pool_max_size=1,
        )
        for index in range(2)
    )
    for repo in repositories:
        repo._initialized = True
    workflow = Workflow(name="Concurrent cron")
    version = WorkflowVersion(workflow_id=workflow.id, version=1, created_by="test")
    config = CronTriggerConfig(expression="*/5 * * * *", timezone="UTC", start_at=NOW)
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        with psycopg.connect(scoped) as conn:
            conn.execute(POSTGRES_SCHEMA)
            conn.execute(
                "INSERT INTO workflows (id, payload, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s)",
                (
                    str(workflow.id),
                    Jsonb(workflow.model_dump(mode="json")),
                    workflow.created_at,
                    workflow.updated_at,
                ),
            )
            conn.execute(
                "INSERT INTO workflow_versions "
                "(id, workflow_id, version, payload, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    str(version.id),
                    str(workflow.id),
                    1,
                    Jsonb(version.model_dump(mode="json")),
                    version.created_at,
                    version.updated_at,
                ),
            )
            conn.execute(
                "INSERT INTO cron_triggers (workflow_id, config) VALUES (%s, %s)",
                (str(workflow.id), Jsonb(config.model_dump(mode="json"))),
            )

        def publish(run: WorkflowRun) -> bool:
            # A different connection must see the run before it is published.
            with psycopg.connect(scoped) as conn:
                assert conn.execute(
                    "SELECT id FROM workflow_runs WHERE id = %s", (str(run.id),)
                ).fetchone()
            return True

        publisher = Mock(side_effect=publish)
        monkeypatch.setattr(_triggers, "_enqueue_run_for_execution", publisher)
        yield CronDatabase(scoped, repositories, workflow, publisher)
    finally:
        for repo in repositories:
            await repo.close()
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.mark.parametrize("allow_overlapping", [False, True])
@pytest.mark.asyncio
async def test_concurrent_dispatchers_create_one_run_per_occurrence(
    cron_database: CronDatabase,
    monkeypatch: pytest.MonkeyPatch,
    allow_overlapping: bool,
) -> None:
    database = cron_database
    with psycopg.connect(database.dsn) as conn:
        conn.execute(
            "UPDATE cron_triggers SET config = jsonb_set(config, '{allow_overlapping}', %s)",
            (Jsonb(allow_overlapping),),
        )
    barrier = asyncio.Barrier(2)
    for repo in database.repositories:
        original = repo._create_cron_run_once

        async def synchronized(
            *args: Any, original: Any = original, **kwargs: Any
        ) -> WorkflowRun | None:
            await barrier.wait()
            return await original(*args, **kwargs)

        monkeypatch.setattr(repo, "_create_cron_run_once", synchronized)
    first, second = await asyncio.wait_for(
        asyncio.gather(
            *(repo.dispatch_due_cron_runs(now=NOW) for repo in database.repositories)
        ),
        timeout=5,
    )
    assert len(first) + len(second) == 1
    database.publish.assert_called_once()
    with psycopg.connect(database.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM workflow_runs").fetchone() == (1,)
        assert conn.execute(
            "SELECT last_dispatched_at FROM cron_triggers"
        ).fetchone() == (NOW,)


@pytest.mark.asyncio
async def test_locked_occurrence_is_skipped_and_active_run_blocks_next_occurrence(
    cron_database: CronDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = cron_database.repositories
    inserted, release = asyncio.Event(), asyncio.Event()
    original = first._create_run_locked

    async def hold_transaction(**kwargs: Any) -> WorkflowRun:
        run = await original(**kwargs)
        inserted.set()
        await release.wait()
        return run

    monkeypatch.setattr(first, "_create_run_locked", hold_transaction)
    task = asyncio.create_task(first.dispatch_due_cron_runs(now=NOW))
    try:
        await asyncio.wait_for(inserted.wait(), timeout=5)
        assert (
            await asyncio.wait_for(second.dispatch_due_cron_runs(now=NOW), timeout=2)
            == []
        )
    finally:
        release.set()
        await task
    assert await second.dispatch_due_cron_runs(now=NOW) == []
    assert await second.dispatch_due_cron_runs(now=NOW + timedelta(minutes=5)) == []
    cron_database.publish.assert_called_once()


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.asyncio
async def test_failed_dispatch_rolls_back_run_and_releases_schedule(
    cron_database: CronDatabase, monkeypatch: pytest.MonkeyPatch, cancel: bool
) -> None:
    first, second = cron_database.repositories
    original = first._create_run_locked

    async def fail_after_insert(**kwargs: Any) -> WorkflowRun:
        await original(**kwargs)
        if cancel:
            raise asyncio.CancelledError
        raise RuntimeError("failed after insert")

    monkeypatch.setattr(first, "_create_run_locked", fail_after_insert)
    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await first.dispatch_due_cron_runs(now=NOW)
    with psycopg.connect(cron_database.dsn) as conn:
        assert conn.execute("SELECT count(*) FROM workflow_runs").fetchone() == (0,)
        assert conn.execute(
            "SELECT last_dispatched_at FROM cron_triggers"
        ).fetchone() == (None,)
    assert len(await second.dispatch_due_cron_runs(now=NOW)) == 1
    cron_database.publish.assert_called_once()
