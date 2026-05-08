"""Tests for PostgreSQL remediation repository support."""

from __future__ import annotations
import json
from typing import Any
from uuid import UUID, uuid4
import pytest
from orcheo.models.workflow import (
    WorkflowRunRemediation,
    WorkflowRunRemediationClassification,
    WorkflowRunRemediationStatus,
)
from orcheo_backend.app.repository.errors import WorkflowRunRemediationNotFoundError
from orcheo_backend.app.repository_postgres import PostgresWorkflowRepository
from orcheo_backend.app.repository_postgres import _base as pg_base


class FakeRow(dict[str, Any]):
    """Fake row supporting both key and integer access."""

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class FakeCursor:
    """Fake cursor returning pre-configured rows."""

    def __init__(
        self,
        *,
        row: dict[str, Any] | None = None,
        rows: list[Any] | None = None,
        rowcount: int = 1,
    ) -> None:
        self._row = FakeRow(row) if row else None
        self._rows = [FakeRow(r) if isinstance(r, dict) else r for r in (rows or [])]
        self.rowcount = rowcount

    async def fetchone(self) -> FakeRow | None:
        return self._row

    async def fetchall(self) -> list[Any]:
        return list(self._rows)


class FakeConnection:
    """Fake connection recording queries and serving configured responses."""

    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.queries: list[tuple[str, Any | None]] = []

    async def execute(self, query: str, params: Any | None = None) -> FakeCursor:
        self.queries.append((query.strip(), params))
        response = self._responses.pop(0) if self._responses else {}
        if isinstance(response, FakeCursor):
            return response
        if isinstance(response, dict):
            return FakeCursor(
                row=response.get("row"),
                rows=response.get("rows"),
                rowcount=response.get("rowcount", 1),
            )
        if isinstance(response, list):
            return FakeCursor(rows=response)
        return FakeCursor()

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class FakePool:
    """Fake pool returning a single shared connection."""

    def __init__(self, connection: FakeConnection) -> None:
        self._connection = connection

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def connection(self) -> FakeConnection:
        return self._connection


def make_repo(
    monkeypatch: pytest.MonkeyPatch,
    responses: list[Any],
    *,
    initialized: bool = True,
) -> PostgresWorkflowRepository:
    monkeypatch.setattr(pg_base, "AsyncConnectionPool", object())
    monkeypatch.setattr(pg_base, "DictRowFactory", object())
    repo = PostgresWorkflowRepository("postgresql://test")
    repo._pool = FakePool(FakeConnection(responses))  # noqa: SLF001
    repo._initialized = initialized  # noqa: SLF001
    return repo


def _candidate(
    *,
    fingerprint: str = "fp",
    status: WorkflowRunRemediationStatus = WorkflowRunRemediationStatus.PENDING,
) -> WorkflowRunRemediation:
    remediation = WorkflowRunRemediation(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint=fingerprint,
        version_checksum="checksum",
        context={"workflow_source": "source"},
    )
    remediation.status = status
    return remediation


def _payload(candidate: WorkflowRunRemediation, *, as_json: bool = True) -> Any:
    payload = candidate.model_dump(mode="json")
    return json.dumps(payload) if as_json else payload


@pytest.mark.asyncio
async def test_postgres_create_candidate_inserts_and_returns_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{"rows": []}, {"rowcount": 1}])
    candidate = await repo.create_remediation_candidate(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="create-fp",
        version_checksum="checksum",
        graph_format="langgraph_script",
        context={"workflow_source": "source"},
    )

    assert candidate.fingerprint == "create-fp"
    assert candidate.status is WorkflowRunRemediationStatus.PENDING
    assert len(repo._pool.connection().queries) == 2  # noqa: SLF001
    assert (
        "INSERT INTO workflow_run_remediations" in repo._pool.connection().queries[1][0]
    )  # noqa: SLF001


@pytest.mark.asyncio
async def test_postgres_create_candidate_returns_active_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _candidate(fingerprint="dup")
    existing.claim(actor="worker")
    repo = make_repo(
        monkeypatch,
        [{"row": {"payload": _payload(existing)}}],
    )

    duplicate = await repo.create_remediation_candidate(
        workflow_id=uuid4(),
        workflow_version_id=uuid4(),
        run_id=uuid4(),
        fingerprint="dup",
        version_checksum="checksum",
        graph_format="langgraph_script",
        context={"workflow_source": "source"},
    )

    assert duplicate.id == existing.id
    assert duplicate.status is WorkflowRunRemediationStatus.CLAIMED


@pytest.mark.asyncio
async def test_postgres_claim_next_candidate_claims_and_updates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _candidate(fingerprint="claim")
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": _payload(existing)}},
            {"rowcount": 1},
        ],
    )

    claimed = await repo.claim_next_remediation_candidate(
        actor="worker",
        max_attempts=3,
    )

    assert claimed is not None
    assert claimed.status is WorkflowRunRemediationStatus.CLAIMED
    assert claimed.attempt_count == 1
    assert claimed.claimed_by == "worker"
    query, params = repo._pool.connection().queries[0]  # noqa: SLF001
    assert "attempt_count < %s" in query
    assert params == (WorkflowRunRemediationStatus.PENDING.value, 3)


