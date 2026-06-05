"""Startup checks for workflow trust mode configuration."""

from __future__ import annotations
import logging
from orcheo.workflow.trust.modes import WorkflowTrustMode, get_workflow_trust_mode


logger = logging.getLogger(__name__)


def check_workflow_trust_mode_on_startup() -> None:
    """Log the configured workflow trust mode on startup."""
    mode = get_workflow_trust_mode()
    if mode == WorkflowTrustMode.PRODUCTION:
        logger.info(
            "Workflow trust mode: production — declarative trusted workflows only."
        )
    else:
        logger.warning(
            "Workflow trust mode: %s — arbitrary-code workflows are permitted. "
            "This mode provides NO tenant isolation guarantee. "
            "Do not use in production multi-tenant deployments.",
            mode.value,
        )


__all__ = ["check_workflow_trust_mode_on_startup"]
