"""Integration checks for the Postgres checkpoint backend."""

from __future__ import annotations
import os
import pytest
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import CheckpointMetadata, empty_checkpoint
from orcheo import config
from orcheo.persistence import create_checkpointer


@pytest.mark.asyncio
async def test_postgres_checkpointer_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure the Postgres checkpointer can persist and retrieve a checkpoint."""

    dsn = os.getenv("ORCHEO_POSTGRES_DSN")
    if dsn is None:
        pytest.skip("ORCHEO_POSTGRES_DSN is not configured.")

    monkeypatch.setenv("ORCHEO_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("ORCHEO_POSTGRES_DSN", dsn)

    settings = config.get_settings(refresh=True)

    try:
        async with create_checkpointer(settings) as checkpointer:
            await checkpointer.setup()

            thread_id = "integration-thread"
            checkpoint_ns = "pytest"
            run_config: RunnableConfig = {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                }
            }
            checkpoint = empty_checkpoint()
            metadata = CheckpointMetadata(
                source="input",
                step=0,
                writes={},
                parents={},
            )

            next_config = await checkpointer.aput(
                run_config,
                checkpoint,
                metadata,
                new_versions={},
            )

            assert next_config["configurable"]["checkpoint_id"] == checkpoint["id"]

            stored = await checkpointer.aget_tuple(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                    }
                }
            )

            assert stored is not None
            assert stored.checkpoint["id"] == checkpoint["id"]

            await checkpointer.adelete_thread(thread_id)
    except Exception as exc:
        _msg = str(exc)
        if "couldn't get a connection" in _msg or "Connection refused" in _msg:
            pytest.skip(f"PostgreSQL not reachable: {exc}")
        raise
