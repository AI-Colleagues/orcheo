"""Workflow trust mode configuration."""

from __future__ import annotations
import os
from enum import Enum


class WorkflowTrustMode(str, Enum):
    """Production safety mode for workflow ingestion and execution."""

    PRODUCTION = "production"
    SELF_HOST_UNSAFE = "self_host_unsafe"
    DEVELOPER = "developer"


def get_workflow_trust_mode() -> WorkflowTrustMode:
    """Return the configured workflow trust mode from environment."""
    raw = os.getenv("ORCHEO_WORKFLOW_TRUST_MODE", WorkflowTrustMode.PRODUCTION.value)
    try:
        return WorkflowTrustMode(raw)
    except ValueError:
        return WorkflowTrustMode.PRODUCTION


def is_production_trust_mode(mode: WorkflowTrustMode | None = None) -> bool:
    """Return whether the given or current mode enforces production restrictions."""
    if mode is None:
        mode = get_workflow_trust_mode()
    return mode == WorkflowTrustMode.PRODUCTION


__all__ = [
    "WorkflowTrustMode",
    "get_workflow_trust_mode",
    "is_production_trust_mode",
]
