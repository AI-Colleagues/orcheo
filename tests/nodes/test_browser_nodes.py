"""Tests for Playwright-backed browser nodes."""

from __future__ import annotations
import os
from pathlib import Path
from typing import Any
import pytest
from langchain_core.runnables import RunnableConfig
from orcheo.graph.state import State
from orcheo.nodes import browser as browser_nodes
from orcheo.nodes.browser import (
    BrowserActionNode,
    BrowserCloseNode,
    BrowserExtractNode,
    BrowserNavigateNode,
    BrowserScriptNode,
    BrowserWaitNode,
)


class FakeResponse:
    """Simple Playwright response double."""

    def __init__(
        self,
        url: str,
        *,
        status: int = 200,
        ok: bool = True,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.status = status
        self.ok = ok
        self.headers = headers or {"content-type": "text/html"}


class FakeTracing:
    """Capture trace start and stop calls."""

    def __init__(self) -> None:
        self.start_kwargs: dict[str, Any] | None = None
        self.stop_path: str | None = None

    async def start(self, **kwargs: Any) -> None:
        self.start_kwargs = kwargs

    async def stop(self, path: str | None = None) -> None:
        self.stop_path = path


class FakeLocator:
    """Minimal locator double used by the browser nodes."""

    def __init__(self, page: FakePage, selector: str) -> None:
        self._page = page
        self._selector = selector

    async def click(self, **kwargs: Any) -> None:
        self._page.actions.append(("click", self._selector, None, kwargs))

    async def fill(self, value: str, **kwargs: Any) -> None:
        self._page.actions.append(("fill", self._selector, value, kwargs))

    async def press(self, value: str, **kwargs: Any) -> None:
        self._page.actions.append(("press", self._selector, value, kwargs))

    async def check(self, **kwargs: Any) -> None:
        self._page.actions.append(("check", self._selector, None, kwargs))

    async def uncheck(self, **kwargs: Any) -> None:
        self._page.actions.append(("uncheck", self._selector, None, kwargs))

    async def hover(self, **kwargs: Any) -> None:
        self._page.actions.append(("hover", self._selector, None, kwargs))

    async def select_option(self, value: Any, **kwargs: Any) -> None:
        self._page.actions.append(("select_option", self._selector, value, kwargs))

    async def set_input_files(self, value: Any, **kwargs: Any) -> None:
        self._page.actions.append(("set_input_files", self._selector, value, kwargs))

    async def wait_for(self, **kwargs: Any) -> None:
        self._page.wait_calls.append(("selector", self._selector, kwargs))

    async def inner_text(self) -> str:
        return self._page.text_values[self._selector]

    async def all_inner_texts(self) -> list[str]:
        return self._page.all_text_values[self._selector]

    async def inner_html(self) -> str:
        return self._page.html_values[self._selector]

    async def get_attribute(self, name: str) -> str | None:
        return self._page.attribute_values.get((self._selector, name))


class FakePage:
    """Minimal Playwright page double."""

    def __init__(self) -> None:
        self.url = "about:blank"
        self.title_text = "Blank"
        self.actions: list[tuple[str, str, Any, dict[str, Any]]] = []
        self.goto_calls: list[dict[str, Any]] = []
        self.wait_calls: list[tuple[str, Any, Any]] = []
        self.evaluate_calls: list[tuple[str, Any]] = []
        self.text_values = {"#result": "Orcheo"}
        self.all_text_values = {"li.item": ["one", "two"]}
        self.html_values = {"#result": "<span>Orcheo</span>"}
        self.attribute_values = {("#result", "href"): "https://example.com/item"}
        self.page_content = "<html><body>hello</body></html>"
        self.response_to_wait = FakeResponse(
            "https://example.com/api/ready",
            status=204,
            ok=True,
        )
        self.function_handle_payload: Any = None
        self.function_handle_disposed = False

    async def goto(self, url: str, **kwargs: Any) -> FakeResponse | None:
        self.url = url
        self.title_text = f"Title for {url}"
        self.goto_calls.append({"url": url, **kwargs})
        if getattr(self, "goto_returns_none", False):
            return None
        return FakeResponse(url)

    async def title(self) -> str:
        return self.title_text

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def content(self) -> str:
        return self.page_content

    async def wait_for_url(self, url_pattern: str, **kwargs: Any) -> None:
        self.wait_calls.append(("url", url_pattern, kwargs))

    async def wait_for_load_state(self, state: str, **kwargs: Any) -> None:
        self.wait_calls.append(("load_state", state, kwargs))

    async def wait_for_function(self, expression: str, **kwargs: Any) -> Any:
        self.wait_calls.append(("function", expression, kwargs))
        if self.function_handle_payload is None:
            return {"expression": expression, "arg": kwargs.get("arg")}
        return FakeJSHandle(self)

    async def wait_for_timeout(self, timeout: float) -> None:
        self.wait_calls.append(("timeout", timeout, {}))

    async def wait_for_response(self, predicate: Any, **kwargs: Any) -> FakeResponse:
        self.wait_calls.append(("response", self.response_to_wait.url, kwargs))
        assert predicate(self.response_to_wait) is True
        return self.response_to_wait

    async def evaluate(self, script: str, arg: Any = None) -> dict[str, Any]:
        self.evaluate_calls.append((script, arg))
        return {"script": script, "arg": arg}


class FakeContext:
    """Minimal browser context double."""

    def __init__(self, page: FakePage) -> None:
        self.page = page
        self.context_kwargs: dict[str, Any] | None = None
        self.closed = False
        self.tracing = FakeTracing()

    async def new_page(self) -> FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    """Minimal browser double."""

    def __init__(self, context: FakeContext) -> None:
        self.context = context
        self.closed = False
        self.context_kwargs: dict[str, Any] | None = None

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.context_kwargs = kwargs
        self.context.context_kwargs = kwargs
        return self.context

    async def close(self) -> None:
        self.closed = True


class FakeLauncher:
    """Minimal browser launcher double."""

    def __init__(self, browser: FakeBrowser) -> None:
        self.browser = browser
        self.launch_calls: list[dict[str, Any]] = []

    async def launch(self, **kwargs: Any) -> FakeBrowser:
        self.launch_calls.append(kwargs)
        return self.browser


class FakePlaywright:
    """Minimal Playwright runtime double."""

    def __init__(self) -> None:
        self.page = FakePage()
        self.context = FakeContext(self.page)
        self.browser = FakeBrowser(self.context)
        self.chromium = FakeLauncher(self.browser)
        self.firefox = FakeLauncher(self.browser)
        self.webkit = FakeLauncher(self.browser)
        self.stopped = False

    async def stop(self) -> None:
        self.stopped = True


class FakePlaywrightContextManager:
    """Return the fake Playwright runtime from .start()."""

    def __init__(self, playwright: FakePlaywright) -> None:
        self.playwright = playwright

    async def start(self) -> FakePlaywright:
        return self.playwright


class FakeJSHandle:
    """Minimal JSHandle double used by wait_for_function tests."""

    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def json_value(self) -> Any:
        return self._page.function_handle_payload

    async def dispose(self) -> None:
        self._page.function_handle_disposed = True


@pytest.fixture
def fake_browser_runtime(monkeypatch: pytest.MonkeyPatch) -> FakePlaywright:
    """Reset the browser session manager and inject a fake Playwright runtime."""

    fake_playwright = FakePlaywright()
    monkeypatch.setattr(
        browser_nodes,
        "_browser_session_manager",
        browser_nodes.BrowserSessionManager(),
    )
    monkeypatch.setattr(
        browser_nodes,
        "_async_playwright_factory",
        lambda: lambda: FakePlaywrightContextManager(fake_playwright),
    )
    return fake_playwright


@pytest.mark.asyncio
async def test_browser_nodes_navigate_action_extract_and_close(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Browser nodes should create sessions, act, extract, and close cleanly."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-1"})

    navigate = BrowserNavigateNode(
        name="navigate",
        url="https://example.com/dashboard",
        launch_args=["--disable-dev-shm-usage"],
        viewport_width=1280,
        viewport_height=720,
        extra_http_headers={"x-test": "1"},
        trace_path="/tmp/browser-trace.zip",
    )
    navigate_payload = (await navigate(state, config))["results"]["navigate"]

    assert navigate_payload["created_session"] is True
    assert navigate_payload["resolved_session_key"] == "exec-1:browser"
    assert navigate_payload["title"] == "Title for https://example.com/dashboard"
    assert navigate_payload["response"]["status"] == 200
    assert fake_browser_runtime.chromium.launch_calls == [
        {"headless": True, "args": ["--disable-dev-shm-usage"]}
    ]
    assert fake_browser_runtime.browser.context_kwargs == {
        "ignore_https_errors": False,
        "java_script_enabled": True,
        "viewport": {"width": 1280, "height": 720},
        "extra_http_headers": {"x-test": "1"},
    }
    assert fake_browser_runtime.context.tracing.start_kwargs == {
        "screenshots": True,
        "snapshots": True,
        "sources": True,
    }

    action = BrowserActionNode(
        name="action",
        action="fill",
        locator="#search",
        value="playwright",
    )
    action_payload = (await action(state, config))["results"]["action"]

    assert action_payload["action"] == "fill"
    assert fake_browser_runtime.page.actions[-1][0:3] == (
        "fill",
        "#search",
        "playwright",
    )

    extract = BrowserExtractNode(
        name="extract",
        mode="attribute",
        locator="#result",
        attribute_name="href",
    )
    extract_payload = (await extract(state, config))["results"]["extract"]

    assert extract_payload["value"] == "https://example.com/item"

    close = BrowserCloseNode(name="close")
    close_payload = (await close(state, config))["results"]["close"]

    assert close_payload["closed"] is True
    assert fake_browser_runtime.context.tracing.stop_path == "/tmp/browser-trace.zip"
    assert fake_browser_runtime.context.closed is True
    assert fake_browser_runtime.browser.closed is True
    assert fake_browser_runtime.stopped is True


