"""Fetch candidate AI colleagues from the colleague-candidates GitHub repo.

The colleague-candidates repository stores each candidate as an Orcheo workflow
under ``colleagues/``. This module downloads the repository tarball, parses the
``# /// orcheo`` frontmatter of every ``workflow.py``, and caches the result
with a TTL plus stale-while-revalidate strategy so the Candidates tab never
blocks on GitHub and keeps serving the last good payload on transient failures.
"""

from __future__ import annotations
import asyncio
import io
import json
import logging
import os
import tarfile
import time
from dataclasses import dataclass
from typing import Any
import httpx
from orcheo.graph.ingestion import ScriptIngestionError, ingest_langgraph_script
from orcheo.workflow.mermaid import render_mermaid_from_graph_payload
from orcheo_backend.app.schemas.candidates import CandidateItem, CandidateUpdateNote
from orcheo_sdk.cli.errors import CLIError
from orcheo_sdk.cli.workflow.frontmatter import parse_workflow_frontmatter


logger = logging.getLogger(__name__)

_DEFAULT_REPO = "AI-Colleagues/colleague-candidates"
_DEFAULT_REF = "main"
_COLLEAGUES_DIR = "colleagues"
_WORKFLOW_FILENAME = "workflow.py"
_CONFIG_FILENAME = "config.json"
_CACHE_TTL_SECONDS = 300.0
_FETCH_TIMEOUT_SECONDS = 30.0
_MAX_TARBALL_BYTES = 16 * 1024 * 1024


class CandidateFetchError(RuntimeError):
    """Raised when candidates cannot be fetched and no cached copy exists."""


@dataclass
class _CacheEntry:
    """A cached snapshot of candidates and when it was fetched."""

    candidates: list[CandidateItem]
    fetched_at: float


class _CacheState:
    """Mutable holder for the candidate cache and background refresh task."""

    def __init__(self) -> None:
        self.entry: _CacheEntry | None = None
        self.background_task: asyncio.Task[None] | None = None
        self.preview_task: asyncio.Task[None] | None = None


_state = _CacheState()
_refresh_lock = asyncio.Lock()


def reset_cache() -> None:
    """Clear cached state. Intended for tests."""
    _state.entry = None
    _state.background_task = None
    _state.preview_task = None


def _repo_settings() -> tuple[str, str, str | None]:
    """Return the (repo, ref, token) for the candidates repository."""
    repo = os.getenv("ORCHEO_CANDIDATES_REPO", "").strip() or _DEFAULT_REPO
    ref = os.getenv("ORCHEO_CANDIDATES_REPO_REF", "").strip() or _DEFAULT_REF
    token = os.getenv("ORCHEO_CANDIDATES_GITHUB_TOKEN") or None
    return repo, ref, token


def get_candidate_source_ref() -> str:
    """Return the configured candidate repository ref for source metadata."""
    _, ref, _ = _repo_settings()
    return ref


async def _download_tarball() -> bytes:
    """Download the candidates repository tarball from GitHub."""
    repo, ref, token = _repo_settings()
    url = f"https://api.github.com/repos/{repo}/tarball/{ref}"
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=_FETCH_TIMEOUT_SECONDS
    ) as client:
        response = await client.get(url, headers=headers)
    response.raise_for_status()
    content = response.content
    if len(content) > _MAX_TARBALL_BYTES:
        raise CandidateFetchError("Candidate repository tarball is too large.")
    return content


def _candidate_dir(rel_path: str) -> str:
    """Return the colleague directory relative to ``colleagues/``."""
    trimmed = rel_path[len(_COLLEAGUES_DIR) + 1 :]
    return trimmed.rsplit("/", 1)[0]


def _build_candidate(
    directory: str,
    source: str,
    config_text: str | None,
) -> CandidateItem | None:
    """Build a candidate from a workflow file and optional config.

    Returns None when frontmatter parsing fails.
    """
    try:
        frontmatter = parse_workflow_frontmatter(source)
    except CLIError:
        logger.warning("Skipping candidate with invalid frontmatter: %s", directory)
        return None
    fallback_name = directory.rsplit("/", 1)[-1]
    handle = frontmatter.workflow_handle or frontmatter.workflow_id or fallback_name

    config: dict[str, Any] | None = None
    if config_text is not None:
        try:
            parsed = json.loads(config_text)
            if isinstance(parsed, dict):
                config = parsed
        except json.JSONDecodeError:
            logger.debug("Invalid config.json for candidate %s", directory)

    return CandidateItem(
        id=directory,
        handle=handle,
        name=frontmatter.name or fallback_name,
        description=frontmatter.description,
        avatar=frontmatter.avatar,
        subtitle=frontmatter.subtitle,
        script=source,
        config=config,
        entrypoint=frontmatter.entrypoint,
        notes=frontmatter.notes,
        metadata=frontmatter.metadata,
        version=frontmatter.version,
        updates=[
            CandidateUpdateNote(
                version=update.version,
                summary=update.summary,
                migration=update.migration,
            )
            for update in (frontmatter.updates or [])
        ],
        # Populated later by catalog preview enrichment; parsing the archive
        # itself must not execute remotely sourced Python.
        mermaid=None,
    )


