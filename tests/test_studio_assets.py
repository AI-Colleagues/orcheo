"""Precompressed Studio delivery and cache negotiation regression tests."""

from __future__ import annotations
import gzip
import runpy
from pathlib import Path
from random import Random
import httpx
import pytest
from starlette.applications import Starlette
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from orcheo import studio_assets
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
        expected_size = (
            compressed_path.stat().st_size if compressed else asset.stat().st_size
        )
        assert int(response.headers["Content-Length"]) == expected_size
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


@pytest.mark.parametrize("path", ["/", "/index.html", "/workspace/workflow"])
@pytest.mark.asyncio
async def test_studio_compresses_index_and_spa_routes(
    tmp_path: Path, path: str
) -> None:
    """Root and SPA routes negotiate the same compressed index representation."""
    content = "<html>" + "Studio " * 200 + "</html>"
    (tmp_path / "index.html").write_text(content)
    compress_assets(tmp_path)
    app = _StudioStaticFiles(directory=tmp_path, html=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio"
    ) as client:
        response = await client.get(path, headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.text == content
        assert response.headers["Content-Encoding"] == "gzip"
        assert response.headers["Content-Type"] == "text/html"
        cached = await client.get(
            path,
            headers={
                "Accept-Encoding": "gzip",
                "If-None-Match": response.headers["ETag"],
            },
        )
        assert cached.status_code == 304


@pytest.mark.asyncio
async def test_studio_gzip_uses_compressed_body_size_and_validator(
    tmp_path: Path,
) -> None:
    """GET and HEAD describe the compressed file, with an encoding-specific ETag."""
    content = "const domain = 'configured.example';\n" * 100
    (tmp_path / "app.js").write_text(content)
    compress_assets(tmp_path)
    compressed = (tmp_path / "app.js.gz").read_bytes()
    app = _StudioStaticFiles(directory=tmp_path, html=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio"
    ) as client:
        async with client.stream(
            "GET", "/app.js", headers={"Accept-Encoding": "gzip"}
        ) as response:
            body = b"".join([chunk async for chunk in response.aiter_raw()])
            compressed_etag = response.headers["ETag"]
            assert body == compressed
            assert int(response.headers["Content-Length"]) == len(compressed)
        head = await client.head("/app.js", headers={"Accept-Encoding": "gzip"})
        assert head.content == b""
        assert head.headers["ETag"] == compressed_etag
        assert int(head.headers["Content-Length"]) == len(compressed)
        identity = await client.get(
            "/app.js",
            headers={"Accept-Encoding": "identity", "If-None-Match": compressed_etag},
        )
        assert identity.status_code == 200
        assert identity.text == content
        assert identity.headers["ETag"] != compressed_etag


@pytest.mark.parametrize(
    ("range_header", "status"),
    [
        ("bytes=0-9", 206),
        ("bytes=0-2,20-22", 206),
        ("bytes=10000-", 416),
        ("bytes=invalid", 400),
    ],
)
@pytest.mark.asyncio
async def test_studio_ranges_use_identity_encoding(
    tmp_path: Path, range_header: str, status: int
) -> None:
    """Single, multipart and invalid ranges never label plain bodies as gzip."""
    content = "const domain = 'configured.example';\n" * 100
    (tmp_path / "app.js").write_text(content)
    compress_assets(tmp_path)
    app = _StudioStaticFiles(directory=tmp_path, html=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio"
    ) as client:
        response = await client.get(
            "/app.js", headers={"Accept-Encoding": "gzip", "Range": range_header}
        )
        assert response.status_code == status
        assert "Content-Encoding" not in response.headers
        assert int(response.headers["Content-Length"]) == len(response.content)
        if range_header == "bytes=0-9":
            assert response.text == content[:10]
            assert response.headers["Content-Range"] == f"bytes 0-9/{len(content)}"
        elif range_header == "bytes=0-2,20-22":
            assert response.headers["Content-Type"].startswith("multipart/byteranges;")
            assert f"Content-Range: bytes 0-2/{len(content)}" in response.text
            assert f"Content-Range: bytes 20-22/{len(content)}" in response.text
        elif status == 416:
            assert response.headers["Content-Range"] == f"bytes */{len(content)}"


@pytest.mark.asyncio
async def test_studio_if_range_uses_selected_representation_validator(
    tmp_path: Path,
) -> None:
    """A gzip validator cannot authorize a partial response of identity bytes."""
    content = "const domain = 'configured.example';\n" * 100
    (tmp_path / "app.js").write_text(content)
    compress_assets(tmp_path)
    app = _StudioStaticFiles(directory=tmp_path, html=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio"
    ) as client:
        for encoding in ("gzip", "identity"):
            original = await client.get(
                "/app.js", headers={"Accept-Encoding": encoding}
            )
            response = await client.get(
                "/app.js",
                headers={
                    "Accept-Encoding": "gzip",
                    "Range": "bytes=0-9",
                    "If-Range": original.headers["ETag"],
                },
            )
            assert "Content-Encoding" not in response.headers
            assert response.status_code == (200 if encoding == "gzip" else 206)
            assert response.text == (content if encoding == "gzip" else content[:10])


@pytest.mark.asyncio
async def test_studio_custom_404_is_not_labeled_gzip(tmp_path: Path) -> None:
    """A missing gzip sidecar does not turn the HTML error page into gzip bytes."""
    (tmp_path / "app.js").write_text("const studio = true;")
    (tmp_path / "404.html").write_text("<html>Not found</html>")
    app = _StudioStaticFiles(directory=tmp_path, html=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://studio"
    ) as client:
        response = await client.get("/app.js", headers={"Accept-Encoding": "gzip"})
        assert response.status_code == 200
        assert response.text == "const studio = true;"
        missing = await client.get("/api/missing", headers={"Accept-Encoding": "gzip"})
        assert missing.status_code == 404
        assert missing.text == "<html>Not found</html>"
        assert "Content-Encoding" not in missing.headers


def test_compress_assets_removes_sidecar_for_incompressible_content(
    tmp_path: Path,
) -> None:
    """Large files that do not shrink lose any stale gzip sidecar."""
    asset = tmp_path / "bundle.js"
    asset.write_bytes(Random(42).randbytes(2048))
    sidecar = tmp_path / "bundle.js.gz"
    sidecar.write_bytes(b"stale compressed asset")

    compress_assets(tmp_path)

    assert not sidecar.exists()


def test_compress_assets_command_uses_requested_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The module command compresses the directory passed on argv."""
    (tmp_path / "bundle.js").write_text("const configured = true;\n" * 100)
    monkeypatch.setattr(studio_assets.sys, "argv", ["studio_assets.py", str(tmp_path)])

    runpy.run_path(studio_assets.__file__, run_name="__main__")

    assert (tmp_path / "bundle.js.gz").exists()


@pytest.mark.asyncio
async def test_studio_static_gzip_propagates_non_not_found_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forbidden gzip asset response is not hidden by identity fallback."""

    async def forbidden_asset(
        self: StaticFiles, path: str, scope: dict[str, object]
    ) -> None:
        raise StarletteHTTPException(status_code=403, detail="forbidden asset")

    monkeypatch.setattr(StaticFiles, "get_response", forbidden_asset)
    app = _StudioStaticFiles(directory=tmp_path, html=True)

    with pytest.raises(StarletteHTTPException, match="forbidden asset"):
        await app._get_asset_response(
            "bundle.js",
            {"type": "http", "headers": [(b"accept-encoding", b"gzip")]},
        )
