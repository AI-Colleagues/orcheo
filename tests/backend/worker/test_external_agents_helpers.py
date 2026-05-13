import os
import signal
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import pytest
from orcheo_backend.app.external_agent_runtime_store import ExternalAgentRuntimeStore
from orcheo_backend.app.schemas.system import (
    ExternalAgentLoginSession,
    ExternalAgentLoginSessionState,
    ExternalAgentProviderName,
)
from orcheo_backend.worker.external_agents import (
    CODEX_AUTH_JSON_CREDENTIAL_NAME,
    GEMINI_AUTH_JSON_CREDENTIAL_NAME,
    GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME,
    GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME,
    GEMINI_STATE_JSON_CREDENTIAL_NAME,
    LOGIN_INPUT_RESEND_SECONDS,
    _active_login_session_status,
    _browser_login_detail,
    _clear_provider_auth_state,
    _delete_directory_if_present,
    _delete_file_if_present,
    _drain_login_output,
    _extract_auth_token,
    _extract_auth_url,
    _extract_device_code,
    _extract_token_segment_from_line,
    _extract_visible_auth_token,
    _extract_worker_auth_token,
    _forward_login_input,
    _gemini_auth_file_paths,
    _gemini_auto_enter_prompt_id,
    _gemini_login_working_directory,
    _has_gemini_auth_method_prompt,
    _has_gemini_trust_prompt,
    _initial_login_detail,
    _logout_provider_cli,
    _mark_provider_session_disconnected,
    _normalize_terminal_line,
    _persist_codex_auth_json_to_vault,
    _persist_gemini_auth_files_to_vault,
    _prefix_before_store_marker,
    _provider_environment_reset,
    _redact_sensitive_output,
    _should_retry_gemini_login,
    _sync_authenticated_provider_to_vault,
    _terminate_process_group,
    _token_line_after_header,
    _trim_recent_output,
)


@pytest.fixture
def runtime_store() -> ExternalAgentRuntimeStore:
    store = ExternalAgentRuntimeStore()
    store._redis = None
    return store


def test_extract_auth_url_prefers_public_link() -> None:
    payload = """https://localhost/auth
    some text
    https://example.com/auth?action=1"""
    assert _extract_auth_url(payload) == "https://example.com/auth?action=1"


def test_extract_auth_url_returns_localhost_when_it_is_the_only_match() -> None:
    assert (
        _extract_auth_url("Open http://localhost:1455/auth to continue")
        == "http://localhost:1455/auth"
    )


def test_extract_auth_url_ignores_gemini_tos_url_until_real_auth_url_appears() -> None:
    payload = """
    Terms of Services and Privacy Notice for Gemini CLI
    https://geminicli.com/docs/resources/tos-privacy/

    Please visit the following URL to authorize the application:

    https://accounts.google.com/o/oauth2/v2/auth?redirect_uri=https%3A%2F%2Fcodeassist.google.com%2Fauthcode&response_type=code
    """

    assert _extract_auth_url(payload) == (
        "https://accounts.google.com/o/oauth2/v2/auth?redirect_uri="
        "https%3A%2F%2Fcodeassist.google.com%2Fauthcode&response_type=code"
    )


def test_extract_auth_url_returns_none_for_gemini_tos_url_only() -> None:
    payload = """
    Terms of Services and Privacy Notice for Gemini CLI
    https://geminicli.com/docs/resources/tos-privacy/
    """

    assert _extract_auth_url(payload) is None


def test_extract_auth_url_stops_after_second_blank_continuation_line() -> None:
    payload = """https://example.com/path

part-one


part-two"""
    assert _extract_auth_url(payload) == "https://example.com/pathpart-one"


def test_extract_device_code() -> None:
    payload = "Use CODE-ABCD-1234-XYZ to authenticate"
    assert _extract_device_code(payload) == "CODE-ABCD-1234-XYZ"


def test_extract_device_code_supports_gemini_verification_copy() -> None:
    payload = "Your verification code is ABCD-1234"
    assert _extract_device_code(payload) == "ABCD-1234"


def test_has_gemini_trust_prompt_matches_folder_trust_screen() -> None:
    output = """
    Do you trust the files in this folder?

    1. Trust folder (app)
    2. Trust parent folder ()
    3. Don't trust
    """

    assert _has_gemini_trust_prompt(output) is True


def test_has_gemini_trust_prompt_rejects_other_prompts() -> None:
    assert (
        _has_gemini_trust_prompt("Open the browser sign-in link to continue.") is False
    )


def test_has_gemini_auth_method_prompt_matches_auth_selector() -> None:
    output = """
    How would you like to authenticate for this project?

    1. Sign in with Google
    2. Use Gemini API Key
    3. Vertex AI

    No authentication method selected.

    (Use Enter to select)
    """

    assert _has_gemini_auth_method_prompt(output) is True


