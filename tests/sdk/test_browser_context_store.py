"""Tests for BrowserContextStore — TTL eviction and focus-priority resolution."""

from __future__ import annotations
from datetime import UTC, datetime, timedelta
from orcheo_sdk.cli.browser_context.store import BrowserContextStore


def _now() -> datetime:
    return datetime.now(UTC)


def test_empty_store_returns_null_context() -> None:
    """An empty store returns a null active context."""
    store = BrowserContextStore()
    result = store.get_active()
    assert result["session_id"] is None
    assert result["page"] is None
    assert result["total_sessions"] == 0


def test_upsert_and_get_active() -> None:
    """A single upsert makes that session the active one."""
    store = BrowserContextStore()
    now = _now()
    store.upsert(
        session_id="s1",
        page="canvas",
        workflow_id="wf-1",
        workflow_name="My Flow",
        focused=True,
        timestamp=now,
    )
    result = store.get_active()
    assert result["session_id"] == "s1"
    assert result["page"] == "canvas"
    assert result["workflow_id"] == "wf-1"
    assert result["workflow_name"] == "My Flow"
    assert result["focused"] is True
    assert result["total_sessions"] == 1


def test_focus_priority_resolution() -> None:
    """The session with the most recent last_focused_at wins."""
    store = BrowserContextStore()
    now = _now()
    t1 = now - timedelta(seconds=20)
    t2 = now - timedelta(seconds=10)

    store.upsert(
        session_id="s1",
        page="gallery",
        workflow_id=None,
        workflow_name=None,
        focused=True,
        timestamp=t1,
    )
    store.upsert(
        session_id="s2",
        page="canvas",
        workflow_id="wf-2",
        workflow_name="Flow 2",
        focused=True,
        timestamp=t2,
    )
    # Update s1 more recently but without focus
    t3 = now
    store.upsert(
        session_id="s1",
        page="gallery",
        workflow_id=None,
        workflow_name=None,
        focused=False,
        timestamp=t3,
    )

    result = store.get_active()
    # s2 was focused at t2; s1 was focused at t1 (earlier), so s2 wins
    assert result["session_id"] == "s2"
    assert result["total_sessions"] == 2


def test_fallback_to_most_recently_seen() -> None:
    """When no sessions have focus history, the most recently seen wins."""
    store = BrowserContextStore()
    now = _now()
    t1 = now - timedelta(seconds=10)
    t2 = now

    store.upsert(
        session_id="s1",
        page="gallery",
        workflow_id=None,
        workflow_name=None,
        focused=False,
        timestamp=t1,
    )
    store.upsert(
        session_id="s2",
        page="canvas",
        workflow_id="wf-2",
        workflow_name="Flow 2",
        focused=False,
        timestamp=t2,
    )

    result = store.get_active()
    assert result["session_id"] == "s2"


def test_ttl_eviction() -> None:
    """Sessions older than TTL are evicted."""
    store = BrowserContextStore(ttl_seconds=10)
    old_time = _now() - timedelta(seconds=20)
    store.upsert(
        session_id="expired",
        page="gallery",
        workflow_id=None,
        workflow_name=None,
        focused=False,
        timestamp=old_time,
    )
    result = store.get_active()
    assert result["total_sessions"] == 0
    assert result["session_id"] is None


def test_ttl_eviction_preserves_fresh() -> None:
    """Only expired sessions are evicted; fresh ones remain."""
    store = BrowserContextStore(ttl_seconds=10)
    old_time = _now() - timedelta(seconds=20)
    store.upsert(
        session_id="expired",
        page="gallery",
        workflow_id=None,
        workflow_name=None,
        focused=False,
        timestamp=old_time,
    )
    store.upsert(
        session_id="fresh",
        page="canvas",
        workflow_id="wf-1",
        workflow_name="Fresh",
        focused=True,
    )
    result = store.get_active()
    assert result["total_sessions"] == 1
    assert result["session_id"] == "fresh"


def test_get_all_sessions() -> None:
    """get_all_sessions returns all non-expired sessions ordered by last_seen."""
    store = BrowserContextStore()
    now = _now()
    t1 = now - timedelta(seconds=10)
    t2 = now

    store.upsert(
        session_id="s1",
        page="gallery",
        workflow_id=None,
        workflow_name=None,
        focused=False,
        timestamp=t1,
    )
    store.upsert(
        session_id="s2",
        page="canvas",
        workflow_id="wf-2",
        workflow_name="Flow 2",
        focused=True,
        timestamp=t2,
    )

    sessions = store.get_all_sessions()
    assert len(sessions) == 2
    # Most recently seen first
    assert sessions[0]["session_id"] == "s2"
    assert sessions[1]["session_id"] == "s1"


def test_upsert_updates_existing() -> None:
    """Upserting the same session_id updates the entry."""
    store = BrowserContextStore()
    now = _now()
    t1 = now - timedelta(seconds=10)
    t2 = now

    store.upsert(
        session_id="s1",
        page="gallery",
        workflow_id=None,
        workflow_name=None,
        focused=False,
        timestamp=t1,
    )
    store.upsert(
        session_id="s1",
        page="canvas",
        workflow_id="wf-1",
        workflow_name="My Flow",
        focused=True,
        timestamp=t2,
    )

    result = store.get_active()
    assert result["session_id"] == "s1"
    assert result["page"] == "canvas"
    assert result["workflow_id"] == "wf-1"
    assert result["total_sessions"] == 1


def test_focus_preserved_across_unfocused_update() -> None:
    """last_focused_at is preserved when a later update has focused=False."""
    store = BrowserContextStore()
    now = _now()
    t1 = now - timedelta(seconds=10)
    t2 = now

    store.upsert(
        session_id="s1",
        page="canvas",
        workflow_id="wf-1",
        workflow_name="Flow",
        focused=True,
        timestamp=t1,
    )
    store.upsert(
        session_id="s1",
        page="canvas",
        workflow_id="wf-1",
        workflow_name="Flow",
        focused=False,
        timestamp=t2,
    )

    result = store.get_active()
    # last_focused_at should still be t1
    assert result["last_focused_at"] == t1.isoformat()


def test_periodic_cleanup_evicts_expired_entries() -> None:
    """Lines 77-80: _periodic_cleanup evicts stale entries and reschedules."""
    store = BrowserContextStore(ttl_seconds=1)
    if store._cleanup_timer:
        store._cleanup_timer.cancel()

    old_time = _now() - timedelta(seconds=10)
    store.upsert(
        session_id="old",
        page="canvas",
        workflow_id=None,
        workflow_name=None,
        focused=False,
        timestamp=old_time,
    )
    assert "old" in store._sessions

    store._periodic_cleanup()

    assert "old" not in store._sessions
    assert store._cleanup_timer is not None
    store._cleanup_timer.cancel()
