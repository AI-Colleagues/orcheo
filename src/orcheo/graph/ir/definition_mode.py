"""Definition-mode toggle for workflow ingestion and execution.

``ORCHEO_WORKFLOW_DEFINITION_MODE`` selects between:

* ``restricted`` — uploads are compiled to the frozen IR (no author code runs at
  ingestion) and executed from the IR with ``CodeNode`` bodies sandboxed.
* ``unrestricted`` — today's in-process ``load_graph_from_script`` path, intended
  for local/self-hosted development and providing **no tenant isolation**.

The default at this stage is ``unrestricted``; flipping the default to
``restricted`` is a separate follow-up task. The active mode is logged once per
process the first time ingestion or execution runs.
"""

from __future__ import annotations
import logging
from orcheo.config.loader import get_settings


logger = logging.getLogger(__name__)

RESTRICTED = "restricted"
UNRESTRICTED = "unrestricted"

_log_state = {"logged": False}


def get_definition_mode() -> str:
    """Return the active workflow definition mode."""
    return str(get_settings().get("WORKFLOW_DEFINITION_MODE", UNRESTRICTED))


def is_restricted_mode() -> bool:
    """Return ``True`` when ingestion/execution must enforce the IR + sandbox."""
    return get_definition_mode() == RESTRICTED


def log_active_definition_mode(*, force: bool = False) -> None:
    """Log the active definition mode once per process (idempotent).

    Args:
        force: Log even if it was already logged (e.g. an explicit startup call).
    """
    if _log_state["logged"] and not force:
        return
    _log_state["logged"] = True
    if is_restricted_mode():
        logger.info(  # pragma: no cover
            "Workflow definition mode: restricted — uploads compile to a frozen IR "
            "and CodeNode bodies run in the MicroPython-WASM sandbox"
        )
    else:
        logger.warning(
            "Workflow definition mode: unrestricted — uploaded workflow.py executes "
            "in-process with full builtins and NO tenant isolation; not tenant-safe"
        )


__all__ = [
    "RESTRICTED",
    "UNRESTRICTED",
    "get_definition_mode",
    "is_restricted_mode",
    "log_active_definition_mode",
]