@pytest.mark.asyncio
async def test_browser_wait_and_script_nodes_reuse_existing_session(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Wait and script nodes should operate on an existing Playwright session."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-2"})
    await BrowserNavigateNode(
        name="navigate",
        url="https://example.com",
    )(state, config)

    selector_wait = BrowserWaitNode(
        name="wait_selector",
        wait_for="selector",
        locator="#result",
        selector_state="visible",
    )
    selector_payload = (await selector_wait(state, config))["results"]["wait_selector"]
    assert selector_payload["value"] == {"locator": "#result", "state": "visible"}

    function_wait = BrowserWaitNode(
        name="wait_function",
        wait_for="function",
        expression="() => window.ready === true",
        arg={"expected": True},
    )
    function_payload = (await function_wait(state, config))["results"]["wait_function"]
    assert function_payload["value"] == {
        "expression": "() => window.ready === true",
        "arg": {"expected": True},
    }

    response_wait = BrowserWaitNode(
        name="wait_response",
        wait_for="response",
        response_url="/api/ready",
        response_match_mode="contains",
        response_status=204,
    )
    response_payload = (await response_wait(state, config))["results"]["wait_response"]
    assert response_payload["value"] == {
        "url": "https://example.com/api/ready",
        "status": 204,
        "ok": True,
    }

    script = BrowserScriptNode(
        name="script",
        script="(payload) => payload.answer",
        arg={"answer": 42},
    )
    script_payload = (await script(state, config))["results"]["script"]
    assert script_payload["value"] == {
        "script": "(payload) => payload.answer",
        "arg": {"answer": 42},
    }


