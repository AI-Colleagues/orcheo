"""Playwright-backed browser automation nodes."""

from __future__ import annotations
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from langchain_core.runnables import RunnableConfig
from pydantic import Field
from orcheo.graph.state import State
from orcheo.nodes.base import TaskNode
from orcheo.nodes.registry import NodeMetadata, registry


BrowserEngine = Literal["chromium", "firefox", "webkit"]
NavigationWaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]
LocatorWaitState = Literal["attached", "detached", "hidden", "visible"]
LoadState = Literal["domcontentloaded", "load", "networkidle"]
BrowserAction = Literal[
    "check",
    "click",
    "fill",
    "hover",
    "press",
    "select_option",
    "set_input_files",
    "uncheck",
]
BrowserExtractMode = Literal[
    "all_text",
    "attribute",
    "html",
    "page_content",
    "text",
    "title",
    "url",
]
BrowserWaitFor = Literal[
    "function",
    "load_state",
    "response",
    "selector",
    "timeout",
    "url",
]
ResponseMatchMode = Literal["contains", "exact"]
_PLAYWRIGHT_BROWSERS_PATH_ENV = "PLAYWRIGHT_BROWSERS_PATH"
_PLAYWRIGHT_BROWSER_PREFIXES: dict[BrowserEngine, tuple[str, ...]] = {
    "chromium": ("chromium-", "chromium_headless_shell-"),
    "firefox": ("firefox-",),
    "webkit": ("webkit-",),
}
_PLAYWRIGHT_BROWSER_ROOT_CANDIDATES = (
    Path("/ms-playwright"),
    Path("/data/home/.cache/ms-playwright"),
    Path("/home/orcheo/.cache/ms-playwright"),
)


def _playwright_browser_root_candidates() -> tuple[Path, ...]:
    """Return browser cache roots that may contain installed Playwright browsers."""
    candidates: list[Path] = []
    configured_root = os.environ.get(_PLAYWRIGHT_BROWSERS_PATH_ENV)
    if configured_root:
        candidates.append(Path(configured_root).expanduser())
    candidates.append(Path.home() / ".cache" / "ms-playwright")
    candidates.extend(_PLAYWRIGHT_BROWSER_ROOT_CANDIDATES)

    unique_candidates: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique_candidates.append(candidate)
    return tuple(unique_candidates)


def _contains_playwright_browser_installation(
    root: Path,
    browser_type: BrowserEngine,
) -> bool:
    """Return whether ``root`` contains browser binaries for ``browser_type``."""
    if not root.is_dir():
        return False
    prefixes = _PLAYWRIGHT_BROWSER_PREFIXES[browser_type]
    return any(
        child.is_dir() and child.name.startswith(prefix)
        for prefix in prefixes
        for child in root.iterdir()
    )


def _configure_playwright_browser_path(browser_type: BrowserEngine) -> None:
    """Point Playwright at an existing shared browser cache when available."""
    configured_root = os.environ.get(_PLAYWRIGHT_BROWSERS_PATH_ENV)
    if configured_root and _contains_playwright_browser_installation(
        Path(configured_root).expanduser(),
        browser_type,
    ):
        return

    for candidate in _playwright_browser_root_candidates():
        if _contains_playwright_browser_installation(candidate, browser_type):
            os.environ[_PLAYWRIGHT_BROWSERS_PATH_ENV] = str(candidate)
            return


def _async_playwright_factory() -> Any:
    """Return the Playwright async factory lazily for easier testing."""
    from playwright.async_api import async_playwright

    return async_playwright


def _configurable_mapping(config: RunnableConfig) -> dict[str, Any]:
    """Return the serializable configurable mapping for a run."""
    configurable = config.get("configurable", {})
    if isinstance(configurable, dict):
        return configurable
    return {}


def _session_scope(config: RunnableConfig) -> str | None:
    """Return a run-scoped session namespace when present."""
    configurable = _configurable_mapping(config)
    for key in ("thread_id", "run_id"):
        value = configurable.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    run_id = config.get("run_id")
    if isinstance(run_id, str) and run_id.strip():
        return run_id.strip()
    return None


def _session_key(session_id: str, config: RunnableConfig) -> str:
    """Return the internal browser session key for one workflow run."""
    scope = _session_scope(config)
    if scope is None:
        return session_id
    return f"{scope}:{session_id}"


