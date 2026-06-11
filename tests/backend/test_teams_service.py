"""Coverage for default-team provisioning service helpers."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from orcheo.models import Team
from orcheo_backend.app import teams_service


class _Repository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def ensure_default_team(
        self, *, workspace_id: str, name: str, slug: str
    ) -> Team:
        self.calls.append((workspace_id, name, slug))
        return Team(workspace_id=workspace_id, name=name, slug=slug, is_default=True)


class _WorkspaceRepository:
    def __init__(self, record: object | None = None, error: Exception | None = None):
        self.record = record
        self.error = error

    def get_workspace(self, workspace_id):  # noqa: ANN001
        del workspace_id
        if self.error is not None:
            raise self.error
        return self.record


@pytest.mark.asyncio
async def test_ensure_default_team_uses_workspace_name_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    repository = _Repository()
    monkeypatch.setattr(
        teams_service,
        "get_workspace_repository",
        lambda: _WorkspaceRepository(SimpleNamespace(name="Acme Workspace")),
    )

    team = await teams_service.ensure_default_team(
        repository,
        SimpleNamespace(workspace_id=workspace_id, workspace_slug="acme"),
    )

    assert team.is_default is True
    assert repository.calls == [(str(workspace_id), "Acme Workspace", "acme")]


@pytest.mark.asyncio
async def test_ensure_default_team_falls_back_to_workspace_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    repository = _Repository()
    monkeypatch.setattr(
        teams_service,
        "get_workspace_repository",
        lambda: _WorkspaceRepository(error=RuntimeError("missing workspace")),
    )

    team = await teams_service.ensure_default_team(
        repository,
        SimpleNamespace(workspace_id=workspace_id, workspace_slug=None),
    )

    assert team.slug == str(workspace_id)
    assert repository.calls == [
        (str(workspace_id), str(workspace_id), str(workspace_id))
    ]
