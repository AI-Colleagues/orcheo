"""Tests for the candidate colleagues service."""

from __future__ import annotations
import asyncio
import io
import tarfile
from collections.abc import Iterator
from unittest.mock import AsyncMock, Mock
import httpx
import pytest
import respx
from orcheo.graph.ingestion import ScriptIngestionError
from orcheo_backend.app import candidates_service
from orcheo_backend.app.candidates_service import CandidateFetchError, get_candidates


_PREFIX = "AI-Colleagues-colleague-candidates-abc1234"

_WORKFLOW_WITH_FRONTMATTER = (
    "# /// orcheo\n"
    '# name = "LinkedIn Publisher"\n'
    '# handle = "linkedin_post"\n'
    '# description = "Publishes posts to LinkedIn."\n'
    '# avatar = "avatar-07"\n'
    '# subtitle = "AI Social Media"\n'
    '# version = "1.2.0"\n'
    "#\n"
    "# [[updates]]\n"
    '# version = "1.2.0"\n'
    '# summary = "Adds publishing guardrails."\n'
    '# migration = "Review scheduled posting windows."\n'
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
    assert item.avatar == "avatar-07"
    assert item.subtitle == "AI Social Media"
    assert item.description == "Publishes posts to LinkedIn."
    assert item.version == "1.2.0"
    assert [note.model_dump() for note in item.updates] == [
        {
            "version": "1.2.0",
            "summary": "Adds publishing guardrails.",
            "migration": "Review scheduled posting windows.",
        }
    ]


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


# ---------------------------------------------------------------------------
# _repo_settings
# ---------------------------------------------------------------------------


def test_repo_settings_returns_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default repo/ref/token values are used when env vars are absent."""
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO_REF", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_GITHUB_TOKEN", raising=False)

    repo, ref, token = candidates_service._repo_settings()

    assert repo == candidates_service._DEFAULT_REPO
    assert ref == candidates_service._DEFAULT_REF
    assert token is None


def test_repo_settings_reads_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Custom env vars override the compiled-in defaults."""
    monkeypatch.setenv("ORCHEO_CANDIDATES_REPO", "my-org/repo")
    monkeypatch.setenv("ORCHEO_CANDIDATES_REPO_REF", "staging")
    monkeypatch.setenv("ORCHEO_CANDIDATES_GITHUB_TOKEN", "ghs_token123")

    repo, ref, token = candidates_service._repo_settings()

    assert repo == "my-org/repo"
    assert ref == "staging"
    assert token == "ghs_token123"


# ---------------------------------------------------------------------------
# _download_tarball
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_download_tarball_returns_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful 200 response body is returned as bytes (no auth header)."""
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO_REF", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_GITHUB_TOKEN", raising=False)

    with respx.mock() as mock:
        route = mock.get(
            "https://api.github.com/repos/"
            "AI-Colleagues/colleague-candidates/tarball/main"
        ).respond(200, content=b"fake-tarball")

        result = await candidates_service._download_tarball()

    assert result == b"fake-tarball"
    assert "authorization" not in route.calls[0].request.headers


@pytest.mark.asyncio()
async def test_download_tarball_sends_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An Authorization header is included when a GitHub token is configured."""
    monkeypatch.setenv("ORCHEO_CANDIDATES_GITHUB_TOKEN", "ghs_test")
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO_REF", raising=False)

    with respx.mock() as mock:
        route = mock.get(
            "https://api.github.com/repos/"
            "AI-Colleagues/colleague-candidates/tarball/main"
        ).respond(200, content=b"data")

        result = await candidates_service._download_tarball()

    assert result == b"data"
    assert route.calls[0].request.headers["authorization"] == "Bearer ghs_test"


@pytest.mark.asyncio()
async def test_download_tarball_raises_when_too_large(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A response that exceeds the size cap raises CandidateFetchError."""
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO_REF", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_GITHUB_TOKEN", raising=False)
    big = b"x" * (candidates_service._MAX_TARBALL_BYTES + 1)

    with respx.mock() as mock:
        mock.get(
            "https://api.github.com/repos/"
            "AI-Colleagues/colleague-candidates/tarball/main"
        ).respond(200, content=big)

        with pytest.raises(CandidateFetchError, match="too large"):
            await candidates_service._download_tarball()


@pytest.mark.asyncio()
async def test_download_tarball_raises_on_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An HTTP error response is surfaced via raise_for_status."""
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_REPO_REF", raising=False)
    monkeypatch.delenv("ORCHEO_CANDIDATES_GITHUB_TOKEN", raising=False)

    with respx.mock() as mock:
        mock.get(
            "https://api.github.com/repos/"
            "AI-Colleagues/colleague-candidates/tarball/main"
        ).respond(403)

        with pytest.raises(httpx.HTTPStatusError):
            await candidates_service._download_tarball()


# ---------------------------------------------------------------------------
# _build_candidate
# ---------------------------------------------------------------------------


def test_build_candidate_returns_none_on_cli_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Returns None when frontmatter parsing raises CLIError."""
    from orcheo_sdk.cli.errors import CLIError

    def fail_parse(source: str) -> None:
        raise CLIError("no frontmatter block")

    monkeypatch.setattr(candidates_service, "parse_workflow_frontmatter", fail_parse)

    result = candidates_service._build_candidate("test/dir", "x = 1", None)

    assert result is None


def test_build_candidate_ignores_non_dict_config() -> None:
    """A config.json that parses to a non-dict value leaves config as None."""
    result = candidates_service._build_candidate(
        "linkedin_post",
        _WORKFLOW_WITH_FRONTMATTER,
        "[1, 2, 3]",  # valid JSON but not a dict
    )

    assert result is not None
    assert result.config is None


def test_build_candidate_ignores_invalid_json_config() -> None:
    """An unparseable config.json is silently ignored."""
    result = candidates_service._build_candidate(
        "linkedin_post",
        _WORKFLOW_WITH_FRONTMATTER,
        "this is not json",
    )

    assert result is not None
    assert result.config is None


def test_build_candidate_defers_remote_script_rendering() -> None:
    """Catalog refresh does not execute remotely sourced workflow Python."""
    result = candidates_service._build_candidate(
        "linkedin_post", _WORKFLOW_WITH_FRONTMATTER, None
    )

    assert result is not None
    assert result.mermaid is None


@pytest.mark.asyncio()
async def test_render_candidate_previews_uses_local_catalog_ingestion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preview derivation uses the RP-sandboxed script ingestion path."""
    candidate = candidates_service._build_candidate(
        "linkedin_post", _WORKFLOW_WITH_FRONTMATTER, None
    )
    assert candidate is not None
    ingestor = Mock(return_value={"format": "langgraph-script", "source": "x"})
    renderer = Mock(return_value="graph TD; A-->B")
    monkeypatch.setattr(candidates_service, "ingest_langgraph_script", ingestor)
    monkeypatch.setattr(
        candidates_service, "render_mermaid_from_graph_payload", renderer
    )

    result = await candidates_service._render_candidate_previews([candidate])

    assert result[0].mermaid == "graph TD; A-->B"
    ingestor.assert_called_once_with(_WORKFLOW_WITH_FRONTMATTER, entrypoint=None)
    renderer.assert_called_once_with({"format": "langgraph-script", "source": "x"})


@pytest.mark.asyncio()
async def test_render_candidate_previews_handles_ingestion_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ingestion errors are downgraded to a missing mermaid preview."""
    first = candidates_service._build_candidate(
        "first", _WORKFLOW_WITH_FRONTMATTER, None
    )
    second = candidates_service._build_candidate(
        "second", _WORKFLOW_WITH_FRONTMATTER, None
    )
    assert first is not None
    assert second is not None

    ingestor = Mock(
        side_effect=[
            ScriptIngestionError("bad graph"),
            RuntimeError("preview crashed"),
        ]
    )
    monkeypatch.setattr(candidates_service, "ingest_langgraph_script", ingestor)

    result = await candidates_service._render_candidate_previews([first, second])

    assert [item.mermaid for item in result] == [None, None]


@pytest.mark.asyncio()
async def test_enrich_cached_with_previews_returns_when_cache_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing cache entry short-circuits without calling the renderer."""
    called = False

    async def fake_render(candidates: list[object]) -> list[object]:
        nonlocal called
        called = True
        return candidates

    monkeypatch.setattr(candidates_service, "_render_candidate_previews", fake_render)

    candidates_service._state.entry = None
    await candidates_service._enrich_cached_with_previews()

    assert called is False


@pytest.mark.asyncio()
async def test_enrich_cached_with_previews_does_not_overwrite_replaced_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A newer cache entry wins if refresh races with preview enrichment."""
    candidate = candidates_service._build_candidate(
        "race", _WORKFLOW_WITH_FRONTMATTER, None
    )
    assert candidate is not None
    original_entry = candidates_service._CacheEntry(
        candidates=[candidate], fetched_at=1.0
    )
    replacement_entry = candidates_service._CacheEntry(
        candidates=[candidate.model_copy(update={"mermaid": "updated"})],
        fetched_at=2.0,
    )
    candidates_service._state.entry = original_entry

    async def fake_render(candidates: list[object]) -> list[object]:
        del candidates
        candidates_service._state.entry = replacement_entry
        return replacement_entry.candidates

    monkeypatch.setattr(candidates_service, "_render_candidate_previews", fake_render)

    await candidates_service._enrich_cached_with_previews()

    assert candidates_service._state.entry is replacement_entry


@pytest.mark.asyncio()
async def test_enrich_cached_with_previews_logs_renderer_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renderer exceptions are swallowed so background enrichment never crashes."""
    candidate = candidates_service._build_candidate(
        "boom", _WORKFLOW_WITH_FRONTMATTER, None
    )
    assert candidate is not None
    candidates_service._state.entry = candidates_service._CacheEntry(
        candidates=[candidate],
        fetched_at=1.0,
    )

    async def fake_render(candidates: list[object]) -> list[object]:
        del candidates
        raise RuntimeError("renderer exploded")

    monkeypatch.setattr(candidates_service, "_render_candidate_previews", fake_render)

    await candidates_service._enrich_cached_with_previews()


# ---------------------------------------------------------------------------
# _parse_tarball
# ---------------------------------------------------------------------------


def test_parse_tarball_skips_directory_members() -> None:
    """A directory entry in the tarball is skipped without errors."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        dir_info = tarfile.TarInfo(name=f"{_PREFIX}/colleagues/some_dir/")
        dir_info.type = tarfile.DIRTYPE
        archive.addfile(dir_info)
        data = _WORKFLOW_WITH_FRONTMATTER.encode()
        file_info = tarfile.TarInfo(name=f"{_PREFIX}/colleagues/some_dir/workflow.py")
        file_info.size = len(data)
        archive.addfile(file_info, io.BytesIO(data))
    tarball = buffer.getvalue()

    candidates = candidates_service._parse_tarball(tarball)

    assert len(candidates) == 1


def test_parse_tarball_skips_unextractable_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Members for which extractfile returns None are silently skipped."""
    tarball = _make_tarball(
        {"colleagues/test_dir/workflow.py": _WORKFLOW_WITH_FRONTMATTER}
    )
    monkeypatch.setattr(tarfile.TarFile, "extractfile", lambda self, member: None)

    candidates = candidates_service._parse_tarball(tarball)

    assert candidates == []


def test_parse_tarball_excludes_none_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidates for which _build_candidate returns None are not included."""
    from orcheo_sdk.cli.errors import CLIError

    def fail_parse(source: str) -> None:
        raise CLIError("bad frontmatter")

    monkeypatch.setattr(candidates_service, "parse_workflow_frontmatter", fail_parse)

    tarball = _make_tarball(
        {"colleagues/some_agent/workflow.py": _WORKFLOW_WITH_FRONTMATTER}
    )

    candidates = candidates_service._parse_tarball(tarball)

    assert candidates == []


# ---------------------------------------------------------------------------
# _background_refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_background_refresh_swallows_exceptions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Background refresh logs failures and does not propagate exceptions."""

    async def bad_download() -> bytes:
        raise RuntimeError("network down")

    monkeypatch.setattr(candidates_service, "_download_tarball", bad_download)

    # Must not raise
    await candidates_service._background_refresh()


# ---------------------------------------------------------------------------
# _schedule_background_refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_schedule_background_refresh_deduplicates_running_task() -> None:
    """No new task is created when a background refresh is already in flight."""
    event = asyncio.Event()

    async def blocked() -> None:
        await event.wait()

    candidates_service._state.background_task = asyncio.create_task(blocked())
    original_task = candidates_service._state.background_task

    candidates_service._schedule_background_refresh()

    assert candidates_service._state.background_task is original_task

    event.set()
    await original_task


# ---------------------------------------------------------------------------
# get_candidates — additional branches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_candidates_concurrent_cold_fetch_single_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two concurrent cold-cache callers trigger only one download."""
    tarball = _make_tarball(
        {"colleagues/linkedin_post/workflow.py": _WORKFLOW_WITH_FRONTMATTER}
    )
    fetch_count = 0

    async def slow_download() -> bytes:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0)  # yield so the second caller can enter entry-is-None
        return tarball

    monkeypatch.setattr(candidates_service, "_download_tarball", slow_download)

    results = await asyncio.gather(get_candidates(), get_candidates())

    assert fetch_count == 1
    assert all(len(r) == 1 for r in results)


@pytest.mark.asyncio()
async def test_get_candidates_reraises_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CandidateFetchError from _refresh_cache is re-raised unchanged."""

    async def bad_download() -> bytes:
        raise CandidateFetchError("tarball too large")

    monkeypatch.setattr(candidates_service, "_download_tarball", bad_download)

    with pytest.raises(CandidateFetchError, match="tarball too large"):
        await get_candidates()


@pytest.mark.asyncio()
async def test_render_candidate_previews_handles_script_ingestion_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ScriptIngestionError during script ingestion is silently logged."""
    candidate = candidates_service._build_candidate(
        "failing_post", _WORKFLOW_WITH_FRONTMATTER, None
    )
    assert candidate is not None
    monkeypatch.setattr(
        candidates_service,
        "ingest_langgraph_script",
        Mock(side_effect=candidates_service.ScriptIngestionError("bad graph")),
    )

    result = await candidates_service._render_candidate_previews([candidate])

    assert result[0].mermaid is None


@pytest.mark.asyncio()
async def test_render_candidate_previews_handles_unexpected_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected errors during script ingestion are silently logged."""
    candidate = candidates_service._build_candidate(
        "crashing_post", _WORKFLOW_WITH_FRONTMATTER, None
    )
    assert candidate is not None
    monkeypatch.setattr(
        candidates_service,
        "ingest_langgraph_script",
        Mock(side_effect=RuntimeError("unexpected crash")),
    )

    result = await candidates_service._render_candidate_previews([candidate])

    assert result[0].mermaid is None


@pytest.mark.asyncio()
async def test_get_candidates_cold_cache_does_not_wait_for_preview_rendering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cold-cache callers get candidates immediately; mermaid renders in the background."""
    tarball = _make_tarball(
        {"colleagues/linkedin_post/workflow.py": _WORKFLOW_WITH_FRONTMATTER}
    )
    gate = asyncio.Event()

    async def gated_render(
        candidates: list,
    ) -> list:
        await gate.wait()
        from orcheo_backend.app.schemas.candidates import CandidateItem

        return [c.model_copy(update={"mermaid": "graph TD; A-->B"}) for c in candidates]

    monkeypatch.setattr(
        candidates_service, "_download_tarball", AsyncMock(return_value=tarball)
    )
    monkeypatch.setattr(candidates_service, "_render_candidate_previews", gated_render)

    result = await get_candidates()

    assert len(result) == 1
    assert result[0].mermaid is None

    task = candidates_service._state.preview_task
    assert task is not None and not task.done()

    gate.set()
    await task

    enriched = await get_candidates()
    assert enriched[0].mermaid == "graph TD; A-->B"
