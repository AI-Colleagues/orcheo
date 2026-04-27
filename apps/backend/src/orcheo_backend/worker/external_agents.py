"""Worker-side helpers for external agent status checks and OAuth sessions."""

from __future__ import annotations
import fcntl
import os
import pty
import re
import selectors
import shutil
import signal
import struct
import subprocess
import termios
import time
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from orcheo.external_agents import ExternalAgentRuntimeManager, RuntimeInstallError
from orcheo.external_agents.models import ResolvedRuntime
from orcheo.external_agents.providers.gemini import (
    GEMINI_AUTH_JSON_ENV_VAR,
    GEMINI_GOOGLE_ACCOUNTS_JSON_ENV_VAR,
    GEMINI_OAUTH_CREDS_JSON_ENV_VAR,
    GEMINI_STATE_JSON_ENV_VAR,
    GeminiProvider,
)
from orcheo_backend.app.dependencies import get_external_agent_runtime_store, get_vault
from orcheo_backend.app.external_agent_auth import (
    CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
    CODEX_AUTH_JSON_CREDENTIAL_NAME,
    GEMINI_AUTH_JSON_CREDENTIAL_NAME,
    GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME,
    GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME,
    GEMINI_STATE_JSON_CREDENTIAL_NAME,
    delete_external_agent_secret,
    load_external_agent_vault_environment,
    upsert_external_agent_secret,
)
from orcheo_backend.app.external_agent_runtime_store import (
    default_external_agent_status,
    is_terminal_login_state,
    list_external_agent_providers,
)
from orcheo_backend.app.schemas.system import (
    ExternalAgentLoginSession,
    ExternalAgentLoginSessionState,
    ExternalAgentProviderName,
    ExternalAgentProviderState,
    ExternalAgentProviderStatus,
)


LOGIN_TIMEOUT_SECONDS = 15 * 60
MAX_RECENT_OUTPUT_CHARS = 4000
LOGIN_INPUT_RESEND_SECONDS = 3.0
LOGIN_HEARTBEAT_SECONDS = 2.0
LOGIN_SESSION_STALE_SECONDS = 10.0
LOGIN_PTY_ROWS = 60
LOGIN_PTY_COLS = 240
GEMINI_SIGNIN_WORKING_DIRECTORY = Path("/workspace/agents")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1a\x1c-\x1f\x7f]")
URL_PATTERN = re.compile(r"https?://[^\s]+")
URL_CONTINUATION_PATTERN = re.compile(r"^[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]+$")
DEVICE_CODE_PATTERN = re.compile(r"\b[A-Z0-9]{3,}(?:-[A-Z0-9]{3,})+\b")
CLAUDE_OAUTH_TOKEN_PATTERN = re.compile(r"sk-ant-[A-Za-z0-9_-]+")
CLAUDE_OAUTH_TOKEN_BLOCK_PATTERN = re.compile(
    (
        r"Your OAuth token \(valid for 1 year\):\s+"
        r"(?P<token>.+?)\s+Store this token securely\."
    ),
    re.DOTALL,
)
CLAUDE_OAUTH_TOKEN_FUZZY_BLOCK_PATTERN = re.compile(
    (
        r"valid\s*for\s*1\s*year\):"
        r"(?P<token>.*?)"
        r"Store\s*this\s*token\s*securely\."
    ),
    re.DOTALL | re.IGNORECASE,
)
CLAUDE_OAUTH_TOKEN_EXPORT_PATTERN = re.compile(
    r"CLAUDE_CODE_OAUTH_TOKEN=(?P<token>sk-ant-[A-Za-z0-9_-]+)"
)
CLAUDE_OAUTH_TOKEN_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
GEMINI_VERIFICATION_CODE_PATTERN = re.compile(
    (
        r"(?:verification code|enter code|code is)"
        r".*?(?P<code>[A-Z0-9]{4,}(?:-[A-Z0-9]{3,})+)"
    ),
    re.IGNORECASE,
)


@dataclass(slots=True)
class LoginCommandResult:
    """Captured result from a worker-side interactive login command."""

    exit_code: int | None
    timed_out: bool
    output: str
    auth_url: str | None
    device_code: str | None
    auth_token: str | None


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _worker_provider_environment(runtime_store: object) -> dict[str, str]:
    """Return provider auth env persisted in the shared runtime store."""
    merged: dict[str, str] = {}
    for provider_name in list_external_agent_providers():
        merged.update(
            runtime_store.get_provider_environment(provider_name)  # type: ignore[attr-defined]
        )
    merged.update(load_external_agent_vault_environment(get_vault()))
    return merged


def _codex_auth_file_path(environ: Mapping[str, str]) -> Path:
    """Return the worker-local auth.json path for Codex."""
    codex_home = environ.get("CODEX_HOME", "").strip()
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path("~/.codex/auth.json").expanduser()


def _persist_claude_oauth_token_to_vault(token: str) -> None:
    """Persist a Claude OAuth token to the unrestricted worker vault."""
    upsert_external_agent_secret(
        get_vault(),
        credential_name=CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
        provider="claude_code",
        secret=token,
    )


def _persist_codex_auth_json_to_vault(environ: Mapping[str, str]) -> None:
    """Persist the current worker Codex auth.json content to the vault."""
    auth_file = _codex_auth_file_path(environ)
    if not auth_file.exists():
        return
    upsert_external_agent_secret(
        get_vault(),
        credential_name=CODEX_AUTH_JSON_CREDENTIAL_NAME,
        provider="codex",
        secret=auth_file.read_text(encoding="utf-8"),
    )


def _gemini_provider() -> GeminiProvider:
    """Return a Gemini provider for auth file path helpers."""
    return GeminiProvider()


def _gemini_auth_file_paths(
    environ: Mapping[str, str],
) -> dict[str, Path]:
    """Return the restorable Gemini auth file paths for this worker."""
    provider = _gemini_provider()
    return {
        GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME: provider.google_accounts_path(
            environ
        ),
        GEMINI_STATE_JSON_CREDENTIAL_NAME: provider.state_path(environ),
        GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME: provider.oauth_creds_path(environ),
    }