def test_gemini_auto_enter_prompt_id_distinguishes_supported_prompts() -> None:
    assert (
        _gemini_auto_enter_prompt_id(
            """
            Do you trust the files in this folder?
            1. Trust folder (app)
            3. Don't trust
            """
        )
        == "trust_folder"
    )
    assert (
        _gemini_auto_enter_prompt_id(
            """
            How would you like to authenticate for this project?
            1. Sign in with Google
            2. Use Gemini API Key
            3. Vertex AI
            No authentication method selected.
            (Use Enter to select)
            """
        )
        == "auth_method"
    )
    assert _gemini_auto_enter_prompt_id("Continue in the browser.") is None


def test_should_retry_gemini_login_matches_restart_notice() -> None:
    assert (
        _should_retry_gemini_login(
            "Logging in with Google... Restarting Gemini CLI to continue."
        )
        is True
    )


def test_should_retry_gemini_login_ignores_non_restart_output() -> None:
    assert _should_retry_gemini_login("Please visit the following URL.") is False


def test_gemini_login_working_directory_returns_signin_path_when_created(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signin_dir = Path("/tmp/orcheo-gemini-login")
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.GEMINI_SIGNIN_WORKING_DIRECTORY",
        signin_dir,
    )

    assert _gemini_login_working_directory() == signin_dir


def test_gemini_login_working_directory_falls_back_to_cwd_on_mkdir_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    signin_dir = Path("/tmp/orcheo-gemini-login")
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.GEMINI_SIGNIN_WORKING_DIRECTORY",
        signin_dir,
    )

    def _raise_oserror(*args: object, **kwargs: object) -> None:
        raise OSError("read-only")

    monkeypatch.setattr(Path, "mkdir", _raise_oserror)
    monkeypatch.chdir(tmp_path)

    assert _gemini_login_working_directory() == tmp_path


@pytest.mark.parametrize(
    "token_block",
    [
        "Your OAuth token (valid for 1 year):\nsk-ant-AAA\nStore this token securely.",
        "valid for 1 year):\nsk-ant-BBB\nStore this token securely.",
        "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-CCC",
    ],
)
def test_extract_auth_token_blocks(token_block: str) -> None:
    token = _extract_auth_token(token_block)
    assert token is not None and token.startswith("sk-ant-")


def test_extract_auth_token_rebuilds_collapsed_block_when_line_parser_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._extract_claude_oauth_token_from_lines",
        lambda output: None,
    )

    output = (
        "Your OAuth token (valid for 1 year): sk-ant-AAA BBB Store this token securely."
    )

    assert _extract_auth_token(output) == "sk-ant-AAABBB"


def test_extract_auth_token_ignores_fuzzy_block_without_real_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._extract_claude_oauth_token_from_lines",
        lambda output: None,
    )

    output = "valid for 1 year):\nnot-a-token\nStore this token securely.\n"

    assert _extract_auth_token(output) is None


def test_extract_auth_token_ignores_strict_block_without_claude_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._extract_claude_oauth_token_from_lines",
        lambda output: None,
    )

    output = (
        "Your OAuth token (valid for 1 year):\n"
        "not-a-claude-token\n"
        "Store this token securely.\n"
    )

    assert _extract_auth_token(output) is None


def test_extract_visible_auth_token_handles_masked() -> None:
    masked = (
        "Your OAuth token (valid for 1 year): sk-ant-***\nStore this token securely."
    )
    assert _extract_visible_auth_token(masked) is None


def test_extract_visible_auth_token_handles_masked_fuzzy_block() -> None:
    masked = "valid for 1 year):\nsk-ant-***\nStore this token securely."
    assert _extract_visible_auth_token(masked) is None


def test_extract_visible_auth_token_unmasked() -> None:
    raw = "Your OAuth token (valid for 1 year): sk-ant-XYZ\nStore this token securely."
    assert _extract_visible_auth_token(raw) == "sk-ant-XYZ"


def test_extract_worker_auth_token_prefers_raw() -> None:
    raw = "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-RAW"
    visible = (
        "Your OAuth token (valid for 1 year): sk-ant-***\nStore this token securely."
    )
    assert _extract_worker_auth_token(raw, visible) == "sk-ant-RAW"


def test_normalize_and_prefix_helpers() -> None:
    line = " Valid for 1 year: YOUROAUTH TOKEN: sk-ant-XYZ "
    normalized = _normalize_terminal_line(line)
    assert "validfor1year" in normalized
    prefix, saw = _prefix_before_store_marker("token store it")
    assert saw


def test_token_line_after_header_without_colon_returns_empty_string() -> None:
    assert _token_line_after_header("Your OAuth token (valid for 1 year)") == ""


def test_extract_token_segment_from_line_ignores_masked_candidate() -> None:
    segment, saw_prefix, saw_store, saw_new_prefix = _extract_token_segment_from_line(
        "sk-ant-***",
        saw_prefix=False,
    )
    assert segment is None
    assert saw_prefix is False
    assert saw_store is False
    assert saw_new_prefix is False


def test_redact_sensitive_output() -> None:
    secret = "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-SECRET"
    assert "[redacted worker auth token]" in _redact_sensitive_output(secret)