def _page_timeout_kwargs(timeout_ms: float | None) -> dict[str, float]:
    """Return Playwright timeout kwargs when a timeout was configured."""
    if timeout_ms is None:
        return {}
    return {"timeout": timeout_ms}


def _maybe_sequence(value: Any) -> Any:
    """Return a sequence-friendly value for Playwright passthrough APIs."""
    if isinstance(value, tuple):
        return list(value)
    return value


@dataclass(slots=True)
class BrowserSession:
    """Runtime objects kept alive across browser nodes in one workflow run."""

    browser_type: BrowserEngine
    context: Any
    browser: Any | None
    page: Any
    playwright: Any
    trace_path: str | None = None
    tracing_started: bool = False


class BrowserSessionManager:
    """Manage Playwright browser sessions for workflow runs."""

    def __init__(self) -> None:
        """Initialize the in-memory session registry."""
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _validate_existing_session(
        session: BrowserSession | None,
        *,
        key: str,
        browser_type: BrowserEngine,
    ) -> BrowserSession | None:
        """Return an existing session or raise on incompatible browser reuse."""
        if session is None:
            return None
        if session.browser_type != browser_type:
            msg = (
                f"Browser session '{key}' already exists with engine "
                f"{session.browser_type!r}, not {browser_type!r}."
            )
            raise ValueError(msg)
        return session

    @staticmethod
    def _context_kwargs(
        *,
        viewport_width: int | None,
        viewport_height: int | None,
        user_agent: str | None,
        locale: str | None,
        timezone_id: str | None,
        storage_state: dict[str, Any] | str | None,
        extra_http_headers: dict[str, str] | None,
        ignore_https_errors: bool,
        java_script_enabled: bool,
    ) -> dict[str, Any]:
        """Build Playwright browser context keyword arguments."""
        context_kwargs: dict[str, Any] = {
            "ignore_https_errors": ignore_https_errors,
            "java_script_enabled": java_script_enabled,
        }
        if viewport_width is not None and viewport_height is not None:
            context_kwargs["viewport"] = {
                "width": viewport_width,
                "height": viewport_height,
            }
        optional_values = {
            "user_agent": user_agent,
            "locale": locale,
            "timezone_id": timezone_id,
            "storage_state": storage_state,
            "extra_http_headers": extra_http_headers,
        }
        for field_name, value in optional_values.items():
            if value is not None:
                context_kwargs[field_name] = value
        return context_kwargs

    async def _create_session(
        self,
        *,
        browser_type: BrowserEngine,
        headless: bool,
        launch_args: list[str],
        user_data_dir: str | None,
        viewport_width: int | None,
        viewport_height: int | None,
        user_agent: str | None,
        locale: str | None,
        timezone_id: str | None,
        storage_state: dict[str, Any] | str | None,
        extra_http_headers: dict[str, str] | None,
        ignore_https_errors: bool,
        java_script_enabled: bool,
        trace_path: str | None,
    ) -> BrowserSession:
        """Create one Playwright session with a single active page."""
        _configure_playwright_browser_path(browser_type)
        playwright_context = _async_playwright_factory()()
        playwright = await playwright_context.start()
        launcher = getattr(playwright, browser_type, None)
        if launcher is None:
            msg = f"Unsupported browser engine {browser_type!r}."
            raise ValueError(msg)

        launch_kwargs: dict[str, Any] = {"headless": headless}
        if launch_args:
            launch_kwargs["args"] = launch_args
        context_kwargs = self._context_kwargs(
            viewport_width=viewport_width,
            viewport_height=viewport_height,
            user_agent=user_agent,
            locale=locale,
            timezone_id=timezone_id,
            storage_state=storage_state,
            extra_http_headers=extra_http_headers,
            ignore_https_errors=ignore_https_errors,
            java_script_enabled=java_script_enabled,
        )
        browser = None
        if user_data_dir:
            if storage_state is not None:
                msg = (
                    "BrowserNavigateNode.user_data_dir cannot be used with "
                    "storage_state."
                )
                raise ValueError(msg)
            context_kwargs.pop("storage_state", None)
            context = await launcher.launch_persistent_context(
                user_data_dir,
                **launch_kwargs,
                **context_kwargs,
            )
        else:
            browser = await launcher.launch(**launch_kwargs)
            context = await browser.new_context(**context_kwargs)
        tracing_started = trace_path is not None
        if tracing_started:
            await context.tracing.start(
                screenshots=True,
                snapshots=True,
                sources=True,
            )
        page = await context.new_page()
        return BrowserSession(
            browser_type=browser_type,
            context=context,
            browser=browser,
            page=page,
            playwright=playwright,
            trace_path=trace_path,
            tracing_started=tracing_started,
        )

    async def get(self, key: str) -> BrowserSession | None:
        """Return an existing session when available."""
        return self._sessions.get(key)

    async def get_or_create(
        self,
        *,
        key: str,
        browser_type: BrowserEngine,
        headless: bool,
        launch_args: list[str],
        user_data_dir: str | None,
        viewport_width: int | None,
        viewport_height: int | None,
        user_agent: str | None,
        locale: str | None,
        timezone_id: str | None,
        storage_state: dict[str, Any] | str | None,
        extra_http_headers: dict[str, str] | None,
        ignore_https_errors: bool,
        java_script_enabled: bool,
        trace_path: str | None,
    ) -> tuple[BrowserSession, bool]:
        """Return an existing session or create a new Playwright browser."""
        existing = self._validate_existing_session(
            self._sessions.get(key),
            key=key,
            browser_type=browser_type,
        )
        if existing is not None:
            return existing, False

        async with self._lock:
            existing = self._validate_existing_session(
                self._sessions.get(key),
                key=key,
                browser_type=browser_type,
            )
            if existing is not None:
                return existing, False

            session = await self._create_session(
                browser_type=browser_type,
                headless=headless,
                launch_args=launch_args,
                user_data_dir=user_data_dir,
                viewport_width=viewport_width,
                viewport_height=viewport_height,
                user_agent=user_agent,
                locale=locale,
                timezone_id=timezone_id,
                storage_state=storage_state,
                extra_http_headers=extra_http_headers,
                ignore_https_errors=ignore_https_errors,
                java_script_enabled=java_script_enabled,
                trace_path=trace_path,
            )
            self._sessions[key] = session
            return session, True

    async def close(self, key: str, trace_path: str | None = None) -> bool:
        """Close and remove a session when present."""
        async with self._lock:
            session = self._sessions.pop(key, None)
        if session is None:
            return False
        await self._close_session(session, trace_path=trace_path)
        return True

    async def close_scope(self, scope: str) -> int:
        """Close every session registered for one workflow run scope."""
        if not scope:
            return 0
        scope_prefix = f"{scope}:"
        async with self._lock:
            scoped_sessions = [
                self._sessions.pop(key)
                for key in tuple(self._sessions)
                if key == scope or key.startswith(scope_prefix)
            ]
        for session in scoped_sessions:
            await self._close_session(session, trace_path=None)
        return len(scoped_sessions)

    @staticmethod
    async def _close_session(
        session: BrowserSession,
        *,
        trace_path: str | None,
    ) -> None:
        """Close all Playwright runtime resources for one browser session."""
        if session.tracing_started:
            final_trace_path = trace_path or session.trace_path
            if final_trace_path is not None:
                await session.context.tracing.stop(path=final_trace_path)
            else:
                await session.context.tracing.stop()
        await session.context.close()
        if session.browser is not None:
            await session.browser.close()
        await session.playwright.stop()


