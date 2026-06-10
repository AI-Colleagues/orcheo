"""Browser context bridge for exposing Studio context to coding agents.

Agents interact with workflows via existing CLI commands — no additional tool
definitions are needed:

- ``orcheo context`` — active Studio page and workflow.
- ``orcheo context sessions`` — all active Studio sessions.
- ``orcheo workflow show <id>`` — fetch workflow details and script.
- ``orcheo workflow download <id>`` — download workflow script to a file.
- ``orcheo workflow upload --id <id> <file>`` — upload an updated script.
- ``orcheo workflow upload <file>`` — create a new workflow from a script.
- ``orcheo workflow list`` — list all workflows.
"""

from __future__ import annotations
from orcheo_sdk.cli.browser_context.store import (
    BrowserContextEntry,
    BrowserContextStore,
)


__all__ = [
    "BrowserContextEntry",
    "BrowserContextStore",
]
