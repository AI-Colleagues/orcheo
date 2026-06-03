"""Re-exports attachment service components from the store package.

Import from this module when working in the chatkit layer; the actual
implementation lives in chatkit_store_postgres to avoid circular imports.
"""

from __future__ import annotations
from orcheo_backend.app.chatkit_store_postgres.attachment_service import (
    AttachmentNotFoundError,
    AttachmentService,
    build_attachment_scope,
    build_scoped_resolver,
)


__all__ = [
    "AttachmentNotFoundError",
    "AttachmentService",
    "build_attachment_scope",
    "build_scoped_resolver",
]
