"""Preserve user-customized ``configurable`` values across workflow re-releases.

When a new workflow version is appended to an *existing* workflow — via CLI
re-upload, candidate re-onboard, or candidate update — the incoming
``config.json`` would otherwise overwrite the ``configurable`` values a user
tuned on the workflow page. These helpers carry forward only the fields the user
actually changed, while letting the new release introduce or remove fields and
refresh the defaults of fields the user never touched.

To tell a deliberate override apart from an untouched default, each version
records the release's pristine ``configurable`` defaults under
:data:`CONFIGURABLE_DEFAULTS_KEY` in its metadata. The next release diffs the
currently installed values against those recorded defaults.
"""

from __future__ import annotations
from typing import Any
from orcheo.models import WorkflowVersion


# Version metadata key holding the pristine ``configurable`` defaults that
# shipped with the release that created the version.
CONFIGURABLE_DEFAULTS_KEY = "configurable_defaults"


def extract_configurable_defaults(
    runnable_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy of the ``configurable`` map of a resolved runnable config."""
    if not isinstance(runnable_config, dict):
        return {}
    configurable = runnable_config.get("configurable")
    return dict(configurable) if isinstance(configurable, dict) else {}


def merge_user_configurable(
    new_config: dict[str, Any] | None,
    *,
    existing_config: dict[str, Any] | None,
    previous_defaults: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Overlay user-changed ``configurable`` values onto a new release.

    A field is carried over from ``existing_config`` only when the user changed
    it — its installed value differs from the default recorded for the previous
    release (``previous_defaults``) — and the new release still declares that
    field. New or unchanged fields keep the new release's default.

    When ``previous_defaults`` is ``None`` (versions created before defaults were
    tracked), fall back to preserving every installed value for retained fields.
    """
    if not new_config:
        return new_config
    new_cfg = new_config.get("configurable")
    if not isinstance(new_cfg, dict):
        return new_config
    existing_cfg = (existing_config or {}).get("configurable")
    if not isinstance(existing_cfg, dict) or not existing_cfg:
        return new_config

    if previous_defaults is None:
        # No baseline to diff against: preserve any installed value the new
        # release still declares.
        user_values = existing_cfg
    else:
        user_values = {
            key: value
            for key, value in existing_cfg.items()
            if key not in previous_defaults or previous_defaults[key] != value
        }

    merged = {
        key: user_values[key] if key in user_values else value
        for key, value in new_cfg.items()
    }
    return {**new_config, "configurable": merged}


def apply_user_configurable_overrides(
    new_config: dict[str, Any] | None,
    existing_version: WorkflowVersion | None,
) -> dict[str, Any] | None:
    """Carry user-changed configurable values from an installed version.

    Convenience wrapper around :func:`merge_user_configurable` that reads the
    installed values and recorded defaults straight off ``existing_version``.
    """
    if existing_version is None:
        return new_config
    previous_defaults: dict[str, Any] | None = None
    metadata = existing_version.metadata
    if isinstance(metadata, dict):
        recorded = metadata.get(CONFIGURABLE_DEFAULTS_KEY)
        if isinstance(recorded, dict):
            previous_defaults = recorded
    return merge_user_configurable(
        new_config,
        existing_config=existing_version.runnable_config,
        previous_defaults=previous_defaults,
    )


__all__ = [
    "CONFIGURABLE_DEFAULTS_KEY",
    "apply_user_configurable_overrides",
    "extract_configurable_defaults",
    "merge_user_configurable",
]