def _parse_tarball(payload: bytes) -> list[CandidateItem]:
    """Extract candidate colleagues from a repository tarball."""
    scripts: dict[str, str] = {}
    config_texts: dict[str, str] = {}

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            # Drop the GitHub-generated top-level directory segment.
            _, _, rel_path = member.name.partition("/")
            if not rel_path.startswith(f"{_COLLEAGUES_DIR}/"):
                continue
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            if rel_path.endswith(f"/{_WORKFLOW_FILENAME}"):
                directory = _candidate_dir(rel_path)
                scripts[directory] = extracted.read().decode("utf-8")
            elif rel_path.endswith(f"/{_CONFIG_FILENAME}"):
                directory = _candidate_dir(rel_path)
                config_texts[directory] = extracted.read().decode("utf-8")

    candidates: list[CandidateItem] = []
    for directory, source in scripts.items():
        candidate = _build_candidate(directory, source, config_texts.get(directory))
        if candidate is not None:
            candidates.append(candidate)
    candidates.sort(key=lambda item: item.id)
    return candidates


async def _refresh_cache() -> None:
    """Fetch candidates from GitHub and replace the cached snapshot."""
    payload = await _download_tarball()
    candidates = await asyncio.to_thread(_parse_tarball, payload)
    _state.entry = _CacheEntry(candidates=candidates, fetched_at=time.monotonic())
    enriched = await _render_candidate_previews(candidates)
    _state.entry = _CacheEntry(candidates=enriched, fetched_at=time.monotonic())


async def _enrich_cached_with_previews() -> None:
    """Render mermaid previews for the current cached entry without re-fetching.

    Called as a fire-and-forget task after a cold-cache fetch so callers are
    not blocked on preview rendering.  Only updates the entry if it has not
    been replaced by a full background refresh in the meantime.
    """
    entry = _state.entry
    if entry is None:
        return
    try:
        enriched = await _render_candidate_previews(entry.candidates)
        if _state.entry is entry:
            _state.entry = _CacheEntry(candidates=enriched, fetched_at=time.monotonic())
    except Exception:
        logger.warning("Background preview enrichment failed", exc_info=True)


async def _render_candidate_previews(
    candidates: list[CandidateItem],
) -> list[CandidateItem]:
    """Derive mermaid previews for remote candidates by executing each script."""
    rendered: list[CandidateItem] = []
    for candidate in candidates:
        mermaid: str | None = None

        try:
            script_payload = ingest_langgraph_script(
                candidate.script,
                entrypoint=candidate.entrypoint,
            )
            mermaid = render_mermaid_from_graph_payload(script_payload)
        except ScriptIngestionError:
            logger.debug("Graph derivation failed for candidate %s", candidate.id)
        except Exception:
            logger.debug(
                "Unexpected error during graph derivation for %s",
                candidate.id,
                exc_info=True,
            )

        rendered.append(candidate.model_copy(update={"mermaid": mermaid}))
    return rendered


async def _background_refresh() -> None:
    """Refresh the cache without raising, keeping the last good payload."""
    try:
        async with _refresh_lock:
            await _refresh_cache()
    except Exception:
        logger.warning("Background candidate refresh failed", exc_info=True)


def _schedule_background_refresh() -> None:
    """Start a single background refresh task when none is already running."""
    task = _state.background_task
    if task is not None and not task.done():
        return
    _state.background_task = asyncio.create_task(_background_refresh())


async def get_candidates() -> list[CandidateItem]:
    """Return cached candidates, refreshing in the background when stale."""
    entry = _state.entry
    if entry is None:
        async with _refresh_lock:
            if _state.entry is None:
                try:
                    payload = await _download_tarball()
                    candidates = await asyncio.to_thread(_parse_tarball, payload)
                    _state.entry = _CacheEntry(
                        candidates=candidates, fetched_at=time.monotonic()
                    )
                except CandidateFetchError:
                    raise
                except Exception as exc:
                    raise CandidateFetchError(str(exc)) from exc
        # Enrich with mermaid in the background so callers are not blocked on
        # sequential preview rendering (~20s per candidate).
        task = _state.preview_task
        if task is None or task.done():
            _state.preview_task = asyncio.create_task(_enrich_cached_with_previews())
        assert _state.entry is not None
        return _state.entry.candidates

    if time.monotonic() - entry.fetched_at >= _CACHE_TTL_SECONDS:
        _schedule_background_refresh()
    return entry.candidates
