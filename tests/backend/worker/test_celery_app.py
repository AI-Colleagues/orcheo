"""Tests for the Celery app configuration."""

from __future__ import annotations
import importlib


def test_celery_app_imports_without_sandbox_hook() -> None:
    """The worker app should not expose the removed sandbox init hook."""
    module = importlib.import_module("orcheo_backend.worker.celery_app")

    assert hasattr(module, "celery_app")
    assert not hasattr(module, "_configure_sandbox_for_worker")
    assert "dispatch-cron-triggers" in module.celery_app.conf.beat_schedule
    assert "scan-workflow-remediations" not in module.celery_app.conf.beat_schedule
