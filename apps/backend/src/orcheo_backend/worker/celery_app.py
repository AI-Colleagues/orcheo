"""Celery application configuration for the Orcheo execution worker."""

from __future__ import annotations
import os
from celery import Celery


# Configuration from environment
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CRON_DISPATCH_INTERVAL = float(os.getenv("CRON_DISPATCH_INTERVAL", "60"))
WORKFLOW_AUTOFIX_SCAN_INTERVAL = float(
    os.getenv("ORCHEO_WORKFLOW_AUTOFIX_SCAN_INTERVAL_SECONDS", "60")
)
CELERY_BEAT_SCHEDULE_FILE = os.getenv(
    "CELERY_BEAT_SCHEDULE_FILE", "celerybeat-schedule"
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
        "schedule": CRON_DISPATCH_INTERVAL,
    },
    "scan-workflow-remediations": {
        "task": "orcheo_backend.worker.tasks.scan_workflow_remediations",
        "schedule": WORKFLOW_AUTOFIX_SCAN_INTERVAL,
    },
}
celery_app.conf.beat_schedule_filename = CELERY_BEAT_SCHEDULE_FILE

__all__ = ["celery_app"]
