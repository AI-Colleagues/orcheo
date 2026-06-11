"""Coverage for the team model and slug normalization helpers."""

from __future__ import annotations

import pytest

from orcheo.models import Team, normalize_team_slug


def test_normalize_team_slug_trims_and_lowercases() -> None:
    assert normalize_team_slug("  Sales-Team  ") == "sales-team"


@pytest.mark.parametrize(
    ("value", "message"),
    [
        ("   ", "Team slug must not be empty."),
        (
            "sales/team",
            "Team slug must contain only alphanumeric characters, hyphens, or underscores.",
        ),
    ],
)
def test_normalize_team_slug_rejects_invalid_values(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        normalize_team_slug(value)


def test_team_model_coerces_slug_and_name() -> None:
    team = Team(
        workspace_id="workspace-1",
        name="  Sales Team  ",
        slug="  SALES-Team  ",
        is_default=True,
    )

    assert team.name == "Sales Team"
    assert team.slug == "sales-team"
    assert team.is_default is True


def test_team_model_rejects_empty_name() -> None:
    with pytest.raises(ValueError, match="Team name must not be empty."):
        Team(workspace_id="workspace-1", name="   ", slug="sales")
