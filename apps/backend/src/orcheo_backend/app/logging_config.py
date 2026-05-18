"""Centralised logging configuration for the Orcheo backend service."""

from __future__ import annotations
import logging
import os
import structlog


def configure_logging() -> None:
    """Configure module and framework loggers based on environment variables."""
    log_level = os.getenv("ORCHEO_LOG_LEVEL", "INFO").upper()
    log_format = os.getenv("ORCHEO_LOG_FORMAT", "console").lower()
    resolved_level = getattr(logging, log_level, logging.INFO)

    shared_processors: list[structlog.types.Processor] = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.EventRenamer("message"),
        structlog.stdlib.ExtraAdder(),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.StackInfoRenderer(),
    ]

    if log_format == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(
            exception_formatter=structlog.dev.plain_traceback,
        )
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    for existing_handler in list(root_logger.handlers):
        root_logger.removeHandler(existing_handler)
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.WARNING)

    for name in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "fastapi",
        "orcheo",
        "orcheo_backend",
    ):
        logging.getLogger(name).setLevel(resolved_level)


configure_logging()


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger instance after ensuring configuration is applied."""
    return logging.getLogger(name or "orcheo_backend.app")


__all__ = ["configure_logging", "get_logger"]