_browser_session_manager = BrowserSessionManager()


async def close_browser_sessions_for_scope(scope: str) -> int:
    """Close all active browser sessions for one workflow run scope."""
    return await _browser_session_manager.close_scope(scope)


class BrowserNode(TaskNode):
    """Base node for operations against a Playwright page session."""

    session_id: str = Field(
        default="browser",
        description="Logical browser session identifier within one workflow run.",
    )

    def _resolved_session_key(self, config: RunnableConfig) -> str:
        """Return the run-scoped internal session key."""
        return _session_key(self.session_id, config)

    async def _require_session(
        self, config: RunnableConfig
    ) -> tuple[str, BrowserSession]:
        """Return an existing session or raise when none exists."""
        resolved_key = self._resolved_session_key(config)
        session = await _browser_session_manager.get(resolved_key)
        if session is None:
            msg = (
                f"Browser session '{self.session_id}' is not active for this run. "
                "Start it with BrowserNavigateNode first."
            )
            raise ValueError(msg)
        return resolved_key, session

    async def _page_metadata(
        self,
        *,
        resolved_key: str,
        session: BrowserSession,
    ) -> dict[str, Any]:
        """Return normalized page metadata for node outputs."""
        return {
            "session_id": self.session_id,
            "resolved_session_key": resolved_key,
            "browser_type": session.browser_type,
            "url": session.page.url,
            "title": await session.page.title(),
        }


