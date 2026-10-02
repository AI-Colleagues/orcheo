"""Precompressed Studio delivery and cache negotiation regression tests."""

from __future__ import annotations
import gzip
from pathlib import Path
import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from orcheo.studio_assets import compress_assets
from orcheo_backend.app.factory import _StudioStaticFiles


@pytest.mark.parametrize(
    ("accept_encoding", "compressed"),
    [
        ("gzip", True),
        ("*", True),
        ("br", False),
        ("gzip;q=0, *;q=1", False),
        ("gzip;q=invalid", False),
    ],
)
@pytest.mark.asyncio
async def test_studio_negotiates_gzip_and_revalidates_runtime_assets(
    tmp_path: Path, accept_encoding: str, compressed: bool
) -> None:
    """Clients get equivalent JS and encoding-specific validators after rendering."""
    asset = tmp_path / "app-hash.js"
    content = "const domain = 'configured.example';\n" * 100
    asset.write_text(content)
    (tmp_path / "index.html").write_text("<html>Studio</html>")
    compress_assets(tmp_path)
    compressed_path = tmp_path / "app-hash.js.gz"
    assert gzip.decompress(compressed_path.read_bytes()).decode() == content
    original_gzip = compressed_path.read_bytes()
    compress_assets(tmp_path)
    assert compressed_path.read_bytes() == original_gzip
    app = _StudioStaticFiles(directory=tmp_path, html=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio"
    ) as client:
        headers = {"Accept-Encoding": accept_encoding}
        response = await client.get("/app-hash.js", headers=headers)
        assert response.status_code == 200
        assert response.text == content
        assert (response.headers.get("Content-Encoding") == "gzip") is compressed
        assert response.headers["Content-Type"].split(";")[0] in {
            "text/javascript",
            "application/javascript",
        }
        assert response.headers["Vary"] == "Accept-Encoding"
        assert "must-revalidate" in response.headers["Cache-Control"]
        cached = await client.get(
            "/app-hash.js",
            headers={**headers, "If-None-Match": response.headers["ETag"]},
        )
        assert cached.status_code == 304
        assert "must-revalidate" in cached.headers["Cache-Control"]


@pytest.mark.asyncio
async def test_studio_compression_preserves_spa_fallback_and_api_404(
    tmp_path: Path,
) -> None:
    """Missing compressed assets still follow the existing route boundaries."""
    (tmp_path / "index.html").write_text("<html>Studio</html>")
    app = Starlette(
        routes=[Mount("/", app=_StudioStaticFiles(directory=tmp_path, html=True))]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio"
    ) as client:
        assert (await client.get("/workspace/workflow")).text == "<html>Studio</html>"
        assert (await client.get("/api/missing")).status_code == 404
