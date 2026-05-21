"""Celery application configuration for the Orcheo execution worker."""

from __future__ import annotations
import logging
import os
from typing import Any
from celery import Celery
from celery.signals import worker_process_init


logger = logging.getLogger(__name__)


# Configuration from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ORCHEO_CRON_DISPATCH_INTERVAL = float(os.getenv("ORCHEO_CRON_DISPATCH_INTERVAL", "60"))
WORKFLOW_AUTOFIX_SCAN_INTERVAL = float(
    os.getenv("ORCHEO_WORKFLOW_AUTOFIX_SCAN_INTERVAL_SECONDS", "60")
)
ORCHEO_CELERY_BEAT_SCHEDULE_FILE = os.getenv(
    "ORCHEO_CELERY_BEAT_SCHEDULE_FILE", "celerybeat-schedule"
)

celery_app = Celery(
    "orcheo-backend",
    broker=REDIS_URL,
    backend=None,  # No result backend needed for fire-and-forget
    include=["orcheo_backend.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,  # Acknowledge after execution completes
    worker_prefetch_multiplier=1,  # Fetch one task at a time for fairness
)

# Celery Beat schedule for cron dispatch
celery_app.conf.beat_schedule = {
    "dispatch-cron-triggers": {
        "task": "orcheo_backend.worker.tasks.dispatch_cron_triggers",
        "schedule": ORCHEO_CRON_DISPATCH_INTERVAL,
    },
    "scan-workflow-remediations": {
        "task": "orcheo_backend.worker.tasks.scan_workflow_remediations",
        "schedule": WORKFLOW_AUTOFIX_SCAN_INTERVAL,
    },
}
celery_app.conf.beat_schedule_filename = ORCHEO_CELERY_BEAT_SCHEDULE_FILE


@worker_process_init.connect
def _configure_sandbox_for_worker(**_: Any) -> None:
    """Bind the shared sandbox bootstrap inside every worker process.

    Celery forks worker processes after import; the shared
    ``WorkflowSandboxDispatcher`` / ``SandboxedProcessLauncher`` cache lives
    inside ``orcheo_backend.app.sandbox`` and must be bootstrapped per
    process so workflow execution paths can dispatch sandboxed runs and
    bind the launcher for vibe-agent subprocesses.
    """
    from orcheo_backend.app.sandbox import ensure_sandbox_configured

    try:
        ensure_sandbox_configured()
    except Exception:
        logger.exception("Failed to configure sandbox runtime for worker process")
        raise


__all__ = ["celery_app"]