@registry.register(
    NodeMetadata(
        name="BrowserNavigateNode",
        description="Open a URL in a shared Playwright browser session.",
        category="browser",
    )
)
class BrowserNavigateNode(BrowserNode):
    """Navigate a Playwright page to a URL."""

    browser_type: BrowserEngine = Field(
        default="chromium",
        description="Playwright browser engine used for the session.",
    )
    headless: bool = Field(
        default=True,
        description="Launch the browser without a visible window.",
    )
    launch_args: list[str] = Field(
        default_factory=list,
        description="Optional extra browser launch arguments.",
    )
    user_data_dir: str | None = Field(
        default=None,
        description=(
            "Optional persistent browser profile directory. Uses "
            "Playwright launch_persistent_context()."
        ),
    )
    url: str = Field(description="Target URL to open in the browser.")
    wait_until: NavigationWaitUntil = Field(
        default="load",
        description="Navigation readiness event to wait for before returning.",
    )
    timeout_ms: float | None = Field(
        default=30000.0,
        ge=0.0,
        description="Maximum navigation time in milliseconds.",
    )
    referer: str | None = Field(
        default=None,
        description="Optional Referer header used for navigation.",
    )
    viewport_width: int | None = Field(
        default=None,
        ge=1,
        description="Optional viewport width used when creating a new session.",
    )
    viewport_height: int | None = Field(
        default=None,
        ge=1,
        description="Optional viewport height used when creating a new session.",
    )
    user_agent: str | None = Field(
        default=None,
        description="Optional browser user agent for a new session.",
    )
    locale: str | None = Field(
        default=None,
        description="Optional browser locale for a new session.",
    )
    timezone_id: str | None = Field(
        default=None,
        description="Optional timezone override for a new session.",
    )
    storage_state: dict[str, Any] | str | None = Field(
        default=None,
        description="Playwright storage state payload or path for a new session.",
    )
    extra_http_headers: dict[str, str] | None = Field(
        default=None,
        description="Optional HTTP headers applied to all requests in a new session.",
    )
    ignore_https_errors: bool = Field(
        default=False,
        description="Ignore TLS certificate errors when opening pages.",
    )
    java_script_enabled: bool = Field(
        default=True,
        description="Enable JavaScript execution in the browser context.",
    )
    trace_path: str | None = Field(
        default=None,
        description="Optional trace archive path recorded for the session.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Create or reuse a browser session and navigate to the configured URL."""
        del state
        resolved_key = self._resolved_session_key(config)
        session, created_session = await _browser_session_manager.get_or_create(
            key=resolved_key,
            browser_type=self.browser_type,
            headless=self.headless,
            launch_args=self.launch_args,
            user_data_dir=self.user_data_dir,
            viewport_width=self.viewport_width,
            viewport_height=self.viewport_height,
            user_agent=self.user_agent,
            locale=self.locale,
            timezone_id=self.timezone_id,
            storage_state=self.storage_state,
            extra_http_headers=self.extra_http_headers,
            ignore_https_errors=self.ignore_https_errors,
            java_script_enabled=self.java_script_enabled,
            trace_path=self.trace_path,
        )
        response = await session.page.goto(
            self.url,
            wait_until=self.wait_until,
            referer=self.referer,
            **_page_timeout_kwargs(self.timeout_ms),
        )
        response_payload: dict[str, Any] | None = None
        if response is not None:
            response_payload = {
                "status": response.status,
                "ok": response.ok,
                "url": response.url,
                "headers": dict(response.headers),
            }
        payload = await self._page_metadata(resolved_key=resolved_key, session=session)
        payload["created_session"] = created_session
        payload["response"] = response_payload
        return payload


@registry.register(
    NodeMetadata(
        name="BrowserActionNode",
        description="Perform a user-style action against the active Playwright page.",
        category="browser",
    )
)
class BrowserActionNode(BrowserNode):
    """Run one browser action against a locator."""

    action: BrowserAction = Field(description="Browser action to perform.")
    locator: str = Field(description="Playwright locator string.")
    value: Any | None = Field(
        default=None,
        description=(
            "Optional value used by fill, press, select, or file upload actions."
        ),
    )
    timeout_ms: float | None = Field(
        default=30000.0,
        ge=0.0,
        description="Maximum action time in milliseconds.",
    )
    force: bool = Field(
        default=False,
        description="Force the action even when the target is not actionable.",
    )

    def _action_timeout_kwargs(self) -> dict[str, float]:
        """Return the configured action timeout kwargs."""
        return _page_timeout_kwargs(self.timeout_ms)

    async def _run_click(self, locator: Any) -> None:
        """Click the configured locator."""
        await locator.click(force=self.force, **self._action_timeout_kwargs())

    async def _run_fill(self, locator: Any) -> None:
        """Fill the configured locator with text."""
        if not isinstance(self.value, str):
            raise ValueError("BrowserActionNode.fill requires a string value.")
        await locator.fill(
            self.value,
            force=self.force,
            **self._action_timeout_kwargs(),
        )

    async def _run_press(self, locator: Any) -> None:
        """Press one keyboard shortcut on the configured locator."""
        if not isinstance(self.value, str):
            raise ValueError("BrowserActionNode.press requires a string value.")
        await locator.press(self.value, **self._action_timeout_kwargs())

    async def _run_check(self, locator: Any) -> None:
        """Check the configured checkbox or radio input."""
        await locator.check(force=self.force, **self._action_timeout_kwargs())

    async def _run_uncheck(self, locator: Any) -> None:
        """Uncheck the configured checkbox."""
        await locator.uncheck(force=self.force, **self._action_timeout_kwargs())

    async def _run_hover(self, locator: Any) -> None:
        """Hover over the configured locator."""
        await locator.hover(force=self.force, **self._action_timeout_kwargs())

    async def _run_select_option(self, locator: Any) -> None:
        """Select one or more values from the configured locator."""
        if self.value is None:
            raise ValueError(
                "BrowserActionNode.select_option requires a non-empty value."
            )
        await locator.select_option(
            _maybe_sequence(self.value),
            **self._action_timeout_kwargs(),
        )

    async def _run_set_input_files(self, locator: Any) -> None:
        """Upload files into the configured file input."""
        if self.value is None:
            raise ValueError(
                "BrowserActionNode.set_input_files requires a file path value."
            )
        await locator.set_input_files(
            _maybe_sequence(self.value),
            **self._action_timeout_kwargs(),
        )

    async def _perform_action(self, locator: Any) -> None:
        """Dispatch the configured locator action."""
        handlers = {
            "click": self._run_click,
            "fill": self._run_fill,
            "press": self._run_press,
            "check": self._run_check,
            "uncheck": self._run_uncheck,
            "hover": self._run_hover,
            "select_option": self._run_select_option,
            "set_input_files": self._run_set_input_files,
        }
        handler = handlers.get(self.action)
        if handler is None:  # pragma: no cover - Literal guards this in normal use.
            raise ValueError(f"Unsupported browser action {self.action!r}.")
        await handler(locator)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Perform the configured action against the current page."""
        del state
        resolved_key, session = await self._require_session(config)
        locator = session.page.locator(self.locator)
        await self._perform_action(locator)

        payload = await self._page_metadata(resolved_key=resolved_key, session=session)
        payload["action"] = self.action
        payload["locator"] = self.locator
        payload["value"] = self.value
        return payload


@registry.register(
    NodeMetadata(
        name="BrowserExtractNode",
        description="Extract page or element content from the active Playwright page.",
        category="browser",
    )
)
class BrowserExtractNode(BrowserNode):
    """Extract structured or textual data from the page."""

    mode: BrowserExtractMode = Field(description="Extraction mode to execute.")
    locator: str | None = Field(
        default=None,
        description="Optional Playwright locator required by element extraction modes.",
    )
    attribute_name: str | None = Field(
        default=None,
        description="Attribute name used by attribute extraction mode.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Extract content from the current page or one locator."""
        del state
        resolved_key, session = await self._require_session(config)
        extracted: Any

        if self.mode == "title":
            extracted = await session.page.title()
        elif self.mode == "url":
            extracted = session.page.url
        elif self.mode == "page_content":
            extracted = await session.page.content()
        else:
            if not isinstance(self.locator, str) or not self.locator.strip():
                raise ValueError(
                    f"BrowserExtractNode mode {self.mode!r} requires a locator."
                )
            target = session.page.locator(self.locator)
            if self.mode == "text":
                extracted = await target.inner_text()
            elif self.mode == "all_text":
                extracted = await target.all_inner_texts()
            elif self.mode == "html":
                extracted = await target.inner_html()
            elif self.mode == "attribute":
                if not isinstance(self.attribute_name, str) or not self.attribute_name:
                    raise ValueError(
                        "BrowserExtractNode.attribute requires attribute_name."
                    )
                extracted = await target.get_attribute(self.attribute_name)
            else:  # pragma: no cover - Literal guards this in normal execution.
                raise ValueError(f"Unsupported browser extract mode {self.mode!r}.")

        payload = await self._page_metadata(resolved_key=resolved_key, session=session)
        payload["mode"] = self.mode
        payload["locator"] = self.locator
        payload["attribute_name"] = self.attribute_name
        payload["value"] = extracted
        return payload


@registry.register(
    NodeMetadata(
        name="BrowserWaitNode",
        description="Wait for a condition on the active Playwright page.",
        category="browser",
    )
)
class BrowserWaitNode(BrowserNode):
    """Wait for a selector, URL, function, response, or timeout."""

    wait_for: BrowserWaitFor = Field(description="Condition type to wait for.")
    locator: str | None = Field(
        default=None,
        description="Playwright locator required for selector waits.",
    )
    selector_state: LocatorWaitState = Field(
        default="visible",
        description="Desired selector state for selector waits.",
    )
    url_pattern: str | None = Field(
        default=None,
        description="URL pattern used by URL waits.",
    )
    wait_until: NavigationWaitUntil | None = Field(
        default=None,
        description="Optional readiness state used by URL waits.",
    )
    load_state: LoadState = Field(
        default="load",
        description="Load state used by load_state waits.",
    )
    expression: str | None = Field(
        default=None,
        description="JavaScript expression used by function waits.",
    )
    arg: Any | None = Field(
        default=None,
        description="Optional argument passed to page.wait_for_function().",
    )
    timeout_ms: float | None = Field(
        default=30000.0,
        ge=0.0,
        description="Maximum wait time in milliseconds.",
    )
    response_url: str | None = Field(
        default=None,
        description="Response URL matcher used by response waits.",
    )
    response_status: int | None = Field(
        default=None,
        description="Optional HTTP status required for a matching response.",
    )
    response_match_mode: ResponseMatchMode = Field(
        default="exact",
        description="How response_url is matched against observed responses.",
    )

    def _wait_timeout_kwargs(self) -> dict[str, float]:
        """Return the configured wait timeout kwargs."""
        return _page_timeout_kwargs(self.timeout_ms)

    async def _wait_for_selector(self, session: BrowserSession) -> dict[str, Any]:
        """Wait for one locator to reach the requested state."""
        if not isinstance(self.locator, str) or not self.locator.strip():
            raise ValueError("BrowserWaitNode.selector requires a locator.")
        await session.page.locator(self.locator).wait_for(
            state=self.selector_state,
            **self._wait_timeout_kwargs(),
        )
        return {"locator": self.locator, "state": self.selector_state}

    async def _wait_for_url(self, session: BrowserSession) -> dict[str, Any]:
        """Wait for the page URL to match a pattern."""
        if not isinstance(self.url_pattern, str) or not self.url_pattern.strip():
            raise ValueError("BrowserWaitNode.url requires url_pattern.")
        wait_kwargs: dict[str, Any] = dict(self._wait_timeout_kwargs())
        if self.wait_until is not None:
            wait_kwargs["wait_until"] = self.wait_until
        await session.page.wait_for_url(self.url_pattern, **wait_kwargs)
        return {"url_pattern": self.url_pattern}

    async def _wait_for_load_state(self, session: BrowserSession) -> dict[str, Any]:
        """Wait for one page load state."""
        await session.page.wait_for_load_state(
            self.load_state,
            **self._wait_timeout_kwargs(),
        )
        return {"load_state": self.load_state}

    async def _wait_for_function(self, session: BrowserSession) -> Any:
        """Wait for a JavaScript condition to become truthy."""
        if not isinstance(self.expression, str) or not self.expression.strip():
            raise ValueError("BrowserWaitNode.function requires expression.")
        wait_result = await session.page.wait_for_function(
            self.expression,
            arg=self.arg,
            **self._wait_timeout_kwargs(),
        )
        as_json_value = getattr(wait_result, "json_value", None)
        if not callable(as_json_value):
            return wait_result
        try:
            return await as_json_value()
        finally:
            dispose = getattr(wait_result, "dispose", None)
            if callable(dispose):
                await dispose()

    async def _wait_for_timeout(self, session: BrowserSession) -> dict[str, Any]:
        """Sleep for the configured browser timeout."""
        if self.timeout_ms is None:
            raise ValueError("BrowserWaitNode.timeout requires timeout_ms.")
        await session.page.wait_for_timeout(self.timeout_ms)
        return {"timeout_ms": self.timeout_ms}

    def _response_matches(self, response: Any) -> bool:
        """Return whether a Playwright response matches the node criteria."""
        if not isinstance(self.response_url, str) or not self.response_url.strip():
            raise ValueError("BrowserWaitNode.response requires response_url.")
        observed_url = str(response.url)
        if self.response_match_mode == "contains":
            url_matches = self.response_url in observed_url
        else:
            url_matches = observed_url == self.response_url
        if not url_matches:
            return False
        if self.response_status is None:
            return True
        return response.status == self.response_status

    async def _wait_for_response(self, session: BrowserSession) -> dict[str, Any]:
        """Wait for one matching network response."""
        response = await session.page.wait_for_response(
            self._response_matches,
            **self._wait_timeout_kwargs(),
        )
        return {
            "url": response.url,
            "status": response.status,
            "ok": response.ok,
        }

    async def _perform_wait(self, session: BrowserSession) -> Any:
        """Dispatch the configured wait condition."""
        handlers = {
            "selector": self._wait_for_selector,
            "url": self._wait_for_url,
            "load_state": self._wait_for_load_state,
            "function": self._wait_for_function,
            "timeout": self._wait_for_timeout,
            "response": self._wait_for_response,
        }
        handler = handlers.get(self.wait_for)
        if handler is None:  # pragma: no cover - Literal guards this in normal use.
            raise ValueError(f"Unsupported browser wait mode {self.wait_for!r}.")
        return await handler(session)

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Wait for the configured browser condition."""
        del state
        resolved_key, session = await self._require_session(config)
        wait_result = await self._perform_wait(session)

        payload = await self._page_metadata(resolved_key=resolved_key, session=session)
        payload["wait_for"] = self.wait_for
        payload["value"] = wait_result
        return payload


@registry.register(
    NodeMetadata(
        name="BrowserScriptNode",
        description="Evaluate JavaScript in the active Playwright page context.",
        category="browser",
    )
)
class BrowserScriptNode(BrowserNode):
    """Run page.evaluate() against the current Playwright page."""

    script: str = Field(description="JavaScript expression or function to evaluate.")
    arg: Any | None = Field(
        default=None,
        description="Optional argument passed to page.evaluate().",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Evaluate JavaScript and return the result."""
        del state
        resolved_key, session = await self._require_session(config)
        if self.arg is None:
            result = await session.page.evaluate(self.script)
        else:
            result = await session.page.evaluate(self.script, self.arg)
        payload = await self._page_metadata(resolved_key=resolved_key, session=session)
        payload["script"] = self.script
        payload["value"] = result
        return payload


@registry.register(
    NodeMetadata(
        name="BrowserCloseNode",
        description="Close an active Playwright browser session.",
        category="browser",
    )
)
class BrowserCloseNode(BrowserNode):
    """Close a shared Playwright session."""

    trace_path: str | None = Field(
        default=None,
        description="Optional override for where to save the Playwright trace.",
    )

    async def run(self, state: State, config: RunnableConfig) -> dict[str, Any]:
        """Close the browser session when it exists."""
        del state
        resolved_key = self._resolved_session_key(config)
        closed = await _browser_session_manager.close(resolved_key, self.trace_path)
        return {
            "session_id": self.session_id,
            "resolved_session_key": resolved_key,
            "closed": closed,
        }


__all__ = [
    "BrowserAction",
    "BrowserActionNode",
    "BrowserCloseNode",
    "BrowserEngine",
    "BrowserExtractMode",
    "BrowserExtractNode",
    "BrowserNavigateNode",
    "BrowserScriptNode",
    "BrowserWaitFor",
    "BrowserWaitNode",
    "close_browser_sessions_for_scope",
]
