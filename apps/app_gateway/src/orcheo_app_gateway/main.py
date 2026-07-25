"""Manifest-only static Hosted Apps gateway for local and stack deployment."""

from __future__ import annotations
import json
import os
import time
from pathlib import Path
from typing import Any
from uuid import UUID
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from orcheo.hosted_apps import (
    canonical_app_host,
    derive_client_ip,
    is_safe_app_path,
)
from orcheo.hosted_apps.errors import AliasValidationError


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
        descriptor_cache[alias] = (now + cache_seconds, descriptor)
        return descriptor

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

    async def runtime_request(
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call only the gateway-scoped backend runtime namespace."""
        if not backend_url or not gateway_secret:
            raise HTTPException(status_code=503, detail="App runtime is unavailable.")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.request(
                    method,
                    f"{backend_url}/internal/hosted-apps/runtime/{path}",
                    json=json_body,
                    params=params,
                    headers={"X-Orcheo-App-Gateway-Token": gateway_secret},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503, detail="App runtime is unavailable."
            ) from exc
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="App runtime is unavailable.")
        if response.status_code != 200:
            raise HTTPException(status_code=503, detail="App runtime is unavailable.")
        payload = response.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=503, detail="App runtime is unavailable.")
        return payload

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
        result = await runtime_request(
            "POST",
            "runs",
            json_body={
                "host": host,
                "binding": binding,
                "payload": payload,
                "idempotency_key": idempotency_key,
                "client_ip": client_ip,
            },
        )
        return Response(
            content=json.dumps(result, separators=(",", ":")),
            media_type="application/json",
            headers={
                **_SECURITY_HEADERS,
                "Cache-Control": "private, no-store",
            },
        )

    @app.get("/__orcheo/runs/{handle}", include_in_schema=False)
    async def read_runtime_run(handle: str, request: Request) -> Response:
        """Read an opaque app-run handle within the resolved host scope."""
        try:
            host, _alias = canonical_app_host(
                request.headers.get("host", ""), base_domain
            )
        except AliasValidationError as exc:
            raise HTTPException(status_code=404, detail="Unknown hosted app.") from exc
        origin = request.headers.get("origin")
        if origin not in {
            f"https://{host}",
            f"http://{request.headers.get('host', host)}",
            f"https://{request.headers.get('host', host)}",
        }:
            raise HTTPException(status_code=403, detail="App Origin is invalid.")
        result = await runtime_request("GET", f"runs/{handle}", params={"host": host})
        return Response(
            content=json.dumps(result, separators=(",", ":")),
            media_type="application/json",
            headers={
                **_SECURITY_HEADERS,
                "Cache-Control": "private, no-store",
            },
        )

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
            return Response(
                content=json.dumps(
                    {
                        "authenticated": False,
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
        if (
            not isinstance(descriptor, dict)
            or descriptor.get("visibility") == "private"
        ):
            raise HTTPException(status_code=404, detail="Hosted app is unavailable.")
        try:
            deployment_id = UUID(str(descriptor["deployment_id"]))
        except (KeyError, ValueError) as exc:
            raise HTTPException(
                status_code=503, detail="Invalid app descriptor."
            ) from exc
        deployment_root = root / "deployments" / str(deployment_id)
        try:
            manifest = json.loads((deployment_root / "__manifest__.json").read_text())
            files = manifest["files"]
        except (OSError, KeyError, json.JSONDecodeError) as exc:
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
        asset = deployment_root / requested
        if not asset.is_file():
            raise HTTPException(
                status_code=503, detail="Deployment asset is unavailable."
            )
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
        script_sources = " ".join(["'self'", *inline_hashes])
        headers["Content-Security-Policy"] = (
            "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
            "object-src 'none'; worker-src 'none'; connect-src 'self'; "
            f"script-src {script_sources}"
        )
        return FileResponse(
            asset, media_type=files[requested]["content_type"], headers=headers
        )

    return app


def run() -> None:
    """Run the dedicated gateway process from its package entry point."""
    import uvicorn

    uvicorn.run(create_app(), host="0.0.0.0", port=2030)