def test_trim_recent_output() -> None:
    long_text = "a" * 5000
    trimmed = _trim_recent_output(long_text)
    assert len(trimmed) == len(long_text[-4000:])


def test_persist_codex_auth_json_skips_when_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target_file = tmp_path / "auth.json"
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._codex_auth_file_path",
        lambda environ: target_file,
    )
    sentinel_vault = object()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: sentinel_vault,
    )
    calls: list[bool] = []

    def fake_upsert(*args: Any, **kwargs: Any) -> None:
        calls.append(True)

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.upsert_external_agent_secret",
        fake_upsert,
    )

    _persist_codex_auth_json_to_vault({"CODEX_HOME": "unused"})

    assert calls == []


def test_persist_codex_auth_json_reads_file(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_data = "secret-json"
    auth_file.write_text(auth_data, encoding="utf-8")

    sentinel_vault = object()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._codex_auth_file_path",
        lambda environ: auth_file,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: sentinel_vault,
    )
    recorded: list[tuple[Any, str, str, str]] = []  # noqa: E501

    def fake_upsert(
        vault: Any, credential_name: str, provider: str, secret: str
    ) -> None:
        recorded.append((vault, credential_name, provider, secret))

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.upsert_external_agent_secret",
        fake_upsert,
    )

    _persist_codex_auth_json_to_vault({})

    assert recorded == [
        (
            sentinel_vault,
            CODEX_AUTH_JSON_CREDENTIAL_NAME,
            "codex",
            auth_data,
        )
    ]


def test_persist_gemini_auth_files_reads_available_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir()
    google_accounts = gemini_home / "google_accounts.json"
    google_accounts.write_text('{"active":{}}', encoding="utf-8")
    state = gemini_home / "state.json"
    state.write_text('{"tipsShown":{}}', encoding="utf-8")
    trusted_folders = gemini_home / "trustedFolders.json"
    trusted_folders.write_text(
        '{"trustedFolders":["/workspace/agents"]}',
        encoding="utf-8",
    )
    installation_id = gemini_home / "installation_id"
    installation_id.write_text("worker-installation", encoding="utf-8")

    sentinel_vault = object()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: sentinel_vault,
    )
    recorded: list[tuple[Any, str, str, str]] = []
    deleted: list[tuple[Any, str]] = []

    def fake_upsert(
        vault: Any, credential_name: str, provider: str, secret: str
    ) -> None:
        recorded.append((vault, credential_name, provider, secret))

    def fake_delete(vault: Any, credential_name: str) -> bool:
        deleted.append((vault, credential_name))
        return True

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.upsert_external_agent_secret",
        fake_upsert,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.delete_external_agent_secret",
        fake_delete,
    )

    _persist_gemini_auth_files_to_vault({"HOME": str(tmp_path)})

    assert recorded == [
        (
            sentinel_vault,
            GEMINI_AUTH_JSON_CREDENTIAL_NAME,
            "gemini",
            (
                '{"files":{"google_accounts.json":"{\\"active\\":{}}",'
                '"installation_id":"worker-installation",'
                '"state.json":"{\\"tipsShown\\":{}}",'
                '"trustedFolders.json":"{\\"trustedFolders\\":[\\"/workspace/agents\\"]}"},'
                '"version":1}'
            ),
        ),
    ]
    assert deleted == [
        (sentinel_vault, GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME),
        (sentinel_vault, GEMINI_STATE_JSON_CREDENTIAL_NAME),
        (sentinel_vault, GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME),
    ]


def test_sync_authenticated_provider_to_vault_persists_claude_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._persist_claude_oauth_token_to_vault",
        lambda token: captured.append(token),
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._persist_codex_auth_json_to_vault",
        lambda environ: pytest.fail("codex persist should not run for Claude"),
    )

    _sync_authenticated_provider_to_vault(
        ExternalAgentProviderName.CLAUDE_CODE,
        {"CLAUDE_CODE_OAUTH_TOKEN": " sk-ant-1234 "},
    )

    assert captured == ["sk-ant-1234"]


def test_sync_authenticated_provider_to_vault_persists_codex_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, str]] = []

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._persist_codex_auth_json_to_vault",
        lambda env: captured.append(env),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._persist_claude_oauth_token_to_vault",
        lambda token: pytest.fail("claude persist should not run for Codex"),
    )

    env = {"CODEX_HOME": "/tmp/codex", "OTHER": "value"}
    _sync_authenticated_provider_to_vault(ExternalAgentProviderName.CODEX, env)

    assert captured == [env]


def test_sync_authenticated_provider_to_vault_persists_gemini_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Mapping[str, str]] = []

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._persist_gemini_auth_files_to_vault",
        lambda env: captured.append(env),  # type: ignore[arg-type]
    )

    env = {"HOME": "/tmp/gemini"}
    _sync_authenticated_provider_to_vault(ExternalAgentProviderName.GEMINI, env)

    assert captured == [env]