def _persist_gemini_auth_files_to_vault(environ: Mapping[str, str]) -> None:
    """Persist any available Gemini auth files to the unrestricted vault."""
    vault = get_vault()
    provider = _gemini_provider()
    bundled_payload = provider.serialize_auth_payload(environ)
    if not bundled_payload:
        return

    upsert_external_agent_secret(
        vault,
        credential_name=GEMINI_AUTH_JSON_CREDENTIAL_NAME,
        provider="gemini",
        secret=bundled_payload,
    )
    for credential_name in _gemini_auth_file_paths(environ):
        delete_external_agent_secret(
            vault,
            credential_name=credential_name,
        )


def _sync_authenticated_provider_to_vault(
    provider_id: ExternalAgentProviderName,
    environ: Mapping[str, str],
) -> None:
    """Persist restorable auth material for an already-authenticated provider."""
    if provider_id == ExternalAgentProviderName.CLAUDE_CODE:
        token = environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
        if token:  # pragma: no branch
            _persist_claude_oauth_token_to_vault(token)
        return
    if provider_id == ExternalAgentProviderName.CODEX:  # pragma: no branch
        _persist_codex_auth_json_to_vault(environ)
        return
    if provider_id == ExternalAgentProviderName.GEMINI:  # pragma: no branch
        _persist_gemini_auth_files_to_vault(environ)


def _trim_recent_output(output: str) -> str:
    return output[-MAX_RECENT_OUTPUT_CHARS:]


def _strip_ansi(text: str) -> str:
    stripped = ANSI_ESCAPE_PATTERN.sub("", text)
    stripped = stripped.replace("\r\n", "\n").replace("\r", "\n")
    stripped = stripped.replace("\x1b", "")
    return CONTROL_CHAR_PATTERN.sub(" ", stripped)


def _set_pty_size(
    fd: int, *, rows: int = LOGIN_PTY_ROWS, cols: int = LOGIN_PTY_COLS
) -> None:
    """Resize the worker PTY to reduce Claude TUI wrapping and truncation."""
    winsize = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)


def _create_terminal_emulator() -> Any:
    """Return a VT100 screen emulator for capturing visible login output."""

    def _safe_do_log(fsm: Any) -> None:
        """Discard unsupported escape sequences without writing a local log file."""
        screen = fsm.memory[0]
        fsm.memory = [screen]

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="pexpect.screen and pexpect.ANSI are deprecated.*",
            category=UserWarning,
        )
        from pexpect import ANSI

    # pexpect.ANSI wires its fallback handler to DoLog, which tries to append to
    # a literal "./log" file when it sees an unsupported escape sequence.
    ANSI.DoLog = _safe_do_log
    return ANSI.ANSI(LOGIN_PTY_ROWS, LOGIN_PTY_COLS, encoding="utf-8")


