"""Tests for the candidate colleagues service."""

from __future__ import annotations
import io
import tarfile
from collections.abc import Iterator
import httpx
import pytest
from orcheo_backend.app import candidates_service
from orcheo_backend.app.candidates_service import CandidateFetchError, get_candidates


_PREFIX = "AI-Colleagues-colleague-candidates-abc1234"

_WORKFLOW_WITH_FRONTMATTER = (
    "# /// orcheo\n"
    '# name = "LinkedIn Publisher"\n'
    '# handle = "linkedin_post"\n'
    '# description = "Publishes posts to LinkedIn."\n'
    '# emoji = "📣"\n'
    '# subtitle = "AI Social Media"\n'
    "# ///\n"
    "\n"
    "graph = object()\n"
)

_WORKFLOW_WITHOUT_FRONTMATTER = "graph = object()\n"


def _make_tarball(files: dict[str, str]) -> bytes:
    """Build a gzip tarball mimicking a GitHub repository archive."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name=f"{_PREFIX}/{name}")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def _reset_cache() -> Iterator[None]:
    """Clear the module cache around every test."""
    candidates_service.reset_cache()
    yield
    candidates_service.reset_cache()


def test_parse_tarball_extracts_candidate_metadata() -> None:
    """Frontmatter fields are surfaced as candidate metadata."""
    tarball = _make_tarball(
        {
            "colleagues/linkedin_post/workflow.py": _WORKFLOW_WITH_FRONTMATTER,
            "colleagues/linkedin_post/config.json": "{}",
            "README.md": "# repo",
        }
    )

    candidates = candidates_service._parse_tarball(tarball)

    assert len(candidates) == 1
    item = candidates[0]
    assert item.id == "linkedin_post"
    assert item.handle == "linkedin_post"
    assert item.name == "LinkedIn Publisher"
    assert item.emoji == "📣"
    assert item.subtitle == "AI Social Media"
    assert item.description == "Publishes posts to LinkedIn."


def test_parse_tarball_handles_nested_dirs_and_missing_frontmatter() -> None:
    """Nested colleagues are kept and missing frontmatter falls back to the dir."""
    tarball = _make_tarball(
        {
            "colleagues/wechat/daily_reminder/workflow.py": (
                _WORKFLOW_WITHOUT_FRONTMATTER
            ),
            "colleagues/linkedin_post/workflow.py": _WORKFLOW_WITH_FRONTMATTER,
            "colleagues/linkedin_post/helper.py": "x = 1",
        }
    )

    candidates = candidates_service._parse_tarball(tarball)

    assert [c.id for c in candidates] == ["linkedin_post", "wechat/daily_reminder"]
    nested = candidates[1]
    assert nested.id == "wechat/daily_reminder"
    assert nested.handle == "daily_reminder"
    assert nested.name == "daily_reminder"
    assert nested.emoji is None


@pytest.mark.asyncio()
async def test_get_candidates_caches_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh cache is reused without re-downloading the tarball."""
    tarball = _make_tarball(
        {"colleagues/linkedin_post/workflow.py": _WORKFLOW_WITH_FRONTMATTER}
    )
    calls = 0

    async def fake_download() -> bytes:
        nonlocal calls
        calls += 1
        return tarball

    monkeypatch.setattr(candidates_service, "_download_tarball", fake_download)

    first = await get_candidates()
    second = await get_candidates()

    assert calls == 1
    assert [c.id for c in first] == ["linkedin_post"]
    assert second == first


@pytest.mark.asyncio()
async def test_get_candidates_raises_when_cold_fetch_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure with no cached copy surfaces as CandidateFetchError."""

    async def fake_download() -> bytes:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(candidates_service, "_download_tarball", fake_download)

    with pytest.raises(CandidateFetchError):
        await get_candidates()


@pytest.mark.asyncio()
async def test_get_candidates_serves_stale_then_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale cache is served immediately while a refresh runs in the background."""
    first_tarball = _make_tarball(
        {"colleagues/linkedin_post/workflow.py": _WORKFLOW_WITH_FRONTMATTER}
    )
    second_tarball = _make_tarball(
        {
            "colleagues/linkedin_post/workflow.py": _WORKFLOW_WITH_FRONTMATTER,
            "colleagues/simple_agent/workflow.py": _WORKFLOW_WITHOUT_FRONTMATTER,
        }
    )
    payloads = [first_tarball, second_tarball]

    async def fake_download() -> bytes:
        return payloads.pop(0)

    monkeypatch.setattr(candidates_service, "_download_tarball", fake_download)

    first = await get_candidates()
    assert len(first) == 1

    entry = candidates_service._state.entry
    assert entry is not None
    entry.fetched_at -= candidates_service._CACHE_TTL_SECONDS + 1.0

    stale = await get_candidates()
    assert len(stale) == 1

    task = candidates_service._state.background_task
    assert task is not None
    await task

    refreshed = await get_candidates()
    assert len(refreshed) == 2