def test_forward_login_input_sends_and_resends(monkeypatch) -> None:
    writes: list[tuple[int, bytes]] = []
    monkeypatch.setattr(os, "write", lambda fd, data: writes.append((fd, data)))
    now = 1000.0
    last_input, last_at, awaiting = _forward_login_input(
        1,
        queued_input="code",
        last_sent_input=None,
        last_sent_at=None,
        awaiting_output_after_input=False,
        now=now,
    )
    assert last_input == "code"
    assert awaiting
    last_input2, last_at2, awaiting2 = _forward_login_input(
        1,
        queued_input="code",
        last_sent_input=last_input,
        last_sent_at=now,
        awaiting_output_after_input=True,
        now=now + LOGIN_INPUT_RESEND_SECONDS + 0.1,
    )
    assert last_input2 == "code"


def test_forward_login_input_supports_bare_enter(monkeypatch) -> None:
    writes: list[tuple[int, bytes]] = []
    monkeypatch.setattr(os, "write", lambda fd, data: writes.append((fd, data)))

    last_input, last_at, awaiting = _forward_login_input(
        1,
        queued_input="",
        last_sent_input=None,
        last_sent_at=None,
        awaiting_output_after_input=False,
        now=1000.0,
    )

    assert last_input == ""
    assert last_at == 1000.0
    assert awaiting is True
    assert writes == [(1, b""), (1, b"\r")]


def test_login_detail_variants() -> None:
    assert "Codex" in _initial_login_detail(ExternalAgentProviderName.CODEX)
    assert "Gemini" in _initial_login_detail(ExternalAgentProviderName.GEMINI)
    assert _initial_login_detail(ExternalAgentProviderName.CLAUDE_CODE).startswith(
        "Starting the provider"
    )
    assert "Claude" in _browser_login_detail(ExternalAgentProviderName.CLAUDE_CODE)
    assert "device-auth page" in _browser_login_detail(ExternalAgentProviderName.CODEX)
    assert "verification code" in _browser_login_detail(
        ExternalAgentProviderName.GEMINI
    )
    assert "browser sign-in" in _browser_login_detail("other")  # type: ignore[arg-type]


def test_active_login_session_status(runtime_store: ExternalAgentRuntimeStore) -> None:
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="sess",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    runtime_store.save_login_session(session)
    runtime_store.save_provider_status(
        runtime_store.get_provider_status(
            ExternalAgentProviderName.CLAUDE_CODE
        ).model_copy(update={"active_session_id": "sess"})
    )
    status = _active_login_session_status(
        runtime_store,
        ExternalAgentProviderName.CLAUDE_CODE,
        checked_at=now,
    )
    assert status is not None
    assert status.active_session_id == "sess"


def test_active_login_session_status_returns_none_without_active_session(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    now = datetime.now(UTC)
    assert (
        _active_login_session_status(
            runtime_store,
            ExternalAgentProviderName.CLAUDE_CODE,
            checked_at=now,
        )
        is None
    )


def test_active_login_session_status_returns_none_for_terminal_session(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="sess",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude",
        state=ExternalAgentLoginSessionState.AUTHENTICATED,
        created_at=now,
        updated_at=now,
    )
    runtime_store.save_login_session(session)
    runtime_store.save_provider_status(
        runtime_store.get_provider_status(
            ExternalAgentProviderName.CLAUDE_CODE
        ).model_copy(update={"active_session_id": "sess"})
    )

    assert (
        _active_login_session_status(
            runtime_store,
            ExternalAgentProviderName.CLAUDE_CODE,
            checked_at=now,
        )
        is None
    )


def test_forward_login_input_returns_existing_state_when_no_send_is_needed() -> None:
    assert _forward_login_input(
        1,
        queued_input="code",
        last_sent_input="code",
        last_sent_at=1000.0,
        awaiting_output_after_input=False,
        now=1000.5,
    ) == ("code", 1000.0, False)


def test_drain_login_output_handles_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        os, "read", lambda fd, size: (_ for _ in ()).throw(OSError("boom"))
    )
    screen = SimpleNamespace(write=lambda chunk: None)
    outputs: list[tuple[str, str | None, str | None]] = []

    result = _drain_login_output(
        1,
        terminal_screen=screen,
        raw_output="existing",
        output="visible",
        auth_url=None,
        device_code=None,
        auth_token=None,
        on_output=lambda output, auth_url, device_code: outputs.append(
            (output, auth_url, device_code)
        ),
    )

    assert result == ("existing", "visible", None, None, None)
    assert outputs == []


def test_terminate_process_group_returns_when_process_already_exited() -> None:
    process = SimpleNamespace(poll=lambda: 0)
    _terminate_process_group(process)  # type: ignore[arg-type]


def test_terminate_process_group_ignores_missing_process_on_sigterm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def wait(timeout=0):
        return None

    process = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    monkeypatch.setattr(
        os,
        "killpg",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()),
    )

    _terminate_process_group(process)  # type: ignore[arg-type]


