"""Shared fixtures for workflow repository backend tests."""

from __future__ import annotations
from collections.abc import AsyncIterator, Generator
from unittest.mock import patch
import pytest
import pytest_asyncio
from orcheo_backend.app.repository import (
    InMemoryWorkflowRepository,
    WorkflowRepository,
)


@pytest.fixture(autouse=True)
def mock_celery_enqueue(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Generator[None, None, None]:
    """Disable Celery enqueue for all repository tests to avoid Redis hangs."""
    if request.node.get_closest_marker("no_mock_celery"):
        # These tests exercise the real Celery publish path, which the backend
        # otherwise skips in favour of in-process execution.
        monkeypatch.setenv("ORCHEO_INPROCESS_EXECUTION", "false")
        yield
        return
    with patch(
        "orcheo_backend.app.repository_postgres._triggers._enqueue_run_for_execution"
    ):
        yield


@pytest_asyncio.fixture
async def repository(
    request: pytest.FixtureRequest,
) -> AsyncIterator[WorkflowRepository]:
    """Yield an in-memory repository instance for backend tests."""

    repo: WorkflowRepository = InMemoryWorkflowRepository()

    try:
        yield repo
    finally:
        await repo.reset()
