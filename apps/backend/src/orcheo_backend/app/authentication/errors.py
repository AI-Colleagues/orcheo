from __future__ import annotations
from collections.abc import Mapping
from dataclasses import dataclass
from fastapi import HTTPException, status


@dataclass(eq=False)
class AuthenticationError(Exception):
    """Domain-specific error describing why authentication failed."""

    message: str
    code: str = "auth.invalid_token"
    status_code: int = status.HTTP_401_UNAUTHORIZED
    headers: Mapping[str, str] | None = None
    websocket_code: int = 4401

    def as_http_exception(self) -> HTTPException:
        """Translate the authentication error to an HTTPException."""
        headers = {"WWW-Authenticate": "Bearer"}
        if self.headers:
            headers.update(self.headers)
        detail = {"message": self.message, "code": self.code}
        return HTTPException(
            status_code=self.status_code,
            detail=detail,
            headers=headers,
        )


class AuthorizationError(AuthenticationError):
    """Raised when an authenticated identity lacks required permissions."""

    def __init__(
        self,
        message: str,
        code: str = "auth.forbidden",
        status_code: int = status.HTTP_403_FORBIDDEN,
        headers: Mapping[str, str] | None = None,
        websocket_code: int = 4403,
    ) -> None:
        """Initialize authorization failures with forbidden defaults."""
        super().__init__(
            message=message,
            code=code,
            status_code=status_code,
            headers=headers,
            websocket_code=websocket_code,
        )