def test_terminate_process_group_handles_timeout_and_missing_process_on_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def wait(timeout=0):
        calls.append(timeout)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd="cmd", timeout=timeout)

    process = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    kill_calls: list[tuple[int, signal.Signals]] = []

    def killpg(pid: int, sig: signal.Signals) -> None:
        kill_calls.append((pid, sig))
        if sig == signal.SIGKILL:
            raise ProcessLookupError()

    monkeypatch.setattr(os, "killpg", killpg)

    _terminate_process_group(process)  # type: ignore[arg-type]

    assert kill_calls[0] == (123, signal.SIGTERM)
    assert kill_calls[1] == (123, signal.SIGKILL)


def test_terminate_process_group_waits_after_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    def wait(timeout=0):
        calls.append(timeout)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(cmd="cmd", timeout=timeout)

    process = SimpleNamespace(pid=123, poll=lambda: None, wait=wait)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: None)

    _terminate_process_group(process)  # type: ignore[arg-type]

    assert calls == [2, 2]


def test_gemini_auth_file_paths_use_home(tmp_path: Path) -> None:
    paths = _gemini_auth_file_paths({"HOME": str(tmp_path)})
    assert paths[GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME] == (
        tmp_path / ".gemini" / "google_accounts.json"
    )
    assert paths[GEMINI_STATE_JSON_CREDENTIAL_NAME] == (
        tmp_path / ".gemini" / "state.json"
    )
    assert paths[GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME] == (
        tmp_path / ".gemini" / "oauth_creds.json"
    )


def test_delete_file_if_present_handles_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    _delete_file_if_present(missing)
    assert not missing.exists()


def test_logout_provider_cli_returns_early_without_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess.run should not be called"),
    )

    _logout_provider_cli(ExternalAgentProviderName.CODEX, None, {})


@pytest.mark.parametrize(
    ("provider_id", "expected_command"),
    [
        (
            ExternalAgentProviderName.CLAUDE_CODE,
            ["/tmp/runtime/bin/tool", "auth", "logout"],
        ),
        (
            ExternalAgentProviderName.CODEX,
            ["/tmp/runtime/bin/tool", "logout"],
        ),
    ],
)
def test_logout_provider_cli_runs_provider_specific_command(
    monkeypatch: pytest.MonkeyPatch,
    provider_id: ExternalAgentProviderName,
    expected_command: list[str],
) -> None:
    runtime = SimpleNamespace(executable_path=Path("/tmp/runtime/bin/tool"))
    calls: list[tuple[list[str], Mapping[str, str]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> None:
        calls.append((command, kwargs["env"]))

    monkeypatch.setattr(subprocess, "run", fake_run)

    _logout_provider_cli(provider_id, runtime, {"HOME": "/tmp/home"})

    assert calls == [(expected_command, {"HOME": "/tmp/home"})]


def test_logout_provider_cli_ignores_subprocess_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = SimpleNamespace(executable_path=Path("/tmp/runtime/bin/tool"))
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(subprocess.SubprocessError()),
    )

    _logout_provider_cli(ExternalAgentProviderName.CODEX, runtime, {})


def test_logout_provider_cli_ignores_unsupported_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("subprocess.run should not be called"),
    )
    runtime = SimpleNamespace(executable_path=Path("/tmp/runtime/bin/tool"))

    _logout_provider_cli(ExternalAgentProviderName.GEMINI, runtime, {})


def test_clear_provider_auth_state_removes_gemini_secrets_and_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    gemini_home = tmp_path / ".gemini"
    gemini_home.mkdir()
    for name in (
        "google_accounts.json",
        "state.json",
        "oauth_creds.json",
        "trustedFolders.json",
        "installation_id",
    ):
        (gemini_home / name).write_text("{}", encoding="utf-8")

    sentinel_vault = object()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: sentinel_vault,
    )
    deleted: list[tuple[Any, str]] = []

    def fake_delete(vault: Any, credential_name: str) -> bool:
        deleted.append((vault, credential_name))
        return True

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.delete_external_agent_secret",
        fake_delete,
    )

    _clear_provider_auth_state(
        ExternalAgentProviderName.GEMINI,
        {"HOME": str(tmp_path)},
    )

    assert deleted == [
        (sentinel_vault, GEMINI_AUTH_JSON_CREDENTIAL_NAME),
        (sentinel_vault, GEMINI_GOOGLE_ACCOUNTS_JSON_CREDENTIAL_NAME),
        (sentinel_vault, GEMINI_STATE_JSON_CREDENTIAL_NAME),
        (sentinel_vault, GEMINI_OAUTH_CREDS_JSON_CREDENTIAL_NAME),
    ]
    assert not gemini_home.exists()


def test_clear_provider_auth_state_removes_claude_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel_vault = object()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: sentinel_vault,
    )
    deleted: list[tuple[Any, str]] = []

    def fake_delete(vault: Any, credential_name: str) -> bool:
        deleted.append((vault, credential_name))
        return True

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.delete_external_agent_secret",
        fake_delete,
    )

    _clear_provider_auth_state(ExternalAgentProviderName.CLAUDE_CODE, {})

    assert deleted == [(sentinel_vault, "CLAUDE_CODE_OAUTH_TOKEN")]


