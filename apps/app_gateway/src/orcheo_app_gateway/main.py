"""Manifest-only static Hosted Apps gateway for local and stack deployment."""

from __future__ import annotations
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode
from uuid import UUID
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from orcheo.hosted_apps.errors import AliasValidationError
from orcheo.hosted_apps.gateway import (
    canonical_app_host,
    derive_client_ip,
    is_safe_app_path,
)


_SECURITY_HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": (
        "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_APP_SESSION_COOKIE = "__Host-orcheo-app-session"
_APP_AUTH_COOKIE = "__Host-orcheo-app-auth"
_APP_VISITOR_COOKIE = "__Host-orcheo-app-visitor"
_APP_VISITOR_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def _load_descriptors(path: Path) -> dict[str, dict[str, Any]]:
    """Load a local development descriptor map without trusting app requests."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def create_app() -> FastAPI:  # noqa: C901, PLR0915
    """Create a separately runnable, fail-closed static app gateway."""
    base_domain = os.getenv("ORCHEO_APPS_BASE_DOMAIN", "apps.localhost")
    root = Path(os.getenv("ORCHEO_APP_BUNDLE_FILESYSTEM_ROOT", "/data/app-bundles"))
    descriptor_file = Path(
        os.getenv("ORCHEO_APP_GATEWAY_DESCRIPTOR_FILE", "/data/app-descriptors.json")
    )
    backend_url = os.getenv("ORCHEO_APP_GATEWAY_BACKEND_URL", "").rstrip("/")
    gateway_secret = os.getenv("ORCHEO_APP_GATEWAY_SECRET", "")
    studio_url = os.getenv("ORCHEO_STUDIO_URL", "http://localhost:2026").rstrip("/")
    cache_seconds = min(
        max(int(os.getenv("ORCHEO_APP_DESCRIPTOR_CACHE_SECONDS", "30")), 1), 60
    )
    descriptor_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
    trusted_proxy_cidrs = tuple(
        value.strip()
        for value in os.getenv("ORCHEO_APP_TRUSTED_PROXY_CIDRS", "").split(",")
        if value.strip()
    )
    trusted_proxy_hops = int(os.getenv("ORCHEO_APP_TRUSTED_PROXY_HOPS", "0"))
    app = FastAPI(title="Orcheo App Gateway", docs_url=None, redoc_url=None)

    async def resolve_descriptor(host: str, alias: str) -> dict[str, Any] | None:
        """Resolve through the dedicated backend lane with bounded local caching."""
        cached = descriptor_cache.get(alias)
        now = time.monotonic()
        if cached is not None and cached[0] > now:
            return cached[1]
        descriptor: dict[str, Any] | None
        if backend_url and not gateway_secret:
            raise HTTPException(
                status_code=503, detail="App resolution is unavailable."
            )
        if backend_url:
            try:
                async with httpx.AsyncClient(timeout=3.0) as client:
                    response = await client.get(
                        f"{backend_url}/internal/hosted-apps/resolve",
                        params={"host": host},
                        headers={"X-Orcheo-App-Gateway-Token": gateway_secret},
                    )
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=503, detail="App resolution is unavailable."
                ) from exc
            if response.status_code == 404:
                descriptor = None
            elif response.status_code != 200:
                raise HTTPException(
                    status_code=503, detail="App resolution is unavailable."
                )
            else:
                parsed = response.json()
                descriptor = parsed if isinstance(parsed, dict) else None
        else:
            local = _load_descriptors(descriptor_file).get(alias)
            descriptor = local if isinstance(local, dict) else None
        # Backend resolution includes suspension and moderation state. Do not retain
        # those decisions in the gateway process, so an operator action takes effect
        # on the next request. Local descriptor files are immutable test/dev input
        # and can still use the bounded cache.
        if not backend_url:
            descriptor_cache[alias] = (now + cache_seconds, descriptor)
        return descriptor

    async def read_deployment_object(deployment_id: UUID, path: str) -> bytes:
        """Read a private deployment object through the backend or local fallback."""
        if backend_url:
            encoded_path = quote(path, safe="/")
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{backend_url}/internal/hosted-apps/deployments/"
                        f"{deployment_id}/assets/{encoded_path}",
                        headers={"X-Orcheo-App-Gateway-Token": gateway_secret},
                    )
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=503, detail="Deployment asset is unavailable."
                ) from exc
            if response.status_code == 404:
                raise FileNotFoundError(path)
            if response.status_code != 200:
                raise HTTPException(
                    status_code=503, detail="Deployment asset is unavailable."
                )
            return response.content
        asset = root / "deployments" / str(deployment_id) / path
        try:
            return asset.read_bytes()
        except OSError as exc:
            raise FileNotFoundError(path) from exc

    async def open_deployment_object_stream(
        deployment_id: UUID, path: str
    ) -> tuple[httpx.AsyncClient, httpx.Response]:
        """Open a backend asset response without buffering its body."""
        encoded_path = quote(path, safe="/")
        client = httpx.AsyncClient(timeout=10.0)
        try:
            request = client.build_request(
                "GET",
                f"{backend_url}/internal/hosted-apps/deployments/"
                f"{deployment_id}/assets/{encoded_path}",
                headers={"X-Orcheo-App-Gateway-Token": gateway_secret},
            )
            response = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            raise HTTPException(
                status_code=503, detail="Deployment asset is unavailable."
            ) from exc
        if response.status_code == 200:
            return client, response
        await response.aclose()
        await client.aclose()
        if response.status_code == 404:
            raise FileNotFoundError(path)
        raise HTTPException(status_code=503, detail="Deployment asset is unavailable.")

    async def stream_deployment_object(
        client: httpx.AsyncClient, response: httpx.Response
    ) -> AsyncIterator[bytes]:
        """Forward bounded upstream chunks and release the connection afterward."""
        try:
            async for chunk in response.aiter_raw():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    def validate_browser_mutation(request: Request, host: str) -> None:
        """Enforce the browser-origin boundary before forwarding state changes."""
        forbidden = {
            "authorization",
            "x-orcheo-workspace",
            "x-orcheo-actor",
            "x-orcheo-app-gateway-token",
            "x-orcheo-service-token",
            "forwarded",
        }
        if any(name in request.headers for name in forbidden):
            raise HTTPException(status_code=400, detail="Forbidden browser header.")
        content_type = request.headers.get("content-type", "").lower()
        if content_type != "application/json":
            raise HTTPException(
                status_code=415, detail="JSON content type is required."
            )
        origin = request.headers.get("origin")
        expected = {f"https://{host}"}
        if base_domain.endswith(".localhost") or base_domain == "localhost":
            raw_host = request.headers.get("host", host)
            expected.update({f"http://{raw_host}", f"https://{raw_host}"})
        if origin not in expected:
            raise HTTPException(status_code=403, detail="App Origin is invalid.")
        if request.headers.get("sec-fetch-site") != "same-origin":
            raise HTTPException(status_code=403, detail="Fetch Metadata is invalid.")

    def validate_browser_read(request: Request, host: str) -> None:
        """Allow same-origin reads when browsers omit Origin on safe GET requests."""
        raw_host = request.headers.get("host", host)
        expected = {
            f"https://{host}",
            f"http://{raw_host}",
            f"https://{raw_host}",
        }
        origin = request.headers.get("origin")
        if origin is not None and origin not in expected:
            raise HTTPException(status_code=403, detail="App Origin is invalid.")
        if request.headers.get("sec-fetch-site") != "same-origin":
            raise HTTPException(status_code=403, detail="Fetch Metadata is invalid.")

    async def internal_request(
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        session_secret: str | None = None,
    ) -> dict[str, Any]:
        """Call only the dedicated gateway-scoped backend namespace."""
        if not backend_url or not gateway_secret:
            raise HTTPException(status_code=503, detail="App runtime is unavailable.")
        headers = {"X-Orcheo-App-Gateway-Token": gateway_secret}
        if session_secret:
            headers["X-Orcheo-App-Session"] = session_secret
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.request(
                    method,
                    f"{backend_url}/internal/hosted-apps/{path}",
                    json=json_body,
                    params=params,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503, detail="App runtime is unavailable."
            ) from exc
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="App runtime is unavailable.")
        if response.status_code in {401, 409, 429}:
            raise HTTPException(
                status_code=response.status_code,
                detail=response.json().get(
                    "detail", "App runtime request was rejected."
                ),
            )
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="App runtime is unavailable.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=503, detail="App runtime is unavailable.")
        return payload

    def encode_auth_transaction(payload: dict[str, Any]) -> str:
        """Sign a short-lived HttpOnly transaction without server-side state."""
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            )
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(
            gateway_secret.encode(), encoded.encode(), hashlib.sha256
        ).hexdigest()
        return f"{encoded}.{signature}"

    def decode_auth_transaction(value: str | None) -> dict[str, Any]:
        """Verify and decode one gateway-created auth transaction."""
        if not value or not gateway_secret:
            raise HTTPException(status_code=400, detail="App login has expired.")
        try:
            encoded, signature = value.rsplit(".", 1)
            expected = hmac.new(
                gateway_secret.encode(), encoded.encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature")
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(encoded + padding))
            if (
                not isinstance(payload, dict)
                or float(payload["expires_at"]) <= time.time()
            ):
                raise ValueError("expiry")
            return payload
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(
                status_code=400, detail="App login has expired."
            ) from exc

    def resolve_anonymous_visitor(
        request: Request, host: str
    ) -> tuple[str, str | None]:
        """Return a gateway-authenticated visitor id and any replacement cookie."""
        if not gateway_secret:
            raise HTTPException(status_code=503, detail="App runtime is unavailable.")
        cookie = request.cookies.get(_APP_VISITOR_COOKIE)
        raw_id: str | None = None
        if cookie and len(cookie) <= 128:
            try:
                candidate, signature = cookie.rsplit(".", 1)
                expected = hmac.new(
                    gateway_secret.encode(),
                    f"{host}:{candidate}".encode(),
                    hashlib.sha256,
                ).hexdigest()
                if (
                    len(candidate) >= 32
                    and len(signature) == 64
                    and hmac.compare_digest(signature, expected)
                ):
                    raw_id = candidate
            except ValueError:
                pass
        replacement: str | None = None
        if raw_id is None:
            raw_id = secrets.token_urlsafe(32)
            signature = hmac.new(
                gateway_secret.encode(),
                f"{host}:{raw_id}".encode(),
                hashlib.sha256,
            ).hexdigest()
            replacement = f"{raw_id}.{signature}"
        visitor_id = hashlib.sha256(f"{host}:{raw_id}".encode()).hexdigest()
        return visitor_id, replacement

    def set_anonymous_visitor_cookie(response: Response, value: str | None) -> None:
        """Persist an exact-host opaque identity without exposing it to app code."""
        if value is None:
            return
        response.set_cookie(
            _APP_VISITOR_COOKIE,
            value,
            max_age=_APP_VISITOR_COOKIE_MAX_AGE,
            httponly=True,
            secure=True,
            samesite="strict",
            path="/",
        )

    async def session_authenticated(request: Request, host: str) -> bool:
        """Introspect the exact-host HttpOnly app session."""
        secret = request.cookies.get(_APP_SESSION_COOKIE)
        if not secret:
            return False
        try:
            result = await internal_request(
                "GET",
                "auth/session",
                params={"host": host},
                session_secret=secret,
            )
        except HTTPException:
            return False
        return result.get("authenticated") is True

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Return liveness without consulting a browser-controlled host."""
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz() -> dict[str, str]:
        """Verify the configured local bundle root is usable before app traffic."""
        if backend_url and not gateway_secret:
            raise HTTPException(
                status_code=503, detail="Gateway authentication is unavailable."
            )
        if not root.is_dir():
            raise HTTPException(
                status_code=503, detail="Bundle storage is unavailable."
            )
        return {"status": "ready"}

    @app.post(
        "/__orcheo/workflows/{binding}/runs",
        include_in_schema=False,
    )
    async def create_runtime_run(
        binding: str,
        request: Request,
    ) -> Response:
        """Accept an app binding invocation through the isolated runtime lane."""
        try:
            host, _alias = canonical_app_host(
                request.headers.get("host", ""), base_domain
            )
        except AliasValidationError as exc:
            raise HTTPException(status_code=404, detail="Unknown hosted app.") from exc
        validate_browser_mutation(request, host)
        idempotency_key = request.headers.get("idempotency-key")
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key is required.")
        payload = await request.json()
        peer_ip = request.client.host if request.client else "127.0.0.1"
        try:
            client_ip = derive_client_ip(
                peer_ip,
                request.headers.get("x-forwarded-for"),
                trusted_proxy_cidrs=trusted_proxy_cidrs,
                trusted_hops=trusted_proxy_hops,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="Client IP forwarding is invalid."
            ) from exc
        anonymous_visitor_id, visitor_cookie = resolve_anonymous_visitor(request, host)
        result = await internal_request(
            "POST",
            "runtime/runs",
            json_body={
                "host": host,
                "binding": binding,
                "payload": payload,
                "idempotency_key": idempotency_key,
                "client_ip": client_ip,
                "anonymous_visitor_id": anonymous_visitor_id,
            },
            session_secret=request.cookies.get(_APP_SESSION_COOKIE),
        )
        response = Response(
            content=json.dumps(result, separators=(",", ":")),
            media_type="application/json",
            headers={
                **_SECURITY_HEADERS,
                "Cache-Control": "private, no-store",
            },
        )
        set_anonymous_visitor_cookie(response, visitor_cookie)
        return response

    @app.get("/__orcheo/runs/{handle}", include_in_schema=False)
    async def read_runtime_run(handle: str, request: Request) -> Response:
        """Read an opaque app-run handle within the resolved host scope."""
        try:
            host, _alias = canonical_app_host(
                request.headers.get("host", ""), base_domain
            )
        except AliasValidationError as exc:
            raise HTTPException(status_code=404, detail="Unknown hosted app.") from exc
        validate_browser_read(request, host)
        result = await internal_request(
            "GET",
            f"runtime/runs/{handle}",
            params={"host": host},
            session_secret=request.cookies.get(_APP_SESSION_COOKIE),
        )
        return Response(
            content=json.dumps(result, separators=(",", ":")),
            media_type="application/json",
            headers={
                **_SECURITY_HEADERS,
                "Cache-Control": "private, no-store",
            },
        )

    @app.get("/__orcheo/auth/start", include_in_schema=False)
    async def start_app_auth(request: Request, return_to: str = "/") -> Response:
        """Start central member authorization with a gateway-owned PKCE verifier."""
        request_host = request.headers.get("host", "")
        try:
            host, alias = canonical_app_host(request_host, base_domain)
        except AliasValidationError as exc:
            raise HTTPException(status_code=404, detail="Unknown hosted app.") from exc
        if not backend_url or not gateway_secret:
            raise HTTPException(status_code=503, detail="App login is unavailable.")
        descriptor = await resolve_descriptor(host, alias)
        if not isinstance(descriptor, dict) or descriptor.get("state") == "suspended":
            raise HTTPException(status_code=404, detail="Hosted app is unavailable.")
        if not return_to.startswith("/") or return_to.startswith("//"):
            raise HTTPException(status_code=400, detail="Invalid app return path.")
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        state = secrets.token_urlsafe(32)
        scheme = "http" if host.endswith(".localhost") else "https"
        callback_host = host
        if host.endswith(".localhost"):
            raw_host = request_host.strip().lower()
            if raw_host.count(":") == 1:
                _name, port = raw_host.rsplit(":", 1)
                if port.isdigit():
                    callback_host = f"{host}:{port}"
        redirect_uri = f"{scheme}://{callback_host}/__orcheo/auth/callback"
        transaction = encode_auth_transaction(
            {
                "host": host,
                "verifier": verifier,
                "state": state,
                "redirect_uri": redirect_uri,
                "return_to": return_to,
                "expires_at": time.time() + 300,
            }
        )
        authorize_query = urlencode(
            {
                "host": host,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "state": state,
            }
        )
        destination = f"{studio_url}/apps/authorize?{authorize_query}"
        response = RedirectResponse(destination, status_code=302)
        response.set_cookie(
            _APP_AUTH_COOKIE,
            transaction,
            max_age=300,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        return response

    @app.get("/__orcheo/auth/callback", include_in_schema=False)
    async def finish_app_auth(
        request: Request,
        code: str,
        state: str,
    ) -> Response:
        """Exchange a central code and issue the exact-host app session cookie."""
        transaction = decode_auth_transaction(request.cookies.get(_APP_AUTH_COOKIE))
        try:
            host, _alias = canonical_app_host(
                request.headers.get("host", ""), base_domain
            )
        except AliasValidationError as exc:
            raise HTTPException(status_code=404, detail="Unknown hosted app.") from exc
        if transaction.get("host") != host or not hmac.compare_digest(
            str(transaction.get("state", "")), state
        ):
            raise HTTPException(status_code=400, detail="App login state is invalid.")
        result = await internal_request(
            "POST",
            "auth/exchange",
            json_body={
                "host": host,
                "code": code,
                "verifier": transaction["verifier"],
                "redirect_uri": transaction["redirect_uri"],
            },
        )
        session_secret = result.get("session_secret")
        if not isinstance(session_secret, str):
            raise HTTPException(status_code=503, detail="App login is unavailable.")
        response = RedirectResponse(str(transaction["return_to"]), status_code=302)
        response.set_cookie(
            _APP_SESSION_COOKIE,
            session_secret,
            max_age=43_200,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
        )
        response.delete_cookie(_APP_AUTH_COOKIE, path="/", secure=True, httponly=True)
        return response

    @app.get("/{asset_path:path}", include_in_schema=False)
    async def serve_asset(  # noqa: C901, PLR0912
        request: Request, asset_path: str
    ) -> Response:
        """Resolve exact host then serve only a validator-created manifest asset."""
        try:
            _host, alias = canonical_app_host(
                request.headers.get("host", ""), base_domain
            )
        except AliasValidationError as exc:
            raise HTTPException(status_code=404, detail="Unknown hosted app.") from exc
        descriptor = await resolve_descriptor(_host, alias)
        if asset_path == "__orcheo/config":
            if not isinstance(descriptor, dict):
                raise HTTPException(
                    status_code=404, detail="Hosted app is unavailable."
                )
            authenticated = await session_authenticated(request, _host)
            return Response(
                content=json.dumps(
                    {
                        "authenticated": authenticated,
                        "visibility": descriptor.get("visibility", "public"),
                    },
                    separators=(",", ":"),
                ),
                media_type="application/json",
                headers={
                    **_SECURITY_HEADERS,
                    "Cache-Control": "private, no-store",
                },
            )
        if asset_path == "__orcheo" or asset_path.startswith("__orcheo/"):
            raise HTTPException(status_code=404, detail="Reserved runtime path.")
        if isinstance(descriptor, dict) and descriptor.get("state") == "suspended":
            return HTMLResponse(
                "<!doctype html><title>App unavailable</title>"
                "<h1>This app is unavailable</h1>"
                '<p><a href="https://orcheo.cloud/abuse">Report abuse</a></p>',
                status_code=451,
                headers={
                    **_SECURITY_HEADERS,
                    "Cache-Control": "private, no-store",
                    "Content-Security-Policy": (
                        "default-src 'none'; style-src 'unsafe-inline'; "
                        "frame-ancestors 'none'; base-uri 'none'"
                    ),
                },
            )
        if not isinstance(descriptor, dict):
            raise HTTPException(status_code=404, detail="Hosted app is unavailable.")
        private_requires_login = descriptor.get("visibility") == "private"
        if private_requires_login and not await session_authenticated(request, _host):
            return RedirectResponse(
                f"/__orcheo/auth/start?{urlencode({'return_to': request.url.path})}",
                status_code=302,
                headers={**_SECURITY_HEADERS, "Cache-Control": "private, no-store"},
            )
        try:
            deployment_id = UUID(str(descriptor["deployment_id"]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Invalid app descriptor."
            ) from exc
        try:
            manifest = json.loads(
                (
                    await read_deployment_object(deployment_id, "__manifest__.json")
                ).decode()
            )
            files = manifest["files"]
        except (
            FileNotFoundError,
            KeyError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise HTTPException(
                status_code=503, detail="Deployment manifest is unavailable."
            ) from exc
        requested = asset_path or "index.html"
        if not is_safe_app_path(f"/{requested}"):
            raise HTTPException(status_code=400, detail="Unsafe app asset path.")
        if requested not in files:
            requested = "index.html"
        if requested not in files:
            raise HTTPException(status_code=404, detail="Asset was not found.")
        headers = dict(_SECURITY_HEADERS)
        headers["Cache-Control"] = "no-cache"
        digest = files[requested].get("sha256")
        if isinstance(digest, str):
            headers["ETag"] = f'"{digest}"'
        inline_hashes = (
            manifest.get("html_policy", {})
            .get(requested, {})
            .get("inline_script_hashes", [])
        )
        quoted_hashes = [
            f"'{value}'"
            for value in inline_hashes
            if isinstance(value, str) and value.startswith("sha256-")
        ]
        script_sources = " ".join(["'self'", *quoted_hashes])
        headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "object-src 'none'; worker-src 'none'; connect-src 'self'; "
            f"script-src {script_sources}"
        )
        if backend_url:
            try:
                (
                    upstream_client,
                    upstream_response,
                ) = await open_deployment_object_stream(deployment_id, requested)
            except FileNotFoundError:
                raise HTTPException(
                    status_code=503, detail="Deployment asset is unavailable."
                ) from None
            return StreamingResponse(
                stream_deployment_object(upstream_client, upstream_response),
                media_type=files[requested]["content_type"],
                headers=headers,
            )
        asset = root / "deployments" / str(deployment_id) / requested
        if not asset.is_file():
            raise HTTPException(
                status_code=503, detail="Deployment asset is unavailable."
            )
        return FileResponse(
            asset,
            media_type=files[requested]["content_type"],
            headers=headers,
        )

    return app


def run() -> None:
    """Run the dedicated gateway process from its package entry point."""
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=2030)