@pytest.mark.asyncio
async def test_browser_wait_for_function_serializes_handle(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Function waits should serialize and dispose returned JSHandle values."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-serialized"})
    fake_browser_runtime.page.function_handle_payload = {
        "expression": "() => window.ready === true",
        "arg": {"expected": True},
    }

    await BrowserNavigateNode(
        name="navigate",
        url="https://example.com",
    )(state, config)

    function_wait = BrowserWaitNode(
        name="wait_function",
        wait_for="function",
        expression="() => window.ready === true",
        arg={"expected": True},
    )
    function_payload = (await function_wait(state, config))["results"]["wait_function"]
    assert function_payload["value"] == {
        "expression": "() => window.ready === true",
        "arg": {"expected": True},
    }
    assert fake_browser_runtime.page.function_handle_disposed is True


@pytest.mark.asyncio
async def test_browser_sessions_are_scoped_by_thread_id(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """The default browser session id should be isolated per workflow run."""

    state = State({"results": {}})
    config_one = RunnableConfig(configurable={"thread_id": "exec-a"})
    config_two = RunnableConfig(configurable={"thread_id": "exec-b"})

    await BrowserNavigateNode(name="nav_a", url="https://example.com/a")(
        state, config_one
    )
    await BrowserNavigateNode(name="nav_b", url="https://example.com/b")(
        state, config_two
    )

    assert len(fake_browser_runtime.chromium.launch_calls) == 2


@pytest.mark.asyncio
async def test_browser_session_manager_closes_all_sessions_for_scope(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Closing one scope should tear down every associated browser session."""

    del fake_browser_runtime
    manager = browser_nodes.BrowserSessionManager()
    config = RunnableConfig(configurable={"thread_id": "exec-scope"})

    first, _ = await manager.get_or_create(
        key=browser_nodes._session_key("browser", config),
        browser_type="chromium",
        headless=True,
        launch_args=[],
        viewport_width=None,
        viewport_height=None,
        user_agent=None,
        locale=None,
        timezone_id=None,
        storage_state=None,
        extra_http_headers=None,
        ignore_https_errors=False,
        java_script_enabled=True,
        trace_path=None,
    )
    second, _ = await manager.get_or_create(
        key=browser_nodes._session_key("browser-2", config),
        browser_type="chromium",
        headless=True,
        launch_args=[],
        viewport_width=None,
        viewport_height=None,
        user_agent=None,
        locale=None,
        timezone_id=None,
        storage_state=None,
        extra_http_headers=None,
        ignore_https_errors=False,
        java_script_enabled=True,
        trace_path=None,
    )

    closed = await manager.close_scope("exec-scope")

    assert closed == 2
    assert first.context.closed is True
    assert second.context.closed is True


@pytest.mark.asyncio
async def test_browser_nodes_raise_when_session_is_missing() -> None:
    """Browser nodes should fail fast when no matching session exists."""

    state = State({"results": {}})
    action = BrowserActionNode(
        name="action",
        action="click",
        locator="#missing",
    )

    with pytest.raises(ValueError, match="BrowserNavigateNode first"):
        await action(state, RunnableConfig(configurable={"thread_id": "exec-404"}))


def test_configure_playwright_browser_path_uses_fallback_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Browser nodes should reuse a shared browser cache when HOME differs."""

    fallback_root = Path("/data/home/.cache/ms-playwright")
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.setattr(
        browser_nodes,
        "_playwright_browser_root_candidates",
        lambda: (Path("/missing"), fallback_root),
    )
    monkeypatch.setattr(
        browser_nodes,
        "_contains_playwright_browser_installation",
        lambda root, browser_type: root == fallback_root and browser_type == "chromium",
    )

    browser_nodes._configure_playwright_browser_path("chromium")

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(fallback_root)


def test_configure_playwright_browser_path_keeps_valid_existing_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An existing valid Playwright browser cache should not be overwritten."""

    configured_root = Path("/ms-playwright")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(configured_root))
    monkeypatch.setattr(
        browser_nodes,
        "_contains_playwright_browser_installation",
        lambda root, browser_type: (
            root == configured_root and browser_type == "chromium"
        ),
    )

    browser_nodes._configure_playwright_browser_path("chromium")

    assert os.environ["PLAYWRIGHT_BROWSERS_PATH"] == str(configured_root)


def test_playwright_browser_root_candidates_dedups_and_prepends_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The candidate list must honour the env var and deduplicate entries."""

    env_root = tmp_path / "env-root"
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(env_root))
    monkeypatch.setattr(
        browser_nodes,
        "_PLAYWRIGHT_BROWSER_ROOT_CANDIDATES",
        (env_root, env_root, Path("/other")),
    )

    candidates = browser_nodes._playwright_browser_root_candidates()

    assert candidates[0] == env_root
    assert candidates.count(env_root) == 1
    assert Path("/other") in candidates


def test_contains_playwright_browser_installation_non_directory(
    tmp_path: Path,
) -> None:
    """A non-directory path never contains Playwright browsers."""

    missing = tmp_path / "missing"
    assert (
        browser_nodes._contains_playwright_browser_installation(missing, "chromium")
        is False
    )


def test_contains_playwright_browser_installation_with_matching_subdir(
    tmp_path: Path,
) -> None:
    """A directory with a known browser prefix subdir is a valid install."""

    (tmp_path / "chromium-1234").mkdir()
    assert (
        browser_nodes._contains_playwright_browser_installation(tmp_path, "chromium")
        is True
    )
    assert (
        browser_nodes._contains_playwright_browser_installation(tmp_path, "firefox")
        is False
    )


def test_async_playwright_factory_returns_playwright_factory() -> None:
    """The factory helper must return the real Playwright entry point."""

    from playwright.async_api import async_playwright

    assert browser_nodes._async_playwright_factory() is async_playwright


def test_configurable_mapping_with_non_dict_returns_empty_dict() -> None:
    """Non-dict configurable payloads collapse to an empty mapping."""

    config = RunnableConfig(configurable="not-a-dict")  # type: ignore[typeddict-item]
    assert browser_nodes._configurable_mapping(config) == {}


def test_session_scope_prefers_top_level_run_id_when_configurable_missing() -> None:
    """Top-level run_id is used as the scope when configurable has none."""

    config = RunnableConfig(run_id="top-run")
    assert browser_nodes._session_scope(config) == "top-run"


def test_session_scope_returns_none_when_no_identifiers() -> None:
    """When no valid thread/run id exists the scope is None."""

    config = RunnableConfig(configurable={"thread_id": "   "})
    assert browser_nodes._session_scope(config) is None


def test_session_scope_skips_non_string_thread_id_and_returns_run_id() -> None:
    """Non-string thread ids fall through to the run_id lookup."""

    config = RunnableConfig(configurable={"thread_id": 42, "run_id": "alt-run"})  # type: ignore[typeddict-item]
    assert browser_nodes._session_scope(config) == "alt-run"


def test_session_key_without_scope_returns_raw_session_id() -> None:
    """Without any scope, the session id is returned verbatim."""

    config = RunnableConfig(configurable={})
    assert browser_nodes._session_key("browser", config) == "browser"


def test_page_timeout_kwargs_none_returns_empty_dict() -> None:
    """A None timeout maps to an empty kwargs dict."""

    assert browser_nodes._page_timeout_kwargs(None) == {}


def test_maybe_sequence_converts_tuples_to_lists() -> None:
    """Tuples are coerced to lists while other types pass through unchanged."""

    assert browser_nodes._maybe_sequence(("a", "b")) == ["a", "b"]
    assert browser_nodes._maybe_sequence("plain") == "plain"
    assert browser_nodes._maybe_sequence(None) is None


def _session_stub(
    browser_type: browser_nodes.BrowserEngine,
) -> browser_nodes.BrowserSession:
    """Return a minimal BrowserSession that never touches real Playwright objects."""

    return browser_nodes.BrowserSession(
        browser_type=browser_type,
        context=object(),
        browser=object(),
        page=object(),
        playwright=object(),
    )


def test_validate_existing_session_rejects_mismatched_engine() -> None:
    """Reusing a session with a different browser engine raises ValueError."""

    session = _session_stub("chromium")
    with pytest.raises(ValueError, match="already exists with engine"):
        browser_nodes.BrowserSessionManager._validate_existing_session(
            session, key="k", browser_type="firefox"
        )


def test_validate_existing_session_returns_none_for_missing_session() -> None:
    """A missing session remains None for the caller."""

    assert (
        browser_nodes.BrowserSessionManager._validate_existing_session(
            None, key="k", browser_type="chromium"
        )
        is None
    )


@pytest.mark.asyncio
async def test_create_session_raises_for_unsupported_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown Playwright engines must fail fast during session creation."""

    class _NoLauncher:
        async def stop(self) -> None:
            return None

    class _Ctx:
        async def start(self) -> _NoLauncher:
            return _NoLauncher()

    monkeypatch.setattr(
        browser_nodes,
        "_async_playwright_factory",
        lambda: lambda: _Ctx(),
    )
    monkeypatch.setattr(
        browser_nodes,
        "_configure_playwright_browser_path",
        lambda _browser_type: None,
    )

    manager = browser_nodes.BrowserSessionManager()
    with pytest.raises(ValueError, match="Unsupported browser engine"):
        await manager._create_session(
            browser_type="chromium",
            headless=True,
            launch_args=[],
            viewport_width=None,
            viewport_height=None,
            user_agent=None,
            locale=None,
            timezone_id=None,
            storage_state=None,
            extra_http_headers=None,
            ignore_https_errors=False,
            java_script_enabled=True,
            trace_path=None,
        )


@pytest.mark.asyncio
async def test_get_or_create_fast_path_returns_existing_session(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """A second navigate call with the same key hits the no-lock fast path."""

    del fake_browser_runtime
    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-reuse"})
    first = await BrowserNavigateNode(name="n1", url="https://example.com/1")(
        state, config
    )
    second = await BrowserNavigateNode(name="n2", url="https://example.com/2")(
        state, config
    )
    assert first["results"]["n1"]["created_session"] is True
    assert second["results"]["n2"]["created_session"] is False


@pytest.mark.asyncio
async def test_get_or_create_race_condition_returns_existing_in_lock(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """The in-lock re-check returns an existing session without recreating."""

    del fake_browser_runtime
    manager = browser_nodes.BrowserSessionManager()
    placeholder = _session_stub("chromium")
    call_count = 0

    def fake_validate(
        session: browser_nodes.BrowserSession | None,
        *,
        key: str,
        browser_type: browser_nodes.BrowserEngine,
    ) -> browser_nodes.BrowserSession | None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return None
        return placeholder

    manager._validate_existing_session = fake_validate  # type: ignore[method-assign]

    session, created = await manager.get_or_create(
        key="k",
        browser_type="chromium",
        headless=True,
        launch_args=[],
        viewport_width=None,
        viewport_height=None,
        user_agent=None,
        locale=None,
        timezone_id=None,
        storage_state=None,
        extra_http_headers=None,
        ignore_https_errors=False,
        java_script_enabled=True,
        trace_path=None,
    )
    assert session is placeholder
    assert created is False


@pytest.mark.asyncio
async def test_close_returns_false_when_no_session_exists() -> None:
    """Closing a missing session should report False and not error."""

    manager = browser_nodes.BrowserSessionManager()
    assert await manager.close("missing") is False


@pytest.mark.asyncio
async def test_close_scope_with_empty_scope_returns_zero() -> None:
    """An empty scope is a no-op and returns zero sessions closed."""

    manager = browser_nodes.BrowserSessionManager()
    assert await manager.close_scope("") == 0


@pytest.mark.asyncio
async def test_close_session_stops_tracing_without_path(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Tracing is stopped with no path when neither override nor session has one."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-trace"})
    # Start tracing by enabling trace_path, then close without any override path.
    await BrowserNavigateNode(
        name="navigate",
        url="https://example.com",
        trace_path=None,
    )(state, config)
    # Manually flip the session to simulate tracing started without a saved path.
    resolved_key = browser_nodes._session_key("browser", config)
    session = await browser_nodes._browser_session_manager.get(resolved_key)
    assert session is not None
    session.tracing_started = True
    session.trace_path = None

    closed = await browser_nodes._browser_session_manager.close(resolved_key)
    assert closed is True
    assert fake_browser_runtime.context.tracing.stop_path is None


@pytest.mark.asyncio
async def test_close_browser_sessions_for_scope_delegates_to_manager(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """The module-level helper should close all sessions under a scope."""

    del fake_browser_runtime
    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-helper"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)

    closed = await browser_nodes.close_browser_sessions_for_scope("exec-helper")
    assert closed == 1


@pytest.mark.asyncio
async def test_navigate_tolerates_none_response(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """page.goto returning None must surface as a null response payload."""

    fake_browser_runtime.page.goto_returns_none = True  # type: ignore[attr-defined]
    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-none"})
    payload = (
        await BrowserNavigateNode(name="navigate", url="about:blank")(state, config)
    )["results"]["navigate"]
    assert payload["response"] is None


@pytest.mark.asyncio
async def test_browser_action_node_exercises_all_actions(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Every supported action routes through its locator counterpart."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-actions"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)
    page = fake_browser_runtime.page

    await BrowserActionNode(name="click", action="click", locator="#btn")(state, config)
    await BrowserActionNode(
        name="press", action="press", locator="#input", value="Enter"
    )(state, config)
    await BrowserActionNode(name="check", action="check", locator="#chk")(state, config)
    await BrowserActionNode(name="uncheck", action="uncheck", locator="#chk")(
        state, config
    )
    await BrowserActionNode(name="hover", action="hover", locator="#hv")(state, config)
    await BrowserActionNode(
        name="select",
        action="select_option",
        locator="#sel",
        value=("a", "b"),
    )(state, config)
    await BrowserActionNode(
        name="upload",
        action="set_input_files",
        locator="#file",
        value=("/tmp/a.txt", "/tmp/b.txt"),
    )(state, config)

    recorded_actions = [record[0] for record in page.actions]
    assert recorded_actions == [
        "click",
        "press",
        "check",
        "uncheck",
        "hover",
        "select_option",
        "set_input_files",
    ]
    select_record = next(r for r in page.actions if r[0] == "select_option")
    upload_record = next(r for r in page.actions if r[0] == "set_input_files")
    assert select_record[2] == ["a", "b"]
    assert upload_record[2] == ["/tmp/a.txt", "/tmp/b.txt"]


@pytest.mark.asyncio
async def test_browser_action_node_validates_required_values(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Actions that need a value raise when one is not provided."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-action-errors"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)

    fill_missing = BrowserActionNode(
        name="fill_missing", action="fill", locator="#input", value=None
    )
    with pytest.raises(ValueError, match="fill requires a string"):
        await fill_missing(state, config)

    press_missing = BrowserActionNode(
        name="press_missing", action="press", locator="#input", value=None
    )
    with pytest.raises(ValueError, match="press requires a string"):
        await press_missing(state, config)

    select_missing = BrowserActionNode(
        name="select_missing",
        action="select_option",
        locator="#sel",
        value=None,
    )
    with pytest.raises(ValueError, match="select_option requires"):
        await select_missing(state, config)

    upload_missing = BrowserActionNode(
        name="upload_missing",
        action="set_input_files",
        locator="#file",
        value=None,
    )
    with pytest.raises(ValueError, match="set_input_files requires"):
        await upload_missing(state, config)


@pytest.mark.asyncio
async def test_browser_extract_node_supports_every_mode(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Every extraction mode produces the expected structured payload."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-extracts"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)

    title_payload = (
        await BrowserExtractNode(name="title", mode="title")(state, config)
    )["results"]["title"]
    assert title_payload["value"] == "Title for https://example.com"

    url_payload = (await BrowserExtractNode(name="url", mode="url")(state, config))[
        "results"
    ]["url"]
    assert url_payload["value"] == "https://example.com"

    content_payload = (
        await BrowserExtractNode(name="content", mode="page_content")(state, config)
    )["results"]["content"]
    assert content_payload["value"] == fake_browser_runtime.page.page_content

    text_payload = (
        await BrowserExtractNode(name="text", mode="text", locator="#result")(
            state, config
        )
    )["results"]["text"]
    assert text_payload["value"] == "Orcheo"

    all_text_payload = (
        await BrowserExtractNode(name="all_text", mode="all_text", locator="li.item")(
            state, config
        )
    )["results"]["all_text"]
    assert all_text_payload["value"] == ["one", "two"]

    html_payload = (
        await BrowserExtractNode(name="html", mode="html", locator="#result")(
            state, config
        )
    )["results"]["html"]
    assert html_payload["value"] == "<span>Orcheo</span>"


@pytest.mark.asyncio
async def test_browser_extract_node_validates_locator_and_attribute(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Element-based extraction modes require locator and attribute arguments."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-extract-errors"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)

    missing_locator = BrowserExtractNode(name="bad", mode="text", locator="   ")
    with pytest.raises(ValueError, match="requires a locator"):
        await missing_locator(state, config)

    missing_attr = BrowserExtractNode(
        name="bad_attr", mode="attribute", locator="#result", attribute_name=None
    )
    with pytest.raises(ValueError, match="requires attribute_name"):
        await missing_attr(state, config)


@pytest.mark.asyncio
async def test_browser_wait_node_supports_url_load_state_and_timeout(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """URL, load_state, and timeout waits delegate to Playwright accordingly."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-waits"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)
    page = fake_browser_runtime.page

    url_payload = (
        await BrowserWaitNode(
            name="wait_url",
            wait_for="url",
            url_pattern="**/dashboard",
            wait_until="load",
        )(state, config)
    )["results"]["wait_url"]
    assert url_payload["value"] == {"url_pattern": "**/dashboard"}
    assert any(
        call[0] == "url" and call[2].get("wait_until") == "load"
        for call in page.wait_calls
    )

    url_no_wait_until = (
        await BrowserWaitNode(
            name="wait_url_plain",
            wait_for="url",
            url_pattern="**/home",
        )(state, config)
    )["results"]["wait_url_plain"]
    assert url_no_wait_until["value"] == {"url_pattern": "**/home"}
    plain_calls = [c for c in page.wait_calls if c[0] == "url" and c[1] == "**/home"]
    assert plain_calls and "wait_until" not in plain_calls[-1][2]

    load_state_payload = (
        await BrowserWaitNode(
            name="wait_load", wait_for="load_state", load_state="networkidle"
        )(state, config)
    )["results"]["wait_load"]
    assert load_state_payload["value"] == {"load_state": "networkidle"}

    timeout_payload = (
        await BrowserWaitNode(name="wait_timeout", wait_for="timeout", timeout_ms=12.5)(
            state, config
        )
    )["results"]["wait_timeout"]
    assert timeout_payload["value"] == {"timeout_ms": 12.5}