def _render_terminal_screen(screen: Any) -> str:
    """Render the current terminal screen as trimmed plain text."""
    lines = [line.rstrip() for line in str(screen).splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _dewrap_terminal_segments(text: str) -> str:
    """Join wrapped terminal lines for URLs and token-like values."""
    return re.sub(
        r"(?<=[A-Za-z0-9%&=_./:+?-])\s+(?=[A-Za-z0-9%&=_./:+?-])",
        "",
        text,
    )


def _extract_auth_url(output: str) -> str | None:
    lines = output.splitlines()
    matches: list[str] = []
    for index, line in enumerate(lines):
        match = URL_PATTERN.search(line)
        if match is None:
            continue
        parts = [match.group(0)]
        skipped_blank_line = False
        for continuation in lines[index + 1 :]:
            stripped = continuation.strip()
            if not stripped:
                if skipped_blank_line:
                    break
                skipped_blank_line = True
                continue
            if not URL_CONTINUATION_PATTERN.fullmatch(stripped):
                break
            parts.append(stripped)
            skipped_blank_line = False
        matches.append("".join(parts).rstrip(".,;"))
    if not matches:
        return None

    prioritized = sorted(
        matches,
        key=_auth_url_priority,
        reverse=True,
    )
    best = prioritized[0]
    if _auth_url_priority(best) < 0:
        return None
    return best


def _auth_url_priority(candidate: str) -> int:
    """Return a relative priority for URLs shown during auth flows."""
    lowered = candidate.lower()
    if _is_non_auth_reference_url(lowered):
        return -1

    priority = 0
    if "localhost" in lowered or "127.0.0.1" in lowered:
        priority += 0
    else:
        priority += 1

    if any(
        token in lowered
        for token in (
            "oauth",
            "authorize",
            "device",
            "codeassist.google.com/authcode",
        )
    ):
        priority += 20

    hostname = urlsplit(candidate).hostname or ""
    if hostname in {
        "accounts.google.com",
        "claude.ai",
        "console.anthropic.com",
        "chatgpt.com",
        "auth.openai.com",
        "login.openai.com",
    }:
        priority += 10

    return priority


def _is_non_auth_reference_url(lowered_candidate: str) -> bool:
    """Return whether a URL is informational copy instead of an auth target."""
    return any(
        marker in lowered_candidate
        for marker in (
            "geminicli.com/docs/resources/tos-privacy",
            "/docs/",
            "/privacy",
            "/terms",
            "/tos",
        )
    )


def _extract_device_code(output: str) -> str | None:
    gemini_match = GEMINI_VERIFICATION_CODE_PATTERN.search(output)
    if gemini_match is not None:
        return gemini_match.group("code")
    match = DEVICE_CODE_PATTERN.search(output)
    return match.group(0) if match is not None else None


def _extract_auth_token(output: str) -> str | None:
    line_block_token = _extract_claude_oauth_token_from_lines(output)
    if line_block_token is not None:
        return line_block_token

    block_match = CLAUDE_OAUTH_TOKEN_BLOCK_PATTERN.search(output)
    if block_match is not None:
        collapsed = re.sub(r"\s+", "", block_match.group("token"))
        if collapsed.startswith("sk-ant-"):
            return collapsed

    dewrapped = _dewrap_terminal_segments(output)
    fuzzy_match = CLAUDE_OAUTH_TOKEN_FUZZY_BLOCK_PATTERN.search(dewrapped)
    if fuzzy_match is not None:
        collapsed = re.sub(r"\s+", "", fuzzy_match.group("token"))
        token_match = CLAUDE_OAUTH_TOKEN_PATTERN.search(collapsed)
        if token_match is not None:
            return token_match.group(0)
    export_match = CLAUDE_OAUTH_TOKEN_EXPORT_PATTERN.search(dewrapped)
    if export_match is not None:
        return export_match.group("token")
    return None


def _extract_visible_auth_token(output: str) -> str | None:
    """Return a token from rendered screen output when it does not look masked."""
    block_match = CLAUDE_OAUTH_TOKEN_BLOCK_PATTERN.search(output)
    if block_match is not None and "*" in block_match.group("token"):
        return None
    fuzzy_match = CLAUDE_OAUTH_TOKEN_FUZZY_BLOCK_PATTERN.search(output)
    if fuzzy_match is not None and "*" in fuzzy_match.group("token"):
        return None
    return _extract_auth_token(output)


def _extract_worker_auth_token(raw_output: str, visible_output: str) -> str | None:
    """Prefer raw PTY transcript for token extraction, then clean screen output."""
    return _extract_auth_token(raw_output) or _extract_visible_auth_token(
        visible_output
    )


def _normalize_terminal_line(text: str) -> str:
    """Collapse whitespace and casing for fuzzy PTY prompt matching."""
    return re.sub(r"\s+", "", text).lower()


def _has_gemini_trust_prompt(output: str) -> bool:
    """Return whether Gemini is waiting at the folder-trust chooser."""
    normalized = _normalize_terminal_line(output)
    return (
        "doyoutrustthefilesinthisfolder?" in normalized
        and "1.trustfolder" in normalized
        and "3.don'ttrust" in normalized
    )


def _has_gemini_auth_method_prompt(output: str) -> bool:
    """Return whether Gemini is waiting at the auth-method chooser."""
    normalized = _normalize_terminal_line(output)
    return (
        "howwouldyouliketoauthenticateforthisproject?" in normalized
        and "1.signinwithgoogle" in normalized
        and "2.usegeminiapikey" in normalized
        and "3.vertexai" in normalized
        and "noauthenticationmethodselected." in normalized
        and "(useentertoselect)" in normalized
    )


def _gemini_auto_enter_prompt_id(output: str) -> str | None:
    """Return the Gemini prompt id that can be safely accepted with Enter."""
    if _has_gemini_trust_prompt(output):
        return "trust_folder"
    if _has_gemini_auth_method_prompt(output):
        return "auth_method"
    return None


def _should_retry_gemini_login(output: str) -> bool:
    """Return whether Gemini requested a one-time CLI restart during login."""
    normalized = _normalize_terminal_line(output)
    return (
        "logginginwithgoogle...restartinggeminiclitocontinue." in normalized
        or "restartinggeminiclitocontinue." in normalized
    )


def _prefix_before_store_marker(line: str) -> tuple[str, bool]:
    """Return any token content before the Claude storage warning marker."""
    lowered = line.lower()
    index = lowered.find("store")
    if index < 0:
        return line, False
    return line[:index], True


def _is_claude_oauth_header(line: str) -> bool:
    """Return whether a PTY line looks like Claude's OAuth token header."""
    normalized = _normalize_terminal_line(line)
    return "validfor1year" in normalized and (
        "youroauthtok" in normalized or "youroauthto" in normalized
    )


def _token_line_after_header(line: str) -> str | None:
    """Return any token content that appears after the header colon."""
    if not _is_claude_oauth_header(line):
        return None
    if ":" not in line:
        return ""
    return line.split(":", 1)[1].strip()


def _extract_token_segment_from_line(
    line: str,
    *,
    saw_prefix: bool,
) -> tuple[str | None, bool, bool, bool]:
    """Return one token segment, whether the prefix was seen, and stop status."""
    candidate, saw_store_marker = _prefix_before_store_marker(line.strip())
    if not candidate:
        return None, saw_prefix, saw_store_marker, False
    if "*" in candidate:
        return None, saw_prefix, saw_store_marker, False

    saw_new_prefix = False
    if not saw_prefix:
        prefix_index = candidate.find("sk-ant-")
        if prefix_index < 0:
            return None, saw_prefix, saw_store_marker, False
        saw_prefix = True
        candidate = candidate[prefix_index:]
    else:
        prefix_index = candidate.find("sk-ant-")
        if prefix_index >= 0:
            saw_new_prefix = True
            candidate = candidate[prefix_index:]

    segment = "".join(CLAUDE_OAUTH_TOKEN_SEGMENT_PATTERN.findall(candidate))
    return (segment or None), saw_prefix, saw_store_marker, saw_new_prefix


def _extract_claude_oauth_token_from_lines(output: str) -> str | None:
    """Rebuild a wrapped Claude OAuth token from the explicit token block."""
    lines = output.splitlines()
    collecting = False
    saw_prefix = False
    token_segments: list[str] = []

    for raw_line in lines:
        line = raw_line.strip()

        if not collecting:
            line_after_header = _token_line_after_header(line)
            if line_after_header is None:
                continue
            collecting = True
            line = line_after_header

        if not line:
            continue

        segment, saw_prefix, saw_store_marker, saw_new_prefix = (
            _extract_token_segment_from_line(
                line,
                saw_prefix=saw_prefix,
            )
        )
        if saw_new_prefix:
            token_segments = []
        if segment is not None:
            token_segments.append(segment)
        if saw_store_marker:
            break

    if not token_segments:
        return None

    token = "".join(token_segments)
    return token if token.startswith("sk-ant-") else None


def _redact_sensitive_output(output: str) -> str:
    output = CLAUDE_OAUTH_TOKEN_BLOCK_PATTERN.sub(
        lambda match: match.group(0).replace(
            match.group("token"),
            "[redacted worker auth token]",
        ),
        output,
    )
    output = CLAUDE_OAUTH_TOKEN_FUZZY_BLOCK_PATTERN.sub(
        "valid for 1 year):\n[redacted worker auth token]\nStore this token securely.",
        output,
    )
    return CLAUDE_OAUTH_TOKEN_PATTERN.sub("[redacted worker auth token]", output)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        process.wait(timeout=2)


def _drain_login_output(
    master_fd: int,
    *,
    terminal_screen: Any,
    raw_output: str,
    output: str,
    auth_url: str | None,
    device_code: str | None,
    auth_token: str | None,
    on_output: Callable[[str, str | None, str | None], None],
) -> tuple[str, str, str | None, str | None, str | None]:
    """Read and normalize any available PTY output for the login process."""
    while True:
        try:
            chunk = os.read(master_fd, 4096)
        except BlockingIOError:
            break
        except OSError:
            chunk = b""
        if not chunk:
            break
        decoded = chunk.decode("utf-8", errors="replace")
        cleaned = _strip_ansi(decoded)
        raw_output += cleaned
        terminal_screen.write(chunk)
        rendered_output = _render_terminal_screen(terminal_screen)
        visible_output = rendered_output or raw_output
        auth_url = (
            auth_url
            or _extract_auth_url(raw_output)
            or _extract_auth_url(rendered_output)
        )
        device_code = (
            device_code
            or _extract_device_code(raw_output)
            or _extract_device_code(rendered_output)
        )
        auth_token = (
            _extract_worker_auth_token(
                raw_output,
                visible_output,
            )
            or auth_token
        )
        output = _redact_sensitive_output(visible_output)
        on_output(output, auth_url, device_code)
    return raw_output, output, auth_url, device_code, auth_token


def _forward_login_input(
    master_fd: int,
    *,
    queued_input: str | None,
    last_sent_input: str | None,
    last_sent_at: float | None,
    awaiting_output_after_input: bool,
    now: float,
) -> tuple[str | None, float | None, bool]:
    """Send queued operator input back to the interactive login PTY when needed."""
    if queued_input is None:
        return last_sent_input, last_sent_at, awaiting_output_after_input

    should_send = queued_input != last_sent_input
    if (
        not should_send
        and awaiting_output_after_input
        and last_sent_at is not None
        and now - last_sent_at >= LOGIN_INPUT_RESEND_SECONDS
    ):
        should_send = True

    if should_send:
        os.write(master_fd, queued_input.encode())
        os.write(master_fd, b"\r")
        return queued_input, now, True

    return last_sent_input, last_sent_at, awaiting_output_after_input


def _run_login_command(  # noqa: C901, PLR0915
    command: list[str],
    *,
    env: Mapping[str, str],
    cwd: Path | None = None,
    on_output: Callable[[str, str | None, str | None], None],
    consume_input: Callable[[bool], str | None] | None = None,
    auto_input: Callable[[str], str | None] | None = None,
    is_authenticated: Callable[[], bool] | None = None,
    on_tick: Callable[[], None] | None = None,
    timeout_seconds: int = LOGIN_TIMEOUT_SECONDS,
) -> LoginCommandResult:
    """Run an interactive provider login command inside a PTY."""
    master_fd, slave_fd = pty.openpty()
    _set_pty_size(slave_fd)
    os.set_blocking(master_fd, False)
    selector = selectors.DefaultSelector()
    process: subprocess.Popen[bytes] | None = None
    terminal_screen = _create_terminal_emulator()
    raw_output = ""
    output = ""
    auth_url: str | None = None
    device_code: str | None = None
    auth_token: str | None = None
    timed_out = False
    last_sent_input: str | None = None
    last_sent_at: float | None = None
    awaiting_output_after_input = False
    output_len_at_last_input = 0
    last_tick_at = 0.0
    try:
        process = subprocess.Popen(
            command,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=dict(env),
            cwd=str(cwd) if cwd is not None else None,
            start_new_session=True,
            close_fds=True,
        )
        selector.register(master_fd, selectors.EVENT_READ)
        started_at = time.monotonic()

        while True:
            selector.select(timeout=0.5)
            previous_output_len = len(output)
            raw_output, output, auth_url, device_code, auth_token = _drain_login_output(
                master_fd,
                terminal_screen=terminal_screen,
                raw_output=raw_output,
                output=output,
                auth_url=auth_url,
                device_code=device_code,
                auth_token=auth_token,
                on_output=on_output,
            )
            if awaiting_output_after_input and len(output) > output_len_at_last_input:
                awaiting_output_after_input = False
                last_sent_input = None
                if consume_input is not None:
                    consume_input(True)

            now = time.monotonic()
            if on_tick is not None and now - last_tick_at >= LOGIN_HEARTBEAT_SECONDS:
                on_tick()
                last_tick_at = now
            queued_input = consume_input(False) if consume_input is not None else None
            if queued_input is None and auto_input is not None:
                queued_input = auto_input(output)
            (
                last_sent_input,
                last_sent_at,
                awaiting_output_after_input,
            ) = _forward_login_input(
                master_fd,
                queued_input=queued_input,
                last_sent_input=last_sent_input,
                last_sent_at=last_sent_at,
                awaiting_output_after_input=awaiting_output_after_input,
                now=now,
            )
            if len(output) == previous_output_len and last_sent_at == now:
                output_len_at_last_input = len(output)

            if is_authenticated is not None and is_authenticated():
                if consume_input is not None:
                    consume_input(True)
                _terminate_process_group(process)
                break

            if process.poll() is not None:
                raw_output, output, auth_url, device_code, auth_token = (
                    _drain_login_output(
                        master_fd,
                        terminal_screen=terminal_screen,
                        raw_output=raw_output,
                        output=output,
                        auth_url=auth_url,
                        device_code=device_code,
                        auth_token=auth_token,
                        on_output=on_output,
                    )
                )
                break
            if time.monotonic() - started_at > timeout_seconds:
                timed_out = True
                _terminate_process_group(process)
                break

        exit_code = process.wait(timeout=2)
        return LoginCommandResult(
            exit_code=exit_code,
            timed_out=timed_out,
            output=output,
            auth_url=auth_url,
            device_code=device_code,
            auth_token=auth_token,
        )
    finally:
        selector.close()
        os.close(master_fd)
        os.close(slave_fd)
        if process is not None and process.poll() is None:
            _terminate_process_group(process)


def _provider_status(
    provider_name: ExternalAgentProviderName,
    *,
    state: ExternalAgentProviderState,
    authenticated: bool,
    installed: bool,
    detail: str | None,
    resolved_version: str | None = None,
    executable_path: str | None = None,
    checked_at: datetime | None = None,
    last_auth_ok_at: datetime | None = None,
    active_session_id: str | None = None,
) -> ExternalAgentProviderStatus:
    """Build a provider status payload from the current worker state."""
    base = default_external_agent_status(provider_name)
    return base.model_copy(
        update={
            "state": state,
            "authenticated": authenticated,
            "installed": installed,
            "detail": detail,
            "resolved_version": resolved_version,
            "executable_path": executable_path,
            "checked_at": checked_at,
            "last_auth_ok_at": last_auth_ok_at,
            "active_session_id": active_session_id,
        }
    )


def _initial_login_detail(provider_name: ExternalAgentProviderName) -> str:
    """Return the initial operator guidance for a provider login session."""
    if provider_name == ExternalAgentProviderName.CODEX:
        return (
            "Complete the Codex device-auth flow. If ChatGPT says device code "
            "authorization is disabled, enable it in ChatGPT Security Settings and "
            "retry."
        )
    if provider_name == ExternalAgentProviderName.GEMINI:
        return (
            "Complete the Gemini Google sign-in flow on the worker. Paste the "
            "verification code back into Canvas if Gemini prompts for one."
        )
    return "Starting the provider OAuth flow on the worker."


def _browser_login_detail(provider_name: ExternalAgentProviderName) -> str:
    """Return provider-specific guidance while a browser login is in progress."""
    if provider_name == ExternalAgentProviderName.CODEX:
        return (
            "Open the device-auth page, enter the one-time device code, and finish "
            "sign-in. If ChatGPT says device authorization is disabled, enable it "
            "in ChatGPT Security Settings and retry."
        )
    if provider_name == ExternalAgentProviderName.CLAUDE_CODE:
        return (
            "Complete the browser sign-in. If Claude shows a one-time code at the "
            "end, paste it back into Canvas to finish worker auth."
        )
    if provider_name == ExternalAgentProviderName.GEMINI:
        return (
            "Open the Gemini sign-in link, finish browser auth, and paste the "
            "verification code back into Canvas when Gemini asks for it."
        )
    return "Complete the browser sign-in to finish connecting the worker."


def _delete_file_if_present(path: Path) -> None:
    """Delete ``path`` if it exists."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _delete_directory_if_present(path: Path) -> None:
    """Delete ``path`` recursively if it exists."""
    if not path.exists():
        return
    shutil.rmtree(path, ignore_errors=True)


def _logout_provider_cli(
    provider_id: ExternalAgentProviderName,
    runtime: ResolvedRuntime | None,
    provider_environ: Mapping[str, str],
) -> None:
    """Best-effort provider-native logout for CLIs that support it."""
    if runtime is None:
        return
    if provider_id == ExternalAgentProviderName.CLAUDE_CODE:
        command = [str(runtime.executable_path), "auth", "logout"]
    elif provider_id == ExternalAgentProviderName.CODEX:
        command = [str(runtime.executable_path), "logout"]
    else:
        return

    try:
        subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env=dict(provider_environ),
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return


def _mark_provider_session_disconnected(
    runtime_store: object,
    provider_id: ExternalAgentProviderName,
) -> None:
    """Clear any active session state and queued input for one provider."""
    current = runtime_store.get_provider_status(provider_id)  # type: ignore[attr-defined]
    session_id = current.active_session_id
    if session_id:
        session = runtime_store.get_login_session(session_id)  # type: ignore[attr-defined]
        if session is not None and not is_terminal_login_state(session.state):
            runtime_store.save_login_session(  # type: ignore[attr-defined]
                session.model_copy(
                    update={
                        "state": ExternalAgentLoginSessionState.FAILED,
                        "detail": "Disconnected by operator.",
                        "updated_at": _utcnow(),
                        "completed_at": _utcnow(),
                    }
                )
            )
        runtime_store.clear_login_input(session_id)  # type: ignore[attr-defined]
    runtime_store.clear_provider_session(provider_id)  # type: ignore[attr-defined]


def _clear_provider_auth_state(
    provider_id: ExternalAgentProviderName,
    provider_environ: Mapping[str, str],
) -> None:
    """Delete Orcheo-managed auth material for one provider."""
    vault = get_vault()
    if provider_id == ExternalAgentProviderName.CLAUDE_CODE:
        delete_external_agent_secret(
            vault,
            credential_name=CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
        )
        return

    if provider_id == ExternalAgentProviderName.CODEX:
        delete_external_agent_secret(
            vault,
            credential_name=CODEX_AUTH_JSON_CREDENTIAL_NAME,
        )
        _delete_file_if_present(_codex_auth_file_path(provider_environ))
        return

    if provider_id == ExternalAgentProviderName.GEMINI:
        delete_external_agent_secret(
            vault,
            credential_name=GEMINI_AUTH_JSON_CREDENTIAL_NAME,
        )
        delete_external_agent_secret(
            vault,
            credential_name=GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME,
        )
        delete_external_agent_secret(
            vault,
            credential_name=GEMINI_STATE_JSON_CREDENTIAL_NAME,
        )
        delete_external_agent_secret(
            vault,
            credential_name=GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME,
        )
        _delete_directory_if_present(
            _gemini_provider().gemini_home_path(provider_environ)
        )


def _provider_environment_reset(
    provider_id: ExternalAgentProviderName,
) -> dict[str, str]:
    """Return provider-specific environment keys that should be cleared."""
    if provider_id == ExternalAgentProviderName.CLAUDE_CODE:
        return {"CLAUDE_CODE_OAUTH_TOKEN": ""}
    if provider_id == ExternalAgentProviderName.CODEX:
        return {"CODEX_AUTH_JSON": ""}
    if provider_id == ExternalAgentProviderName.GEMINI:
        return {
            GEMINI_AUTH_JSON_ENV_VAR: "",
            GEMINI_GOOGLE_ACCOUNTS_JSON_ENV_VAR: "",
            GEMINI_STATE_JSON_ENV_VAR: "",
            GEMINI_OAUTH_CREDS_JSON_ENV_VAR: "",
        }
    return {}


def _gemini_login_working_directory() -> Path:
    """Return the Gemini sign-in directory that should be trusted."""
    try:
        GEMINI_SIGNIN_WORKING_DIRECTORY.mkdir(parents=True, exist_ok=True)
    except OSError:
        return Path.cwd()
    return GEMINI_SIGNIN_WORKING_DIRECTORY


def _delete_provider_runtime_installation(
    manager: ExternalAgentRuntimeManager,
    provider_name: str,
) -> None:
    """Delete one provider's managed runtime installation tree."""
    _delete_directory_if_present(manager.runtime_root / provider_name)


async def disconnect_external_agent_async(provider_name: str) -> dict[str, str]:
    """Clear worker-scoped auth state for one provider and refresh readiness."""
    provider_id = ExternalAgentProviderName(provider_name)
    runtime_store = get_external_agent_runtime_store()
    manager = ExternalAgentRuntimeManager(
        environ=_worker_provider_environment(runtime_store)
    )
    runtime, _ = manager.inspect_runtime(provider_name)
    provider_environ = manager.environment_for_provider(provider_name)

    _logout_provider_cli(provider_id, runtime, provider_environ)
    _clear_provider_auth_state(provider_id, provider_environ)
    manager.save_provider_environment(
        provider_name,
        _provider_environment_reset(provider_id),
    )
    if provider_id == ExternalAgentProviderName.GEMINI:
        _delete_provider_runtime_installation(manager, provider_name)
    runtime_store.save_provider_environment(provider_id, {})
    _mark_provider_session_disconnected(runtime_store, provider_id)
    return await refresh_external_agent_status_async(provider_name)


def _last_auth_ok_at(manifest: object | None) -> datetime | None:
    """Return the stored last-auth timestamp when a manifest is present."""
    return getattr(manifest, "last_auth_ok_at", None)


def _save_ready_provider_status(
    runtime_store: object,
    provider_id: ExternalAgentProviderName,
    *,
    runtime_version: str,
    executable_path: str,
    checked_at: datetime,
    last_auth_ok_at: datetime | None,
) -> None:
    """Persist a ready/authenticated provider status."""
    runtime_store.save_provider_status(  # type: ignore[attr-defined]
        _provider_status(
            provider_id,
            state=ExternalAgentProviderState.READY,
            authenticated=True,
            installed=True,
            detail="Ready on the worker.",
            resolved_version=runtime_version,
            executable_path=executable_path,
            checked_at=checked_at,
            last_auth_ok_at=last_auth_ok_at,
        )
    )


def _save_needs_login_provider_status(
    runtime_store: object,
    provider_id: ExternalAgentProviderName,
    *,
    detail: str,
    runtime_version: str,
    executable_path: str,
    checked_at: datetime,
    last_auth_ok_at: datetime | None,
) -> None:
    """Persist a worker status showing provider login is still required."""
    runtime_store.save_provider_status(  # type: ignore[attr-defined]
        _provider_status(
            provider_id,
            state=ExternalAgentProviderState.NEEDS_LOGIN,
            authenticated=False,
            installed=True,
            detail=detail,
            resolved_version=runtime_version,
            executable_path=executable_path,
            checked_at=checked_at,
            last_auth_ok_at=last_auth_ok_at,
        )
    )


def _active_login_session_status(
    runtime_store: object,
    provider_id: ExternalAgentProviderName,
    *,
    checked_at: datetime,
) -> ExternalAgentProviderStatus | None:
    """Return the in-flight provider status for one active login session."""
    current = runtime_store.get_provider_status(provider_id)  # type: ignore[attr-defined]
    session_id = current.active_session_id
    if not session_id:
        return None

    session = runtime_store.get_login_session(session_id)  # type: ignore[attr-defined]
    if session is None or is_terminal_login_state(session.state):
        return None
    if (checked_at - session.updated_at).total_seconds() > LOGIN_SESSION_STALE_SECONDS:
        return None

    state = (
        ExternalAgentProviderState.INSTALLING
        if session.state == ExternalAgentLoginSessionState.INSTALLING
        else ExternalAgentProviderState.AUTHENTICATING
    )
    installed = state != ExternalAgentProviderState.INSTALLING
    detail = session.detail or current.detail or "Waiting for worker-side sign-in."

    return _provider_status(
        provider_id,
        state=state,
        authenticated=False,
        installed=installed,
        detail=detail,
        resolved_version=session.resolved_version or current.resolved_version,
        executable_path=session.executable_path or current.executable_path,
        checked_at=checked_at,
        last_auth_ok_at=current.last_auth_ok_at,
        active_session_id=session.session_id,
    )


async def refresh_external_agent_status_async(provider_name: str) -> dict[str, str]:
    """Refresh one provider status without forcing a runtime install."""
    provider_id = ExternalAgentProviderName(provider_name)
    runtime_store = get_external_agent_runtime_store()
    manager = ExternalAgentRuntimeManager(
        environ=_worker_provider_environment(runtime_store)
    )
    provider = manager.get_provider(provider_name)
    checked_at = _utcnow()
    provider_environ = manager.environment_for_provider(provider_name)

    try:
        active_session_status = _active_login_session_status(
            runtime_store,
            provider_id,
            checked_at=checked_at,
        )
        if active_session_status is not None:
            runtime_store.save_provider_status(active_session_status)
            return {"status": active_session_status.state.value}

        runtime, manifest = manager.inspect_runtime(provider_name)
        if runtime is None:
            runtime_store.save_provider_status(
                _provider_status(
                    provider_id,
                    state=ExternalAgentProviderState.NOT_INSTALLED,
                    authenticated=False,
                    installed=False,
                    detail="Runtime not installed on the worker yet.",
                    checked_at=checked_at,
                )
            )
            return {"status": "not_installed"}

        probe = provider.probe_auth(runtime, environ=provider_environ)
        if probe.authenticated:
            _sync_authenticated_provider_to_vault(provider_id, provider_environ)
            manifest = manager.mark_auth_success(provider_name)
            _save_ready_provider_status(
                runtime_store,
                provider_id,
                runtime_version=runtime.version,
                executable_path=str(runtime.executable_path),
                checked_at=checked_at,
                last_auth_ok_at=manifest.last_auth_ok_at,
            )
            return {"status": "ready"}

        _save_needs_login_provider_status(
            runtime_store,
            provider_id,
            detail=probe.message or "OAuth login is required on the worker.",
            runtime_version=runtime.version,
            executable_path=str(runtime.executable_path),
            checked_at=checked_at,
            last_auth_ok_at=_last_auth_ok_at(manifest),
        )
        return {"status": "needs_login"}
    except Exception as exc:
        runtime_store.save_provider_status(
            _provider_status(
                provider_id,
                state=ExternalAgentProviderState.ERROR,
                authenticated=False,
                installed=False,
                detail=str(exc),
                checked_at=checked_at,
            )
        )
        return {"status": "error", "detail": str(exc)}


async def start_external_agent_login_async(  # noqa: C901, PLR0915
    provider_name: str,
    session_id: str,
) -> dict[str, str]:
    """Install the provider runtime if needed and run the interactive OAuth flow."""
    provider_id = ExternalAgentProviderName(provider_name)
    runtime_store = get_external_agent_runtime_store()
    manager = ExternalAgentRuntimeManager(
        environ=_worker_provider_environment(runtime_store)
    )
    provider = manager.get_provider(provider_name)
    session = runtime_store.get_login_session(session_id)
    if session is None:
        return {"status": "missing_session"}

    def save_session(
        state: ExternalAgentLoginSessionState,
        *,
        detail: str | None,
        auth_url: str | None = None,
        device_code: str | None = None,
        recent_output: str | None = None,
        resolved_version: str | None = None,
        executable_path: str | None = None,
        completed: bool = False,
    ) -> ExternalAgentLoginSession:
        current = runtime_store.get_login_session(session_id) or session
        updated = current.model_copy(
            update={
                "state": state,
                "detail": detail,
                "auth_url": auth_url,
                "device_code": device_code,
                "recent_output": recent_output,
                "resolved_version": resolved_version,
                "executable_path": executable_path,
                "updated_at": _utcnow(),
                "completed_at": _utcnow() if completed else None,
            }
        )
        runtime_store.save_login_session(updated)
        return updated

    def save_authenticated_session(
        current_runtime: ResolvedRuntime,
        detail: str,
    ) -> None:
        """Persist a completed authenticated worker login session."""
        save_session(
            ExternalAgentLoginSessionState.AUTHENTICATED,
            detail=detail,
            resolved_version=current_runtime.version,
            executable_path=str(current_runtime.executable_path),
            completed=True,
        )

    def save_provider_ready(
        current_runtime: ResolvedRuntime,
        manifest_last_auth_ok_at: datetime | None,
    ) -> None:
        """Persist a ready provider state for the current runtime."""
        _save_ready_provider_status(
            runtime_store,
            provider_id,
            runtime_version=current_runtime.version,
            executable_path=str(current_runtime.executable_path),
            checked_at=_utcnow(),
            last_auth_ok_at=manifest_last_auth_ok_at,
        )

    try:
        runtime, manifest = manager.inspect_runtime(provider_name)
        if runtime is None:
            save_session(
                ExternalAgentLoginSessionState.INSTALLING,
                detail="Installing the managed runtime on the worker.",
            )
            runtime_store.save_provider_status(
                _provider_status(
                    provider_id,
                    state=ExternalAgentProviderState.INSTALLING,
                    authenticated=False,
                    installed=False,
                    detail="Installing the managed runtime on the worker.",
                    active_session_id=session_id,
                    checked_at=_utcnow(),
                )
            )
            resolution = await manager.resolve_runtime(provider_name)
            runtime = resolution.runtime
            manifest = resolution.manifest

        provider_environ = manager.environment_for_provider(provider_name)
        probe = provider.probe_auth(runtime, environ=provider_environ)
        if probe.authenticated:
            _sync_authenticated_provider_to_vault(provider_id, provider_environ)
            manifest = manager.mark_auth_success(provider_name)
            save_authenticated_session(runtime, "Already authenticated on the worker.")
            save_provider_ready(runtime, manifest.last_auth_ok_at)
            return {"status": "ready"}

        save_session(
            ExternalAgentLoginSessionState.PENDING,
            detail=_initial_login_detail(provider_id),
            resolved_version=runtime.version,
            executable_path=str(runtime.executable_path),
        )
        runtime_store.save_provider_status(
            _provider_status(
                provider_id,
                state=ExternalAgentProviderState.AUTHENTICATING,
                authenticated=False,
                installed=True,
                detail="Waiting for browser-based sign-in.",
                resolved_version=runtime.version,
                executable_path=str(runtime.executable_path),
                checked_at=_utcnow(),
                last_auth_ok_at=manifest.last_auth_ok_at
                if manifest is not None
                else None,
                active_session_id=session_id,
            )
        )

        def on_output(
            output: str,
            auth_url: str | None,
            device_code: str | None,
        ) -> None:
            detail = _browser_login_detail(provider_id)
            state = (
                ExternalAgentLoginSessionState.AWAITING_OAUTH
                if auth_url or device_code
                else ExternalAgentLoginSessionState.PENDING
            )
            save_session(
                state,
                detail=detail,
                auth_url=auth_url,
                device_code=device_code,
                recent_output=_trim_recent_output(output),
                resolved_version=runtime.version,
                executable_path=str(runtime.executable_path),
            )

        def manage_login_input(clear: bool) -> str | None:
            """Read or clear queued operator input for the active login session."""
            if clear:
                runtime_store.clear_login_input(session_id)
                return None
            return runtime_store.get_login_input(session_id)

        def heartbeat_session() -> None:
            """Keep the login session fresh while the worker process is alive."""
            current = runtime_store.get_login_session(session_id)
            if current is None or is_terminal_login_state(current.state):
                return
            runtime_store.save_login_session(
                current.model_copy(update={"updated_at": _utcnow()})
            )

        result: LoginCommandResult | None = None
        login_attempts = 2 if provider_id == ExternalAgentProviderName.GEMINI else 1
        for attempt in range(login_attempts):  # pragma: no branch
            gemini_auto_enter_prompts_sent: set[str] = set()

            def auto_login_input(
                output: str,
                prompts_sent: set[str] = gemini_auto_enter_prompts_sent,
            ) -> str | None:
                """Return provider-specific automatic PTY input when it is safe."""
                if provider_id != ExternalAgentProviderName.GEMINI:
                    return None
                prompt_id = _gemini_auto_enter_prompt_id(output)
                if prompt_id is None or prompt_id in prompts_sent:
                    return None
                prompts_sent.add(prompt_id)
                return ""

            result = _run_login_command(
                provider.oauth_login_command(runtime),
                env=provider.build_environment(provider_environ),
                cwd=(
                    _gemini_login_working_directory()
                    if provider_id == ExternalAgentProviderName.GEMINI
                    else None
                ),
                on_output=on_output,
                consume_input=manage_login_input,
                auto_input=auto_login_input,
                is_authenticated=lambda: (
                    provider.probe_auth(
                        runtime,
                        environ=manager.environment_for_provider(provider_name),
                    ).authenticated
                ),
                on_tick=heartbeat_session,
            )
            if (
                provider_id == ExternalAgentProviderName.GEMINI
                and attempt + 1 < login_attempts
                and not result.auth_url
                and not result.device_code
                and _should_retry_gemini_login(result.output)
            ):
                continue
            break

        assert result is not None
        if result.auth_token:
            _persist_claude_oauth_token_to_vault(result.auth_token)
            provider_environ["CLAUDE_CODE_OAUTH_TOKEN"] = result.auth_token
        if provider_id == ExternalAgentProviderName.CODEX:
            _persist_codex_auth_json_to_vault(provider_environ)
        probe = provider.probe_auth(
            runtime,
            environ=provider_environ,
        )
        if probe.authenticated:
            _sync_authenticated_provider_to_vault(provider_id, provider_environ)
            manifest = manager.mark_auth_success(provider_name)
            save_session(
                ExternalAgentLoginSessionState.AUTHENTICATED,
                detail="Worker authentication completed successfully.",
                auth_url=result.auth_url,
                device_code=result.device_code,
                recent_output=_trim_recent_output(result.output),
                resolved_version=runtime.version,
                executable_path=str(runtime.executable_path),
                completed=True,
            )
            save_provider_ready(runtime, manifest.last_auth_ok_at)
            return {"status": "authenticated"}

        session_state = (
            ExternalAgentLoginSessionState.TIMED_OUT
            if result.timed_out
            else ExternalAgentLoginSessionState.FAILED
        )
        detail = (
            "The login session timed out before the worker was authenticated."
            if result.timed_out
            else "The OAuth flow exited before authentication completed."
        )
        save_session(
            session_state,
            detail=detail,
            auth_url=result.auth_url,
            device_code=result.device_code,
            recent_output=_trim_recent_output(result.output),
            resolved_version=runtime.version,
            executable_path=str(runtime.executable_path),
            completed=True,
        )
        _save_needs_login_provider_status(
            runtime_store,
            provider_id,
            detail=detail,
            runtime_version=runtime.version,
            executable_path=str(runtime.executable_path),
            checked_at=_utcnow(),
            last_auth_ok_at=_last_auth_ok_at(manifest),
        )
        return {"status": session_state.value}
    except RuntimeInstallError as exc:
        save_session(
            ExternalAgentLoginSessionState.FAILED,
            detail=f"Failed to install the managed runtime: {exc}",
            completed=True,
        )
        runtime_store.save_provider_status(
            _provider_status(
                provider_id,
                state=ExternalAgentProviderState.ERROR,
                authenticated=False,
                installed=False,
                detail=f"Failed to install the managed runtime: {exc}",
                checked_at=_utcnow(),
            )
        )
        return {"status": "install_failed"}
    except Exception as exc:
        save_session(
            ExternalAgentLoginSessionState.FAILED,
            detail=str(exc),
            completed=True,
        )
        runtime_store.save_provider_status(
            _provider_status(
                provider_id,
                state=ExternalAgentProviderState.ERROR,
                authenticated=False,
                installed=False,
                detail=str(exc),
                checked_at=_utcnow(),
            )
        )
        return {"status": "error", "detail": str(exc)}


__all__ = [
    "refresh_external_agent_status_async",
    "start_external_agent_login_async",
]