def test_clear_provider_auth_state_removes_codex_secret_and_auth_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    sentinel_vault = object()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: sentinel_vault,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._codex_auth_file_path",
        lambda environ: auth_file,
    )
    deleted: list[tuple[Any, str]] = []

    def fake_delete(vault: Any, credential_name: str) -> bool:
        deleted.append((vault, credential_name))
        return True

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.delete_external_agent_secret",
        fake_delete,
    )

    _clear_provider_auth_state(ExternalAgentProviderName.CODEX, {})

    assert deleted == [(sentinel_vault, "CODEX_AUTH_JSON")]
    assert not auth_file.exists()


def test_clear_provider_auth_state_ignores_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: object(),
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.delete_external_agent_secret",
        lambda *args, **kwargs: pytest.fail("no secrets should be deleted"),
    )

    _clear_provider_auth_state("other", {})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("provider_id", "expected"),
    [
        (
            ExternalAgentProviderName.CLAUDE_CODE,
            {"CLAUDE_CODE_OAUTH_TOKEN": ""},
        ),
        (
            ExternalAgentProviderName.CODEX,
            {"CODEX_AUTH_JSON": ""},
        ),
        (
            ExternalAgentProviderName.GEMINI,
            {
                "GEMINI_AUTH_JSON": "",
                "GEMINI_GOOGLE_ACCOUNTS_JSON": "",
                "GEMINI_STATE_JSON": "",
                "GEMINI_OAUTH_CREDS_JSON": "",
            },
        ),
        ("other", {}),
    ],
)
def test_provider_environment_reset_variants(
    provider_id: ExternalAgentProviderName | str,
    expected: dict[str, str],
) -> None:
    assert _provider_environment_reset(provider_id) == expected  # type: ignore[arg-type]


def test_mark_provider_session_disconnected_clears_active_session(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="sess",
        provider=ExternalAgentProviderName.GEMINI,
        display_name="Gemini CLI",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    runtime_store.save_login_session(session)
    runtime_store.save_login_input("sess", "ABCD-1234")
    runtime_store.save_provider_status(
        runtime_store.get_provider_status(ExternalAgentProviderName.GEMINI).model_copy(
            update={"active_session_id": "sess"}
        )
    )

    _mark_provider_session_disconnected(
        runtime_store,
        ExternalAgentProviderName.GEMINI,
    )

    updated_session = runtime_store.get_login_session("sess")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.FAILED
    assert updated_session.detail == "Disconnected by operator."
    assert runtime_store.get_login_input("sess") is None
    assert (
        runtime_store.get_provider_status(
            ExternalAgentProviderName.GEMINI
        ).active_session_id
        is None
    )


def test_mark_provider_session_disconnected_with_terminal_session_only_clears_input(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="sess",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.AUTHENTICATED,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )
    runtime_store.save_login_session(session)
    runtime_store.save_login_input("sess", "CODE-1234")
    runtime_store.save_provider_status(
        runtime_store.get_provider_status(ExternalAgentProviderName.CODEX).model_copy(
            update={"active_session_id": "sess"}
        )
    )

    _mark_provider_session_disconnected(
        runtime_store,
        ExternalAgentProviderName.CODEX,
    )

    updated_session = runtime_store.get_login_session("sess")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.AUTHENTICATED
    assert runtime_store.get_login_input("sess") is None
    assert (
        runtime_store.get_provider_status(
            ExternalAgentProviderName.CODEX
        ).active_session_id
        is None
    )


def test_mark_provider_session_disconnected_without_active_session(
    runtime_store: ExternalAgentRuntimeStore,
) -> None:
    _mark_provider_session_disconnected(
        runtime_store,
        ExternalAgentProviderName.CLAUDE_CODE,
    )

    assert (
        runtime_store.get_provider_status(
            ExternalAgentProviderName.CLAUDE_CODE
        ).active_session_id
        is None
    )


def test_persist_gemini_auth_files_returns_early_when_no_files_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """_persist_gemini_auth_files_to_vault returns without upserting when no auth files exist."""  # noqa: E501
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: object(),
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.upsert_external_agent_secret",
        lambda *args, **kwargs: pytest.fail("should not upsert when no payload"),
    )

    # HOME points to a tmp dir with no .gemini directory at all
    _persist_gemini_auth_files_to_vault({"HOME": str(tmp_path)})


def test_delete_directory_if_present_is_noop_when_missing(tmp_path: Path) -> None:
    """_delete_directory_if_present silently returns when the path does not exist."""
    missing = tmp_path / "nonexistent_dir"
    _delete_directory_if_present(missing)
    assert not missing.exists()


