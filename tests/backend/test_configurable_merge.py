"""Tests for preserving user-customized configurable values across releases."""

from __future__ import annotations
from datetime import UTC, datetime
from uuid import uuid4
from orcheo.models import WorkflowVersion
from orcheo_backend.app.configurable_merge import (
    CONFIGURABLE_DEFAULTS_KEY,
    apply_user_configurable_overrides,
    extract_configurable_defaults,
    merge_user_configurable,
)


def _version(*, runnable_config, defaults=None) -> WorkflowVersion:
    metadata = {}
    if defaults is not None:
        metadata[CONFIGURABLE_DEFAULTS_KEY] = defaults
    return WorkflowVersion(
        id=uuid4(),
        workflow_id=uuid4(),
        version=1,
        graph={},
        metadata=metadata,
        runnable_config=runnable_config,
        created_by="tester",
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
    )


def test_extract_configurable_defaults_returns_copy() -> None:
    config = {"configurable": {"model": "gpt-4.1"}}
    defaults = extract_configurable_defaults(config)
    defaults["model"] = "mutated"
    assert config["configurable"]["model"] == "gpt-4.1"


def test_extract_handles_missing_configurable() -> None:
    assert extract_configurable_defaults(None) == {}
    assert extract_configurable_defaults({"tags": ["x"]}) == {}


def test_keeps_user_changed_field_and_refreshes_untouched_default() -> None:
    """A user override survives; an untouched field adopts the new default."""
    new_config = {"configurable": {"model": "gpt-5", "temperature": 0.7}}
    existing_config = {"configurable": {"model": "claude", "temperature": 0.2}}
    previous_defaults = {"model": "gpt-4.1", "temperature": 0.2}

    merged = merge_user_configurable(
        new_config,
        existing_config=existing_config,
        previous_defaults=previous_defaults,
    )

    # model was changed by the user -> preserved; temperature was untouched
    # (== previous default) -> takes the new release default.
    assert merged == {"configurable": {"model": "claude", "temperature": 0.7}}


def test_drops_removed_fields_and_adds_new_fields() -> None:
    new_config = {"configurable": {"model": "gpt-5", "added": 1}}
    existing_config = {"configurable": {"model": "claude", "removed": "x"}}
    previous_defaults = {"model": "gpt-4.1", "removed": "x"}

    merged = merge_user_configurable(
        new_config,
        existing_config=existing_config,
        previous_defaults=previous_defaults,
    )

    assert merged == {"configurable": {"model": "claude", "added": 1}}


def test_fallback_preserves_all_existing_when_no_defaults_recorded() -> None:
    new_config = {"configurable": {"model": "gpt-5", "temperature": 0.7}}
    existing_config = {"configurable": {"model": "claude", "temperature": 0.2}}

    merged = merge_user_configurable(
        new_config,
        existing_config=existing_config,
        previous_defaults=None,
    )

    assert merged == {"configurable": {"model": "claude", "temperature": 0.2}}


def test_noop_when_no_existing_config() -> None:
    new_config = {"configurable": {"model": "gpt-5"}}
    assert (
        merge_user_configurable(
            new_config, existing_config=None, previous_defaults=None
        )
        == new_config
    )


def test_apply_user_configurable_overrides_reads_from_version() -> None:
    existing = _version(
        runnable_config={"configurable": {"model": "claude"}},
        defaults={"model": "gpt-4.1"},
    )
    merged = apply_user_configurable_overrides(
        {"configurable": {"model": "gpt-5"}}, existing
    )
    assert merged == {"configurable": {"model": "claude"}}


def test_apply_user_configurable_overrides_no_existing_version() -> None:
    new_config = {"configurable": {"model": "gpt-5"}}
    assert apply_user_configurable_overrides(new_config, None) == new_config