@pytest.mark.asyncio
async def test_browser_wait_node_validates_inputs(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Wait nodes must raise when their required inputs are missing or blank."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-wait-errors"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)

    with pytest.raises(ValueError, match="selector requires a locator"):
        await BrowserWaitNode(name="bad_selector", wait_for="selector", locator="   ")(
            state, config
        )

    with pytest.raises(ValueError, match="url requires url_pattern"):
        await BrowserWaitNode(name="bad_url", wait_for="url", url_pattern=" ")(
            state, config
        )

    with pytest.raises(ValueError, match="function requires expression"):
        await BrowserWaitNode(name="bad_fn", wait_for="function", expression=None)(
            state, config
        )

    with pytest.raises(ValueError, match="timeout requires timeout_ms"):
        await BrowserWaitNode(name="bad_timeout", wait_for="timeout", timeout_ms=None)(
            state, config
        )


@pytest.mark.asyncio
async def test_browser_wait_response_predicate_matches_and_rejects(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Response predicate honours exact URL matching, status, and mismatches."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-response"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)

    exact_node = BrowserWaitNode(
        name="exact_node",
        wait_for="response",
        response_url="https://example.com/api/ready",
        response_match_mode="exact",
    )
    assert exact_node._response_matches(fake_browser_runtime.page.response_to_wait)

    status_none_node = BrowserWaitNode(
        name="any_status",
        wait_for="response",
        response_url="https://example.com/api/ready",
        response_match_mode="exact",
        response_status=None,
    )
    assert status_none_node._response_matches(
        fake_browser_runtime.page.response_to_wait
    )

    mismatch_node = BrowserWaitNode(
        name="mismatch",
        wait_for="response",
        response_url="https://example.com/other",
        response_match_mode="exact",
    )
    assert (
        mismatch_node._response_matches(fake_browser_runtime.page.response_to_wait)
        is False
    )

    missing_url = BrowserWaitNode(
        name="missing",
        wait_for="response",
        response_url="   ",
    )
    with pytest.raises(ValueError, match="response requires response_url"):
        missing_url._response_matches(fake_browser_runtime.page.response_to_wait)


@pytest.mark.asyncio
async def test_browser_script_node_without_arg(
    fake_browser_runtime: FakePlaywright,
) -> None:
    """Scripts with no argument evaluate the expression on its own."""

    state = State({"results": {}})
    config = RunnableConfig(configurable={"thread_id": "exec-script-noarg"})
    await BrowserNavigateNode(name="navigate", url="https://example.com")(state, config)

    payload = (
        await BrowserScriptNode(name="script", script="() => 42")(state, config)
    )["results"]["script"]
    assert payload["value"] == {"script": "() => 42", "arg": None}
    assert fake_browser_runtime.page.evaluate_calls[-1] == ("() => 42", None)
