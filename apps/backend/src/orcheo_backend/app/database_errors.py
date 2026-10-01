"""Sanitized HTTP responses for unavailable PostgreSQL operations."""

from __future__ import annotations
import logging
from fastapi import Request
from fastapi.responses import JSONResponse
from psycopg import InterfaceError, OperationalError
from psycopg_pool import PoolTimeout, TooManyRequests


DATABASE_UNAVAILABLE_ERRORS = (
    OperationalError,
    InterfaceError,
    PoolTimeout,
    TooManyRequests,
)
logger = logging.getLogger(__name__)


def database_unavailable_detail() -> dict[str, str]:
    """Return the shared public error for database outages."""
    return {
        "code": "database.unavailable",
        "message": "Service temporarily unavailable. Please try again later.",
    }


async def database_unavailable_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handle database outages, including failures in dependency initialization."""
    logger.warning(
        "Database operation unavailable at %s", request.url.path, exc_info=exc
    )
    return JSONResponse(
        status_code=503,
        content={"detail": database_unavailable_detail()},
    )