@pytest.mark.asyncio
async def test_postgres_claim_next_candidate_returns_none_when_no_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{"rows": []}])

    assert await repo.claim_next_remediation_candidate(actor="worker") is None


@pytest.mark.asyncio
async def test_postgres_claim_next_candidate_returns_none_when_update_loses_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _candidate(fingerprint="race")
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": _payload(existing)}},
            {"rowcount": 0},
        ],
    )

    assert await repo.claim_next_remediation_candidate(actor="worker") is None


@pytest.mark.asyncio
async def test_postgres_get_candidate_returns_copy_and_handles_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _candidate(fingerprint="get")
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": _payload(existing)}},
            {"row": None},
        ],
    )

    fetched = await repo.get_remediation_candidate(existing.id)
    assert fetched.id == existing.id

    with pytest.raises(WorkflowRunRemediationNotFoundError):
        await repo.get_remediation_candidate(UUID(int=1))


@pytest.mark.asyncio
async def test_postgres_list_candidates_supports_filters_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _candidate(fingerprint="first")
    second = _candidate(fingerprint="second")
    repo = make_repo(
        monkeypatch,
        [{"rows": [{"payload": _payload(first)}, {"payload": _payload(second)}]}],
    )

    listed = await repo.list_remediation_candidates(
        workflow_id=first.workflow_id,
        workflow_version_id=first.workflow_version_id,
        run_id=first.run_id,
        status=WorkflowRunRemediationStatus.PENDING,
        limit=2,
    )

    assert [candidate.id for candidate in listed] == [first.id, second.id]
    query, params = repo._pool.connection().queries[0]  # noqa: SLF001
    assert "WHERE" in query
    assert "workflow_id = %s" in query
    assert "workflow_version_id = %s" in query
    assert "run_id = %s" in query
    assert "status = %s" in query
    assert "LIMIT %s" in query
    assert params == (
        str(first.workflow_id),
        str(first.workflow_version_id),
        str(first.run_id),
        WorkflowRunRemediationStatus.PENDING.value,
        2,
    )


@pytest.mark.asyncio
async def test_postgres_list_candidates_without_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_repo(monkeypatch, [{"rows": []}])

    listed = await repo.list_remediation_candidates()

    assert listed == []
    query, params = repo._pool.connection().queries[0]  # noqa: SLF001
    assert "WHERE" not in query
    assert params == ()


@pytest.mark.parametrize(
    "method_name,kwargs,expected_status",
    [
        (
            "mark_remediation_fixed",
            {
                "created_version_id": uuid4(),
                "classification": WorkflowRunRemediationClassification.WORKFLOW_FIXABLE,
                "developer_note": "fixed",
                "artifacts": {"path": "workflow.py"},
                "validation_result": {"ok": True},
            },
            WorkflowRunRemediationStatus.FIXED,
        ),
        (
            "mark_remediation_note_only",
            {
                "classification": WorkflowRunRemediationClassification.RUNTIME_OR_PLATFORM,
                "developer_note": "review later",
                "artifacts": {"summary": "note"},
            },
            WorkflowRunRemediationStatus.NOTE_ONLY,
        ),
        (
            "mark_remediation_failed",
            {
                "error": "boom",
                "artifacts": {"trace": "payload"},
                "validation_result": {"ok": False},
            },
            WorkflowRunRemediationStatus.FAILED,
        ),
        (
            "dismiss_remediation_candidate",
            {
                "actor": "reviewer",
                "reason": "manual decision",
            },
            WorkflowRunRemediationStatus.DISMISSED,
        ),
    ],
)
@pytest.mark.asyncio
async def test_postgres_update_wrappers_mutate_candidates(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    kwargs: dict[str, Any],
    expected_status: WorkflowRunRemediationStatus,
) -> None:
    candidate = _candidate(fingerprint=method_name)
    candidate.claim(actor="worker")
    repo = make_repo(
        monkeypatch,
        [
            {"row": {"payload": _payload(candidate)}},
            {"rowcount": 1},
        ],
    )

    method = getattr(repo, method_name)
    updated = await method(candidate.id, **kwargs)

    assert updated.status is expected_status
    assert updated.id == candidate.id
    assert (
        repo._pool.connection()
        .queries[1][0]
        .startswith("UPDATE workflow_run_remediations")
    )  # noqa: SLF001


def test_postgres_parse_remediation_payload_supports_string_and_mapping() -> None:
    candidate = _candidate(fingerprint="parse")
    dumped = _payload(candidate)
    parsed_from_json = PostgresWorkflowRepository._parse_remediation_payload(dumped)
    parsed_from_mapping = PostgresWorkflowRepository._parse_remediation_payload(
        candidate.model_dump(mode="json")
    )

    assert parsed_from_json.id == candidate.id
    assert parsed_from_mapping.id == candidate.id
