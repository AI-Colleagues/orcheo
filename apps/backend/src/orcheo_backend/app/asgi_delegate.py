"""Hand a request from a FastAPI route to another ASGI app."""

from __future__ import annotations
from fastapi import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class ASGIDelegateResponse(Response):
    """Pass the raw ASGI exchange to ``app``.

    Returned by a route that has done its own checks (authentication, feature
    flags) and lets an embedded app, such as FastMCP's HTTP app or its OAuth
    routes, read the request body and send the response itself. The route must
    not declare body parameters, so the body is still unread.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Remember the app that will answer the request."""
        super().__init__()
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward the exchange unchanged."""
        await self._app(scope, receive, send)


__all__ = ["ASGIDelegateResponse"]