def test_clear_provider_auth_state_claude_code_with_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 998: _clear_provider_auth_state for CLAUDE_CODE passes workspace_id to delete."""
    from orcheo_backend.worker import external_agents as ea_mod

    calls: list[dict] = []

    def fake_delete(vault, *, credential_name, workspace_id=None):
        calls.append({"credential_name": credential_name, "workspace_id": workspace_id})

    monkeypatch.setattr(ea_mod, "get_vault", lambda: None)
    monkeypatch.setattr(ea_mod, "delete_external_agent_secret", fake_delete)

    _clear_provider_auth_state(
        ExternalAgentProviderName.CLAUDE_CODE,
        {},
        workspace_id="ws-1",
    )

    assert len(calls) == 1
    assert calls[0]["workspace_id"] == "ws-1"
    assert calls[0]["credential_name"] == ea_mod.CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME


def test_clear_provider_auth_state_codex_with_workspace_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Line 1012: _clear_provider_auth_state for CODEX passes workspace_id to delete."""
    from orcheo_backend.worker import external_agents as ea_mod

    calls: list[dict] = []

    def fake_delete(vault, *, credential_name, workspace_id=None):
        calls.append({"credential_name": credential_name, "workspace_id": workspace_id})

    monkeypatch.setattr(ea_mod, "get_vault", lambda: None)
    monkeypatch.setattr(ea_mod, "delete_external_agent_secret", fake_delete)
    monkeypatch.setattr(ea_mod, "_delete_file_if_present", lambda path: None)
    monkeypatch.setattr(
        ea_mod, "_codex_auth_file_path", lambda environ: tmp_path / "codex-auth.json"
    )

    _clear_provider_auth_state(
        ExternalAgentProviderName.CODEX,
        {},
        workspace_id="ws-1",
    )

    assert len(calls) == 1
    assert calls[0]["workspace_id"] == "ws-1"
    assert calls[0]["credential_name"] == ea_mod.CODEX_AUTH_JSON_CREDENTIAL_NAME


@pytest.mark.asyncio
async def test_disconnect_external_agent_async_workspace_id_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lines 1105, 1116, 1129, 1138-1143: disconnect_external_agent_async with workspace_id."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from orcheo_backend.worker import external_agents as ea_mod

    workspace_id = "ws-disconnect"
    provider_name = "claude_code"

    fake_store = MagicMock()
    fake_store.save_provider_environment = MagicMock()

    fake_manager = MagicMock()
    fake_manager.inspect_runtime.return_value = (MagicMock(), None)
    fake_manager.environment_for_provider.return_value = {}
    fake_manager.save_provider_environment = MagicMock()

    clear_calls: list[dict] = []
    mark_calls: list[dict] = []

    def fake_clear(provider_id, provider_environ, *, workspace_id=None):
        clear_calls.append({"workspace_id": workspace_id})

    def fake_mark(store, provider_id, *, workspace_id=None):
        mark_calls.append({"workspace_id": workspace_id})

    with (
        patch.object(
            ea_mod, "get_external_agent_runtime_store", return_value=fake_store
        ),
        patch(
            "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
            return_value=fake_manager,
        ),
        patch.object(ea_mod, "_worker_provider_environment", return_value={}),
        patch.object(ea_mod, "_logout_provider_cli"),
        patch.object(ea_mod, "_clear_provider_auth_state", side_effect=fake_clear),
        patch.object(ea_mod, "_provider_environment_reset", return_value={}),
        patch.object(
            ea_mod, "_mark_provider_session_disconnected", side_effect=fake_mark
        ),
        patch.object(
            ea_mod,
            "refresh_external_agent_status_async",
            new_callable=AsyncMock,
            return_value={"status": "needs_login"},
        ),
    ):
        result = await ea_mod.disconnect_external_agent_async(
            provider_name, workspace_id=workspace_id
        )

    assert result == {"status": "needs_login"}
    assert clear_calls and clear_calls[0]["workspace_id"] == workspace_id
    assert mark_calls and mark_calls[0]["workspace_id"] == workspace_id
    fake_manager.save_provider_environment.assert_called_once()
    fake_store.save_provider_environment.assert_called_once()


@pytest.mark.asyncio
async def test_start_external_agent_login_async_persists_with_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lines 1594, 1615: workspace_id not-None paths in start_external_agent_login_async."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from orcheo_backend.worker import external_agents as ea_mod

    workspace_id = "ws-login"
    provider_name = "claude_code"
    session_id = "sess-1"

    fake_session = MagicMock()
    fake_session.workspace_id = workspace_id

    fake_store = MagicMock()
    fake_store.get_login_session.return_value = fake_session
    fake_store.save_login_session.return_value = fake_session
    fake_store.get_login_input.return_value = None

    fake_runtime = MagicMock()
    fake_runtime.version = "1.0"
    fake_runtime.executable_path = Path("/usr/bin/claude")

    fake_manifest = MagicMock()
    fake_manifest.last_auth_ok_at = None

    fake_probe_not_auth = MagicMock()
    fake_probe_not_auth.authenticated = False
    fake_probe_not_auth.message = ""

    fake_probe_auth = MagicMock()
    fake_probe_auth.authenticated = True

    fake_provider = MagicMock()
    fake_provider.probe_auth.side_effect = [fake_probe_not_auth, fake_probe_auth]
    fake_provider.oauth_login_command.return_value = ["echo", "done"]
    fake_provider.build_environment.return_value = {}

    fake_manager = MagicMock()
    fake_manager.inspect_runtime.return_value = (fake_runtime, fake_manifest)
    fake_manager.environment_for_provider.return_value = {}
    fake_manager.get_provider.return_value = fake_provider
    fake_manager.mark_auth_success.return_value = fake_manifest

    fake_result = MagicMock()
    fake_result.auth_token = "sk-ant-token123"
    fake_result.auth_url = None
    fake_result.device_code = None
    fake_result.output = "done"
    fake_result.timed_out = False

    persist_calls: list[dict] = []
    sync_calls: list[dict] = []

    with (
        patch.object(
            ea_mod, "get_external_agent_runtime_store", return_value=fake_store
        ),
        patch(
            "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
            return_value=fake_manager,
        ),
        patch.object(ea_mod, "_worker_provider_environment", return_value={}),
        patch.object(ea_mod, "_run_login_command", return_value=fake_result),
        patch.object(
            ea_mod,
            "_persist_claude_oauth_token_to_vault",
            side_effect=lambda token, **kw: persist_calls.append(kw),
        ),
        patch.object(
            ea_mod,
            "_sync_authenticated_provider_to_vault",
            side_effect=lambda *a, **kw: sync_calls.append(kw),
        ),
        patch.object(ea_mod, "_save_ready_provider_status"),
        patch.object(ea_mod, "_trim_recent_output", return_value="done"),
    ):
        result = await ea_mod.start_external_agent_login_async(
            provider_name, session_id, workspace_id=workspace_id
        )

    assert result == {"status": "authenticated"}
    assert persist_calls and persist_calls[0].get("workspace_id") == workspace_id
    assert sync_calls and sync_calls[0].get("workspace_id") == workspace_id


@pytest.mark.asyncio
async def test_start_external_agent_login_async_codex_persists_with_workspace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Line 1603: CODEX provider persists auth JSON with workspace_id."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from orcheo_backend.worker import external_agents as ea_mod

    workspace_id = "ws-codex"
    provider_name = "codex"
    session_id = "sess-codex"

    fake_session = MagicMock()
    fake_session.workspace_id = workspace_id

    fake_store = MagicMock()
    fake_store.get_login_session.return_value = fake_session
    fake_store.save_login_session.return_value = fake_session
    fake_store.get_login_input.return_value = None

    fake_runtime = MagicMock()
    fake_runtime.version = "1.0"
    fake_runtime.executable_path = Path("/usr/bin/codex")

    fake_manifest = MagicMock()
    fake_manifest.last_auth_ok_at = None

    fake_probe_not_auth = MagicMock()
    fake_probe_not_auth.authenticated = False
    fake_probe_not_auth.message = ""

    fake_probe_auth = MagicMock()
    fake_probe_auth.authenticated = True

    fake_provider = MagicMock()
    fake_provider.probe_auth.side_effect = [fake_probe_not_auth, fake_probe_auth]
    fake_provider.oauth_login_command.return_value = ["echo", "done"]
    fake_provider.build_environment.return_value = {}

    fake_manager = MagicMock()
    fake_manager.inspect_runtime.return_value = (fake_runtime, fake_manifest)
    fake_manager.environment_for_provider.return_value = {}
    fake_manager.get_provider.return_value = fake_provider
    fake_manager.mark_auth_success.return_value = fake_manifest

    fake_result = MagicMock()
    fake_result.auth_token = None
    fake_result.auth_url = None
    fake_result.device_code = None
    fake_result.output = "done"
    fake_result.timed_out = False

    codex_calls: list[dict] = []
    sync_calls: list[dict] = []

    with (
        patch.object(
            ea_mod, "get_external_agent_runtime_store", return_value=fake_store
        ),
        patch(
            "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
            return_value=fake_manager,
        ),
        patch.object(ea_mod, "_worker_provider_environment", return_value={}),
        patch.object(ea_mod, "_run_login_command", return_value=fake_result),
        patch.object(
            ea_mod,
            "_persist_codex_auth_json_to_vault",
            side_effect=lambda environ, **kw: codex_calls.append(kw),
        ),
        patch.object(
            ea_mod,
            "_sync_authenticated_provider_to_vault",
            side_effect=lambda *a, **kw: sync_calls.append(kw),
        ),
        patch.object(ea_mod, "_save_ready_provider_status"),
        patch.object(ea_mod, "_trim_recent_output", return_value="done"),
    ):
        result = await ea_mod.start_external_agent_login_async(
            provider_name, session_id, workspace_id=workspace_id
        )

    assert result == {"status": "authenticated"}
    assert codex_calls and codex_calls[0].get("workspace_id") == workspace_id
    assert sync_calls and sync_calls[0].get("workspace_id") == workspace_id
