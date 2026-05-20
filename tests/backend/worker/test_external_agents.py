"""Tests for worker-side external agent login helpers."""

from __future__ import annotations
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest
from orcheo.vault import InMemoryCredentialVault
from orcheo_backend.app.external_agent_auth import (
    CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
    CODEX_AUTH_JSON_CREDENTIAL_NAME,
)
from orcheo_backend.app.external_agent_runtime_store import ExternalAgentRuntimeStore
from orcheo_backend.app.schemas.system import (
    ExternalAgentLoginSession,
    ExternalAgentLoginSessionState,
    ExternalAgentProviderName,
    ExternalAgentProviderState,
    ExternalAgentProviderStatus,
)
from orcheo_backend.worker.external_agents import _extract_auth_url


def test_extract_auth_url_prefers_public_url_over_localhost() -> None:
    """Public auth links should win over worker-local callback URLs."""
    output = """
Starting local login server on http://localhost:1455.
If your browser did not open, navigate to this URL to authenticate:
https://auth.openai.com/oauth/authorize?response_type=code
"""

    assert (
        _extract_auth_url(output)
        == "https://auth.openai.com/oauth/authorize?response_type=code"
    )


def test_extract_auth_url_stops_before_next_prompt_line() -> None:
    """Wrapped URLs should not absorb the next terminal prompt line."""
    output = """
Browser didn't open? Use the url below to signin

https://claude.com/cai/oauth/authorize?code=true&client_id=abc123&response_type=code
&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback
Pastecodehereifprompted>
"""

    assert _extract_auth_url(output) == (
        "https://claude.com/cai/oauth/authorize?code=true&client_id=abc123"
        "&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com"
        "%2Foauth%2Fcode%2Fcallback"
    )


def test_extract_auth_url_skips_one_blank_wrapped_line() -> None:
    """Claude's wrapped OAuth URL should survive one blank spacer line."""
    output = """
https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope=user%3Ainference&code_challenge=1G_Pjb3eDvtNbl

jeSRFodV_IfJ_ooj5nfszVier81_w&code_challenge_method=S256&state=bjdD4VJiqv6dlffcqzUmdj9bUUu_eHTAASug9n8lhbg
"""

    assert _extract_auth_url(output) == (
        "https://claude.com/cai/oauth/authorize?code=true&client_id="
        "9d1c250a-e61b-44d9-88ed-5944d1962f5e&response_type=code&redirect_uri="
        "https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&scope="
        "user%3Ainference&code_challenge=1G_Pjb3eDvtNbl"
        "jeSRFodV_IfJ_ooj5nfszVier81_w&code_challenge_method=S256&state="
        "bjdD4VJiqv6dlffcqzUmdj9bUUu_eHTAASug9n8lhbg"
    )


def test_runtime_store_persists_provider_environment() -> None:
    """Shared runtime store should round-trip provider env vars."""
    store = ExternalAgentRuntimeStore()
    store._redis = None

    store.save_provider_environment(
        ExternalAgentProviderName.CLAUDE_CODE,
        {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-shared"},
    )

    assert store.get_provider_environment(ExternalAgentProviderName.CLAUDE_CODE) == {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-shared"
    }


def test_run_login_command_finishes_when_auth_becomes_valid() -> None:
    """Interactive login should complete once auth succeeds, even silently."""
    from orcheo_backend.worker.external_agents import _run_login_command

    def on_output(_: str, __: str | None, ___: str | None) -> None:
        return None

    checks = 0

    def is_authenticated() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    started_at = time.monotonic()
    result = _run_login_command(
        [
            "python3",
            "-c",
            (
                "import time; "
                "print('Opening browser to sign in...', flush=True); "
                "time.sleep(30)"
            ),
        ],
        env={},
        on_output=on_output,
        is_authenticated=is_authenticated,
        timeout_seconds=10,
    )

    assert "Opening browser to sign in..." in result.output
    assert result.timed_out is False
    assert time.monotonic() - started_at < 10


def test_run_login_command_redacts_oauth_token_output() -> None:
    """Worker session output should redact minted Claude OAuth tokens."""
    from orcheo_backend.worker.external_agents import _run_login_command

    def on_output(_: str, __: str | None, ___: str | None) -> None:
        return None

    result = _run_login_command(
        [
            "python3",
            "-c",
            (
                "print('Your OAuth token (valid for 1 year):', flush=True); "
                "print('sk-ant-oat01-secret-token', flush=True); "
                "print('Store this token securely.', flush=True)"
            ),
        ],
        env={},
        on_output=on_output,
        timeout_seconds=10,
    )

    assert result.auth_token == "sk-ant-oat01-secret-token"
    assert "sk-ant-oat01-secret-token" not in result.output
    assert "[redacted worker auth token]" in result.output


def test_run_login_command_extracts_wrapped_token_before_process_exit() -> None:
    """Final Claude token output should be captured even if the process exits fast."""
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import os, sys, termios, time, tty\n"
        "fd = sys.stdin.fileno()\n"
        "old = termios.tcgetattr(fd)\n"
        "tty.setraw(fd)\n"
        "sys.stdout.write('Paste code here if prompted> ')\n"
        "sys.stdout.flush()\n"
        "try:\n"
        "    while True:\n"
        "        ch = os.read(fd, 1)\n"
        "        if ch == b'\\r':\n"
        "            break\n"
        "finally:\n"
        "    termios.tcsetattr(fd, termios.TCSADRAIN, old)\n"
        "print('\\nYour OAuth token (valid for 1 year):\\n', flush=True)\n"
        "print('sk-ant-oat01-verylongtokenpartone', flush=True)\n"
        "print('parttwo', flush=True)\n"
        "time.sleep(0.05)\n"
        "print('\\nStore this token securely. "
        "You won\\'t be able to see it again.', flush=True)\n"
    )
    queued = {"value": "ABCD-1234"}

    def on_output(_: str, __: str | None, ___: str | None) -> None:
        return None

    def consume_input(clear: bool) -> str | None:
        if clear:
            queued["value"] = None
            return None
        return queued["value"]

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=on_output,
        consume_input=consume_input,
        timeout_seconds=10,
    )

    assert result.auth_token == "sk-ant-oat01-verylongtokenpartoneparttwo"
    assert "sk-ant-oat01-verylongtokenpartone" not in result.output
    assert "[redacted worker auth token]" in result.output


def test_run_login_command_uses_visible_terminal_screen_for_rewritten_token() -> None:
    """Token extraction should keep the latest rewritten token within one block."""
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import sys\n"
        "sys.stdout.write('Your OAuth token (valid for 1 year):\\n')\n"
        "sys.stdout.write('sk-ant-oat01-stale-token')\n"
        "sys.stdout.flush()\n"
        "sys.stdout.write('\\r')\n"
        "sys.stdout.write('sk-ant-oat01-fresh-token')\n"
        "sys.stdout.write('\\nStore this token securely.\\n')\n"
        "sys.stdout.flush()\n"
    )

    def on_output(_: str, __: str | None, ___: str | None) -> None:
        return None

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=on_output,
        timeout_seconds=10,
    )

    assert result.auth_token == "sk-ant-oat01-fresh-token"
    assert "sk-ant-oat01-stale-token" not in result.output
    assert "[redacted worker auth token]" in result.output


def test_run_login_command_ignores_unsupported_escape_sequences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsupported ANSI escapes should not try to write a local ./log file."""
    from orcheo_backend.worker.external_agents import _run_login_command

    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_dir.chmod(0o555)
    monkeypatch.chdir(readonly_dir)

    result = _run_login_command(
        [
            "python3",
            "-c",
            (
                "import sys; "
                "sys.stdout.write('\\x1bc'); "
                "sys.stdout.write('Opening browser to sign in...\\n'); "
                "sys.stdout.flush()"
            ),
        ],
        env={},
        on_output=lambda _output, _auth_url, _device_code: None,
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert "Opening browser to sign in..." in result.output
    assert not (readonly_dir / "log").exists()


def test_extract_visible_auth_token_rejects_masked_claude_token_block() -> None:
    """Rendered screen output should not mint fake tokens from masked lines."""
    from orcheo_backend.worker.external_agents import _extract_visible_auth_token

    output = (
        "Your OAuth token (valid for 1 year):\n"
        "sk-ant-oat01-AAAA*********JFZmvs\n"
        "Store this token securely.\n"
    )

    assert _extract_visible_auth_token(output) is None


def test_extract_worker_auth_token_prefers_raw_unmasked_token() -> None:
    """Raw PTY output should win when the rendered screen shows a masked token."""
    from orcheo_backend.worker.external_agents import _extract_worker_auth_token

    raw_output = (
        "Your OAuth token (valid for 1 year):\n"
        "sk-ant-oat01-AAAABBBBCCCCDDDDEEEE\n"
        "FFFFGGGGHHHHIIIIJJJJ\n"
        "Store this token securely.\n"
    )
    visible_output = (
        "Your OAuth token (valid for 1 year):\n"
        "sk-ant-oat01-AAAABBBB*********JFZmvs\n"
        "Store this token securely.\n"
    )

    assert _extract_worker_auth_token(raw_output, visible_output) == (
        "sk-ant-oat01-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIIIJJJJ"
    )


def test_run_login_command_extracts_token_from_compacted_claude_output() -> None:
    """Claude PTY output can collapse spaces; token extraction should still work."""
    from orcheo_backend.worker.external_agents import _extract_auth_token

    output = (
        "Long-livedauthenticationtokencreatedsuccessfully!\n"
        "Your OAuth tokn (valid for 1 year):\n"
        "sk-ant-oat01-AAAABBBBCCCC\n"
        "DDDDEEEEFFFF\n"
        "Storethistokensecurely.Youwon'tbeabletoseeitagain.\n"
    )

    assert _extract_auth_token(output) == "sk-ant-oat01-AAAABBBBCCCCDDDDEEEEFFFF"


def test_extract_auth_token_ignores_unscoped_token_like_output() -> None:
    """Random token-like strings should not be persisted as Claude OAuth tokens."""
    from orcheo_backend.worker.external_agents import _extract_auth_token

    output = "unexpected debug output sk-ant-not-a-real-oauth-token"

    assert _extract_auth_token(output) is None


def test_extract_auth_token_prefers_explicit_oauth_block_over_earlier_token() -> None:
    """The final minted OAuth token block should win over earlier token-like text."""
    from orcheo_backend.worker.external_agents import _extract_auth_token

    output = (
        "unexpected sk-ant-stale-token\n"
        "Your OAuth token (valid for 1 year):\n"
        "sk-ant-oat01-fresh-token\n"
        "Store this token securely.\n"
    )

    assert _extract_auth_token(output) == "sk-ant-oat01-fresh-token"


def test_extract_auth_token_from_realistic_claude_terminal_output() -> None:
    """Claude PTY control characters and wraps should not break token extraction."""
    from orcheo_backend.worker.external_agents import _extract_auth_token, _strip_ansi

    sample = (
        "\x1b[1BYour OAuth tok\x1cn (valid for 1 year):\r\n"
        "\x1b[1Bsk-ant-oat01-KchzHxrSaiGhhAJzDpPvnUSIkTu53Ccs1FfMpIzU_"
        "kyjOlEzgyEBOcztFJEnGbAeaNv\r\n"
        "\x1b[1BUevvlND\x1cwP7tnbNlF\x1cw-lk7qMwAA\r\n"
        "\x1b[1BStore\x1cthis\x1ctoken\x1csecurely.\x1cYou\x1cwon't"
        "\x1cbe\x1cable\x1cto\x1csee\x1cit\x1cagain.\r\n"
    )

    assert _extract_auth_token(_strip_ansi(sample)) == (
        "sk-ant-oat01-KchzHxrSaiGhhAJzDpPvnUSIkTu53Ccs1FfMpIzU_"
        "kyjOlEzgyEBOcztFJEnGbAeaNvUevvlNDwP7tnbNlFw-lk7qMwAA"
    )


def test_extract_auth_token_from_setup_token_output_matches_manual_vm_shape() -> None:
    """Wrapped setup-token output should reconstruct the full token with no drops."""
    from orcheo_backend.worker.external_agents import _extract_auth_token

    output = (
        "✓ Long-lived authentication token created successfully!\n\n"
        "Your OAuth token (valid for 1 year):\n\n"
        "sk-ant-oat01-uotT3f8Nx5pzzxXkQx8uq5pFbuYUYQtmdQnFNvJncndBq7qacSnbCPeod_6E9ruJD4Pe\n"
        "oyuK7ALfpU9CeGy67A-7iX7ZgAA\n\n"
        "Store this token securely. You won't be able to see it again.\n\n"
        "Use this token by setting: export CLAUDE_CODE_OAUTH_TOKEN=<token>\n"
    )

    assert _extract_auth_token(output) == (
        "sk-ant-oat01-uotT3f8Nx5pzzxXkQx8uq5pFbuYUYQtmdQnFNvJncndBq7qacSnbCPeod_6E9ruJD4Pe"
        "oyuK7ALfpU9CeGy67A-7iX7ZgAA"
    )


def test_extract_auth_token_ignores_non_token_text_within_token_block() -> None:
    """Only token segments inside the token block should be concatenated."""
    from orcheo_backend.worker.external_agents import _extract_auth_token

    output = (
        "Your OAuth token (valid for 1 year):\n"
        "copy this carefully\n"
        "sk-ant-oat01-AAAABBBB\n"
        "CCCCDDDD\n"
        "Store this token securely.\n"
    )

    assert _extract_auth_token(output) == "sk-ant-oat01-AAAABBBBCCCCDDDD"


def test_extract_auth_token_keeps_suffix_before_store_marker_on_same_line() -> None:
    """Token suffix should not be dropped if the storage warning shares a line."""
    from orcheo_backend.worker.external_agents import _extract_auth_token

    output = (
        "Your OAuth token (valid for 1 year):\n"
        "sk-ant-oat01-AAAABBBB\n"
        "CCCCDDDDZZStore this token securely. You won't be able to see it again.\n"
    )

    assert _extract_auth_token(output) == "sk-ant-oat01-AAAABBBBCCCCDDDDZZ"


def test_extract_auth_token_when_header_and_token_share_the_same_line() -> None:
    """Token extraction should work when Claude starts the token after the colon."""
    from orcheo_backend.worker.external_agents import _extract_auth_token

    output = (
        "Your OAuth token (valid for 1 year): sk-ant-oat01-AAAABBBB\n"
        "CCCCDDDD\n"
        "Store this token securely.\n"
    )

    assert _extract_auth_token(output) == "sk-ant-oat01-AAAABBBBCCCCDDDD"


def test_run_login_command_submits_carriage_return_for_raw_tty_input() -> None:
    """Worker input forwarding should submit Enter as carriage return."""
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import os, sys, termios, tty\n"
        "fd = sys.stdin.fileno()\n"
        "old = termios.tcgetattr(fd)\n"
        "tty.setraw(fd)\n"
        "sys.stdout.write('Paste code> ')\n"
        "sys.stdout.flush()\n"
        "chars = []\n"
        "try:\n"
        "    while True:\n"
        "        ch = os.read(fd, 1)\n"
        "        if not ch:\n"
        "            break\n"
        "        if ch == b'\\r':\n"
        "            print('ENTER-CR:' + ''.join(chars), flush=True)\n"
        "            break\n"
        "        if ch == b'\\n':\n"
        "            print('ENTER-LF:' + ''.join(chars), flush=True)\n"
        "            break\n"
        "        chars.append(ch.decode('utf-8', 'replace'))\n"
        "finally:\n"
        "    termios.tcsetattr(fd, termios.TCSADRAIN, old)\n"
    )
    queued = {"value": "ABCD-1234"}

    def on_output(_: str, __: str | None, ___: str | None) -> None:
        return None

    def consume_input(clear: bool) -> str | None:
        if clear:
            queued["value"] = None
            return None
        return queued["value"]

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=on_output,
        consume_input=consume_input,
        timeout_seconds=10,
    )

    assert "ENTER-CR:ABCD-1234" in result.output
    assert "ENTER-LF:ABCD-1234" not in result.output


def test_run_login_command_times_out_and_sends_heartbeats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.worker import external_agents as external_agents_module

    heartbeats: list[str] = []
    monkeypatch.setattr(external_agents_module, "LOGIN_HEARTBEAT_SECONDS", 0.0)

    result = external_agents_module._run_login_command(
        [
            "python3",
            "-c",
            (
                "import time; "
                "print('Waiting for device auth', flush=True); "
                "time.sleep(30)"
            ),
        ],
        env={},
        on_output=lambda *_: None,
        on_tick=lambda: heartbeats.append("tick"),
        timeout_seconds=1,
    )

    assert result.timed_out is True
    assert heartbeats


def test_run_login_command_clears_input_after_output_and_when_authenticated() -> None:
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import os, sys, termios, tty, time\n"
        "fd = sys.stdin.fileno()\n"
        "old = termios.tcgetattr(fd)\n"
        "tty.setraw(fd)\n"
        "sys.stdout.write('Paste code> ')\n"
        "sys.stdout.flush()\n"
        "chars = []\n"
        "try:\n"
        "    while True:\n"
        "        ch = os.read(fd, 1)\n"
        "        if ch == b'\\r':\n"
        "            print('\\nreceived:' + ''.join(chars), flush=True)\n"
        "            time.sleep(30)\n"
        "            break\n"
        "        chars.append(ch.decode('utf-8', 'replace'))\n"
        "finally:\n"
        "    termios.tcsetattr(fd, termios.TCSADRAIN, old)\n"
    )
    queued = {"value": "ABCD-1234"}
    cleared: list[bool] = []
    authenticated = {"value": False}

    def on_output(output: str, auth_url: str | None, device_code: str | None) -> None:
        del auth_url, device_code
        if "received:ABCD-1234" in output:
            authenticated["value"] = True

    def consume_input(clear: bool) -> str | None:
        if clear:
            cleared.append(True)
            queued["value"] = None
            return None
        return queued["value"]

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=on_output,
        consume_input=consume_input,
        is_authenticated=lambda: authenticated["value"],
        timeout_seconds=10,
    )

    assert result.timed_out is False
    assert len(cleared) >= 2


def test_run_login_command_wraps_paste_when_bracketed_paste_is_enabled() -> None:
    """Long auth codes must be wrapped with bracketed-paste markers."""
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import os, sys, termios, tty\n"
        "fd = sys.stdin.fileno()\n"
        "old = termios.tcgetattr(fd)\n"
        "tty.setraw(fd)\n"
        # Advertise bracketed paste like Ink-based TUIs do.
        "sys.stdout.write('\\x1b[?2004hPaste code> ')\n"
        "sys.stdout.flush()\n"
        "chars = []\n"
        "try:\n"
        "    while True:\n"
        "        ch = os.read(fd, 1)\n"
        "        if ch == b'\\r' and chars and chars[-1] == 'END':\n"
        "            break\n"
        "        chars.append(ch.decode('utf-8', 'replace'))\n"
        "        # Compact recognition of the paste-end marker.\n"
        "        joined = ''.join(chars)\n"
        "        if joined.endswith('\\x1b[201~'):\n"
        "            chars = chars[:-len('\\x1b[201~')] + ['END']\n"
        "finally:\n"
        "    termios.tcsetattr(fd, termios.TCSADRAIN, old)\n"
        "joined = ''.join(chars)\n"
        "assert joined.startswith('\\x1b[200~'), 'missing paste start marker'\n"
        "body = joined[len('\\x1b[200~'):-len('END')]\n"
        "print('PASTE-OK:' + body, flush=True)\n"
    )
    queued = {"value": "A" * 92}

    def consume_input(clear: bool) -> str | None:
        if clear:
            queued["value"] = None
            return None
        return queued["value"]

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=lambda *_: None,
        consume_input=consume_input,
        timeout_seconds=8,
    )

    assert "PASTE-OK:" + "A" * 92 in result.output


def test_run_login_command_terminates_when_claude_token_is_detected() -> None:
    """The login loop should not hang on the post-token 'press any key' prompt."""
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import sys, time\n"
        "sys.stdout.write('Your OAuth token (valid for 1 year):\\n'\n"
        "                 'sk-ant-oat01-FAKE-TOKEN\\n'\n"
        "                 'Store this token securely.\\n'\n"
        "                 'Press any key to exit\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=lambda *_: None,
        timeout_seconds=5,
    )

    assert result.timed_out is False
    assert result.auth_token == "sk-ant-oat01-FAKE-TOKEN"


def test_run_login_command_flags_invalid_claude_code_and_stops() -> None:
    """The login loop should abort when Claude rejects the submitted code."""
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import sys, time\n"
        "sys.stdout.write('OAuth error: Invalid code. "
        "Please make sure the full code was copied\\n'\n"
        "                 'Press Enter to retry.\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=lambda *_: None,
        timeout_seconds=5,
    )

    assert result.timed_out is False
    assert result.invalid_code is True
    assert result.auth_token is None


def test_run_login_command_uses_auto_input_when_no_queued_input() -> None:
    from orcheo_backend.worker.external_agents import _run_login_command

    script = (
        "import os, sys, termios, tty\n"
        "fd = sys.stdin.fileno()\n"
        "old = termios.tcgetattr(fd)\n"
        "tty.setraw(fd)\n"
        "sys.stdout.write('Paste code> ')\n"
        "sys.stdout.flush()\n"
        "chars = []\n"
        "try:\n"
        "    while True:\n"
        "        ch = os.read(fd, 1)\n"
        "        if ch == b'\\r':\n"
        "            print('\\nauto:' + ''.join(chars), flush=True)\n"
        "            break\n"
        "        chars.append(ch.decode('utf-8', 'replace'))\n"
        "finally:\n"
        "    termios.tcsetattr(fd, termios.TCSADRAIN, old)\n"
    )
    auto_inputs: list[str] = []

    result = _run_login_command(
        ["python3", "-c", script],
        env={},
        on_output=lambda *_: None,
        auto_input=lambda output: auto_inputs.append(output) or "ABCD-1234",
        timeout_seconds=10,
    )

    assert "auto:ABCD-1234" in result.output
    assert auto_inputs


def test_run_login_command_finalizer_terminates_live_process_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.worker import external_agents as external_agents_module

    class DummySelector:
        def register(self, *args, **kwargs) -> None:
            return None

        def select(self, timeout: float = 0.0) -> list[object]:
            return []

        def close(self) -> None:
            return None

    class DummyProcess:
        pid = 123

        def poll(self) -> None:
            return None

    monkeypatch.setattr(external_agents_module.pty, "openpty", lambda: (10, 11))
    monkeypatch.setattr(external_agents_module, "_set_pty_size", lambda fd: None)
    monkeypatch.setattr(
        external_agents_module.os, "set_blocking", lambda fd, value: None
    )
    monkeypatch.setattr(
        external_agents_module.selectors,
        "DefaultSelector",
        lambda: DummySelector(),
    )
    monkeypatch.setattr(
        external_agents_module.subprocess,
        "Popen",
        lambda *args, **kwargs: DummyProcess(),
    )
    monkeypatch.setattr(
        external_agents_module,
        "_create_terminal_emulator",
        lambda: object(),
    )
    monkeypatch.setattr(
        external_agents_module,
        "_drain_login_output",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(external_agents_module.os, "close", lambda fd: None)
    terminated: list[int] = []
    monkeypatch.setattr(
        external_agents_module,
        "_terminate_process_group",
        lambda process: terminated.append(process.pid),
    )

    with pytest.raises(RuntimeError, match="boom"):
        external_agents_module._run_login_command(
            ["dummy"],
            env={},
            on_output=lambda *_: None,
            timeout_seconds=1,
        )

    assert terminated == [123]


def test_run_login_command_handles_forced_awaiting_state_without_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.worker import external_agents as external_agents_module

    class DummySelector:
        def register(self, *args, **kwargs) -> None:
            return None

        def select(self, timeout: float = 0.0) -> list[object]:
            return []

        def close(self) -> None:
            return None

    class DummyProcess:
        pid = 123

        def __init__(self) -> None:
            self.poll_calls = 0

        def poll(self) -> int | None:
            self.poll_calls += 1
            return 0 if self.poll_calls >= 2 else None

        def wait(self, timeout: float = 0.0) -> int:
            return 0

    process = DummyProcess()
    drain_results = iter(
        [
            ("", "", None, None, None),
            ("", "new-output", None, None, None),
            ("", "new-output", None, None, None),
        ]
    )
    forward_results = iter(
        [
            ("queued", 1.0, True),
            ("queued", 1.0, False),
        ]
    )
    monotonic_values = iter([0.0, 1.0, 1.0, 1.5, 1.5])

    monkeypatch.setattr(external_agents_module.pty, "openpty", lambda: (10, 11))
    monkeypatch.setattr(external_agents_module, "_set_pty_size", lambda fd: None)
    monkeypatch.setattr(
        external_agents_module.os, "set_blocking", lambda fd, value: None
    )
    monkeypatch.setattr(
        external_agents_module.selectors,
        "DefaultSelector",
        lambda: DummySelector(),
    )
    monkeypatch.setattr(
        external_agents_module.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        external_agents_module,
        "_create_terminal_emulator",
        lambda: object(),
    )
    monkeypatch.setattr(
        external_agents_module,
        "_drain_login_output",
        lambda *args, **kwargs: next(drain_results),
    )
    monkeypatch.setattr(
        external_agents_module,
        "_forward_login_input",
        lambda *args, **kwargs: next(forward_results),
    )
    monkeypatch.setattr(
        external_agents_module.time, "monotonic", lambda: next(monotonic_values)
    )
    monkeypatch.setattr(external_agents_module.os, "close", lambda fd: None)

    result = external_agents_module._run_login_command(
        ["dummy"],
        env={},
        on_output=lambda *_: None,
        consume_input=None,
        timeout_seconds=10,
    )

    assert result.output == "new-output"


@pytest.mark.asyncio
async def test_start_login_uses_claude_oauth_token_from_vault_before_browser_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stored Claude worker auth should skip the interactive login flow."""
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    vault = InMemoryCredentialVault()
    vault.create_credential(
        name=CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME,
        provider="claude_code",
        scopes=["worker"],
        secret="sk-ant-from-vault",
        actor="tester",
    )
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="fresh-claude-login",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude Code",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: vault,
    )

    runtime = ResolvedRuntime(
        provider="claude_code",
        version="2.1.89",
        install_dir=Path("/root/.orcheo/agent-runtimes/claude_code"),
        executable_path=Path("/root/.orcheo/agent-runtimes/claude"),
        package_name="@anthropic-ai/claude-code",
    )
    manifest = RuntimeManifest(
        provider="claude_code",
        provider_root=Path("/root/.orcheo/agent-runtimes/claude_code"),
    )
    provider = MagicMock()
    provider.probe_auth.return_value = AuthProbeResult(status=AuthStatus.AUTHENTICATED)
    provider.oauth_login_command.return_value = ["/root/.orcheo/agent-runtimes/claude"]

    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-from-vault"
    }
    manager.mark_auth_success.return_value = MagicMock(last_auth_ok_at=now)
    captured_manager_kwargs: dict[str, object] = {}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: captured_manager_kwargs.update(kwargs) or manager,
    )
    run_login = MagicMock()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._run_login_command",
        run_login,
    )

    result = await start_external_agent_login_async("claude_code", "fresh-claude-login")

    assert result == {"status": "ready"}
    run_login.assert_not_called()
    assert captured_manager_kwargs["environ"] == {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-from-vault"
    }
    provider.probe_auth.assert_called_once_with(
        runtime,
        environ={"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-from-vault"},
    )


@pytest.mark.asyncio
async def test_refresh_status_preserves_active_login_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refreshing status should not clobber an in-flight worker login session."""
    from orcheo_backend.worker.external_agents import (
        refresh_external_agent_status_async,
    )

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="session-claude",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude Code",
        state=ExternalAgentLoginSessionState.AWAITING_OAUTH,
        created_at=now,
        updated_at=now,
        auth_url="https://claude.com/cai/oauth/authorize?state=test-state",
        detail="Complete the browser sign-in.",
        recent_output="Opening browser to sign in...",
        resolved_version="2.1.89",
        executable_path="/root/.orcheo/agent-runtimes/claude",
    )
    store.save_login_session(session)
    store.save_provider_status(
        ExternalAgentProviderStatus(
            provider=ExternalAgentProviderName.CLAUDE_CODE,
            display_name="Claude Code",
            state=ExternalAgentProviderState.AUTHENTICATING,
            installed=True,
            authenticated=False,
            resolved_version="2.1.89",
            executable_path="/root/.orcheo/agent-runtimes/claude",
            detail="Waiting for browser-based sign-in.",
            active_session_id=session.session_id,
            checked_at=now,
        )
    )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    manager = MagicMock()
    manager.get_provider.return_value = MagicMock()
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await refresh_external_agent_status_async("claude_code")

    assert result == {"status": "authenticating"}
    manager.inspect_runtime.assert_not_called()
    status = store.get_provider_status(ExternalAgentProviderName.CLAUDE_CODE)
    assert status.active_session_id == session.session_id
    assert status.state == ExternalAgentProviderState.AUTHENTICATING


@pytest.mark.asyncio
async def test_refresh_status_drops_stale_login_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale session snapshots should not keep the provider spinning forever."""
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
    )
    from orcheo_backend.worker.external_agents import (
        refresh_external_agent_status_async,
    )

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    stale_time = now - timedelta(seconds=30)
    session = ExternalAgentLoginSession(
        session_id="stale-session",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude Code",
        state=ExternalAgentLoginSessionState.AWAITING_OAUTH,
        created_at=stale_time,
        updated_at=stale_time,
        auth_url="https://claude.com/cai/oauth/authorize?state=test-state",
        detail="Complete the browser sign-in.",
        resolved_version="2.1.89",
        executable_path="/root/.orcheo/agent-runtimes/claude",
    )
    store.save_login_session(session)
    store.save_provider_status(
        ExternalAgentProviderStatus(
            provider=ExternalAgentProviderName.CLAUDE_CODE,
            display_name="Claude Code",
            state=ExternalAgentProviderState.AUTHENTICATING,
            installed=True,
            authenticated=False,
            resolved_version="2.1.89",
            executable_path="/root/.orcheo/agent-runtimes/claude",
            detail="Waiting for browser-based sign-in.",
            active_session_id=session.session_id,
            checked_at=stale_time,
        )
    )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="claude_code",
        version="2.1.89",
        install_dir=Path("/root/.orcheo/agent-runtimes/claude_code"),
        executable_path=Path("/root/.orcheo/agent-runtimes/claude"),
        package_name="@anthropic-ai/claude-code",
    )
    provider = MagicMock()
    provider.probe_auth.return_value = AuthProbeResult(
        status=AuthStatus.SETUP_NEEDED,
        message="Claude Code is installed but not authenticated on this worker.",
    )
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, None)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await refresh_external_agent_status_async("claude_code")

    assert result == {"status": "needs_login"}
    manager.inspect_runtime.assert_called_once_with("claude_code")


@pytest.mark.asyncio
async def test_refresh_status_marks_provider_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.worker.external_agents import (
        refresh_external_agent_status_async,
    )

    store = ExternalAgentRuntimeStore()
    store._redis = None
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    provider = MagicMock()
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (None, None)
    manager.environment_for_provider.return_value = {}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await refresh_external_agent_status_async("claude_code")

    assert result == {"status": "not_installed"}
    status = store.get_provider_status(ExternalAgentProviderName.CLAUDE_CODE)
    assert status.state == ExternalAgentProviderState.NOT_INSTALLED


@pytest.mark.asyncio
async def test_refresh_status_marks_provider_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
    )
    from orcheo_backend.worker.external_agents import (
        refresh_external_agent_status_async,
    )

    store = ExternalAgentRuntimeStore()
    store._redis = None
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="codex",
        version="1.0.0",
        install_dir=Path("/tmp/codex"),
        executable_path=Path("/tmp/codex/bin/codex"),
        package_name="@openai/codex",
    )
    provider = MagicMock()
    provider.probe_auth.return_value = AuthProbeResult(status=AuthStatus.AUTHENTICATED)
    manifest = MagicMock(last_auth_ok_at=datetime.now(UTC))
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {}
    manager.mark_auth_success.return_value = manifest
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await refresh_external_agent_status_async("codex")

    assert result == {"status": "ready"}
    status = store.get_provider_status(ExternalAgentProviderName.CODEX)
    assert status.state == ExternalAgentProviderState.READY
    assert status.authenticated is True


@pytest.mark.asyncio
async def test_refresh_status_returns_error_payload_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.worker.external_agents import (
        refresh_external_agent_status_async,
    )

    store = ExternalAgentRuntimeStore()
    store._redis = None
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    manager = MagicMock()
    manager.get_provider.return_value = MagicMock()
    manager.inspect_runtime.side_effect = RuntimeError("boom")
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await refresh_external_agent_status_async("codex")

    assert result == {"status": "error", "detail": "boom"}
    status = store.get_provider_status(ExternalAgentProviderName.CODEX)
    assert status.state == ExternalAgentProviderState.ERROR


@pytest.mark.asyncio
async def test_start_login_returns_missing_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: MagicMock(get_provider=MagicMock(return_value=MagicMock())),
    )

    result = await start_external_agent_login_async("codex", "missing")

    assert result == {"status": "missing_session"}


@pytest.mark.asyncio
async def test_start_login_installs_runtime_before_running_oauth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
        RuntimeResolution,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="codex-install",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="codex",
        version="1.0.0",
        install_dir=Path("/tmp/codex"),
        executable_path=Path("/tmp/codex/bin/codex"),
        package_name="@openai/codex",
    )
    manifest = RuntimeManifest(provider="codex", provider_root=Path("/tmp/codex"))
    provider = MagicMock()
    provider.probe_auth.return_value = AuthProbeResult(
        status=AuthStatus.SETUP_NEEDED,
        message="login required",
    )
    provider.oauth_login_command.return_value = ["/tmp/codex/bin/codex", "login"]
    provider.build_environment.return_value = {}
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (None, None)
    manager.environment_for_provider.return_value = {}
    manager.resolve_runtime = AsyncMock(
        return_value=RuntimeResolution(
            runtime=runtime,
            manifest=manifest,
            maintenance_due=False,
        )
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._run_login_command",
        lambda *args, **kwargs: MagicMock(
            auth_token=None,
            auth_url=None,
            device_code=None,
            output="",
            timed_out=True,
        ),
    )

    result = await start_external_agent_login_async("codex", "codex-install")

    assert result == {"status": "timed_out"}
    manager.resolve_runtime.assert_called_once_with("codex")
    updated_session = store.get_login_session("codex-install")
    assert updated_session is not None
    assert updated_session.resolved_version == "1.0.0"


@pytest.mark.asyncio
async def test_start_login_returns_ready_when_provider_is_already_authenticated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="codex-ready",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="codex",
        version="1.0.0",
        install_dir=Path("/tmp/codex"),
        executable_path=Path("/tmp/codex/bin/codex"),
        package_name="@openai/codex",
    )
    manifest = MagicMock(last_auth_ok_at=datetime.now(UTC))
    provider = MagicMock()
    provider.probe_auth.return_value = AuthProbeResult(status=AuthStatus.AUTHENTICATED)
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {}
    manager.mark_auth_success.return_value = manifest
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await start_external_agent_login_async("codex", "codex-ready")

    assert result == {"status": "ready"}
    updated_session = store.get_login_session("codex-ready")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.AUTHENTICATED
    status = store.get_provider_status(ExternalAgentProviderName.CODEX)
    assert status.state == ExternalAgentProviderState.READY


@pytest.mark.asyncio
async def test_start_login_persists_worker_updates_and_auth_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    vault = InMemoryCredentialVault()
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="claude-auth",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude Code",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    store.save_login_input("claude-auth", "ABCD-1234")
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: vault,
    )

    runtime = ResolvedRuntime(
        provider="claude_code",
        version="2.0.0",
        install_dir=Path("/tmp/claude"),
        executable_path=Path("/tmp/claude/bin/claude"),
        package_name="@anthropic-ai/claude-code",
    )
    manifest = RuntimeManifest(
        provider="claude_code",
        provider_root=Path("/tmp/claude"),
    )
    provider = MagicMock()
    provider.probe_auth.side_effect = [
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="login required"),
        AuthProbeResult(status=AuthStatus.AUTHENTICATED),
    ]
    provider.oauth_login_command.return_value = [
        "/tmp/claude/bin/claude",
        "setup-token",
    ]
    provider.build_environment.return_value = {
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-worker"
    }
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {}
    manager.mark_auth_success.return_value = MagicMock(
        last_auth_ok_at=datetime.now(UTC)
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    def fake_run_login_command(*args, **kwargs):
        kwargs["on_output"]("waiting for worker auth", None, None)
        kwargs["on_output"](
            "Open https://example.com/auth",
            "https://example.com/auth",
            "CODE-123",
        )
        assert kwargs["consume_input"](False) == "ABCD-1234"
        kwargs["consume_input"](True)
        current = store.get_login_session("claude-auth")
        assert current is not None
        store.save_login_session(
            current.model_copy(
                update={"state": ExternalAgentLoginSessionState.AUTHENTICATED}
            )
        )
        kwargs["on_tick"]()
        return MagicMock(
            auth_token="sk-ant-worker",
            auth_url="https://example.com/auth",
            device_code="CODE-123",
            output="worker completed auth",
            timed_out=False,
        )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._run_login_command",
        fake_run_login_command,
    )

    result = await start_external_agent_login_async("claude_code", "claude-auth")

    assert result == {"status": "authenticated"}
    updated_session = store.get_login_session("claude-auth")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.AUTHENTICATED
    assert updated_session.auth_url == "https://example.com/auth"
    assert store.get_login_input("claude-auth") is None
    stored_token = next(
        item
        for item in vault.list_all_credentials()
        if item.name == CLAUDE_CODE_OAUTH_TOKEN_CREDENTIAL_NAME
    )
    assert vault.reveal_secret(credential_id=stored_token.id) == "sk-ant-worker"
    status = store.get_provider_status(ExternalAgentProviderName.CLAUDE_CODE)
    assert status.state == ExternalAgentProviderState.READY


@pytest.mark.asyncio
async def test_start_login_backfills_codex_auth_json_into_vault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    vault = InMemoryCredentialVault()
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="codex-ready",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_vault",
        lambda: vault,
    )

    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        '{"auth_mode":"chatgpt","OPENAI_API_KEY":null}',
        encoding="utf-8",
    )
    runtime = ResolvedRuntime(
        provider="codex",
        version="0.30.0",
        install_dir=tmp_path / "codex",
        executable_path=tmp_path / "codex" / "bin" / "codex",
        package_name="@openai/codex",
    )
    manifest = RuntimeManifest(provider="codex", provider_root=tmp_path / "codex")
    provider = MagicMock()
    provider.probe_auth.return_value = AuthProbeResult(status=AuthStatus.AUTHENTICATED)
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {"CODEX_HOME": str(codex_home)}
    manager.mark_auth_success.return_value = MagicMock(last_auth_ok_at=now)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await start_external_agent_login_async("codex", "codex-ready")

    assert result == {"status": "ready"}
    stored_auth = next(
        item
        for item in vault.list_all_credentials()
        if item.name == CODEX_AUTH_JSON_CREDENTIAL_NAME
    )
    assert vault.reveal_secret(credential_id=stored_auth.id) == (
        '{"auth_mode":"chatgpt","OPENAI_API_KEY":null}'
    )


@pytest.mark.asyncio
async def test_start_login_heartbeat_keeps_pending_session_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="claude-heartbeat",
        provider=ExternalAgentProviderName.CLAUDE_CODE,
        display_name="Claude Code",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="claude_code",
        version="2.0.0",
        install_dir=Path("/tmp/claude"),
        executable_path=Path("/tmp/claude/bin/claude"),
        package_name="@anthropic-ai/claude-code",
    )
    manifest = RuntimeManifest(
        provider="claude_code",
        provider_root=Path("/tmp/claude"),
    )
    provider = MagicMock()
    provider.probe_auth.side_effect = [
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="login required"),
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="still waiting"),
    ]
    provider.oauth_login_command.return_value = [
        "/tmp/claude/bin/claude",
        "setup-token",
    ]
    provider.build_environment.return_value = {}
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    def fake_run_login_command(*args, **kwargs):
        before = store.get_login_session("claude-heartbeat")
        kwargs["on_tick"]()
        after = store.get_login_session("claude-heartbeat")
        assert before is not None
        assert after is not None
        assert after.updated_at >= before.updated_at
        return MagicMock(
            auth_token=None,
            auth_url=None,
            device_code=None,
            output="",
            timed_out=True,
        )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._run_login_command",
        fake_run_login_command,
    )

    result = await start_external_agent_login_async("claude_code", "claude-heartbeat")

    assert result == {"status": "timed_out"}


@pytest.mark.asyncio
async def test_start_login_returns_install_failed_when_runtime_install_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import RuntimeInstallError
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="install-error",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    provider = MagicMock()
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (None, None)
    manager.resolve_runtime.side_effect = RuntimeInstallError(
        "codex",
        "install failed",
        command=["npm", "install"],
        stdout="",
        stderr="boom",
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await start_external_agent_login_async("codex", "install-error")

    assert result == {"status": "install_failed"}
    updated_session = store.get_login_session("install-error")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.FAILED
    status = store.get_provider_status(ExternalAgentProviderName.CODEX)
    assert status.state == ExternalAgentProviderState.ERROR


@pytest.mark.asyncio
async def test_start_login_returns_error_when_unexpected_exception_occurs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import ResolvedRuntime
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="unexpected-error",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="codex",
        version="1.0.0",
        install_dir=Path("/tmp/codex"),
        executable_path=Path("/tmp/codex/bin/codex"),
        package_name="@openai/codex",
    )
    provider = MagicMock()
    provider.probe_auth.side_effect = RuntimeError("boom")
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, None)
    manager.environment_for_provider.return_value = {}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    result = await start_external_agent_login_async("codex", "unexpected-error")

    assert result == {"status": "error", "detail": "boom"}
    updated_session = store.get_login_session("unexpected-error")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.FAILED


@pytest.mark.asyncio
async def test_start_login_gemini_auto_submits_safe_enter_prompts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="gemini-auto-enter",
        provider=ExternalAgentProviderName.GEMINI,
        display_name="Gemini CLI",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="gemini",
        version="0.36.0",
        install_dir=Path("/tmp/gemini"),
        executable_path=Path("/tmp/gemini/bin/gemini"),
        package_name="@google/gemini-cli",
    )
    manifest = RuntimeManifest(provider="gemini", provider_root=Path("/tmp/gemini"))
    provider = MagicMock()
    provider.probe_auth.side_effect = [
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="login required"),
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="still waiting"),
    ]
    provider.oauth_login_command.return_value = [
        "/tmp/gemini/bin/gemini",
        "/auth",
        "signin",
    ]
    provider.build_environment.return_value = {"HOME": "/tmp/gemini-home"}
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {"HOME": "/tmp/gemini-home"}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    def fake_run_login_command(*args, **kwargs):
        trust_prompt = """
        Do you trust the files in this folder?
        1. Trust folder (app)
        2. Trust parent folder ()
        3. Don't trust
        """
        auth_prompt = """
        How would you like to authenticate for this project?
        1. Sign in with Google
        2. Use Gemini API Key
        3. Vertex AI
        No authentication method selected.
        (Use Enter to select)
        """
        assert kwargs["cwd"] == Path("/workspace/agents")
        assert kwargs["auto_input"]("Continue in the browser.") is None
        assert kwargs["auto_input"](trust_prompt) == ""
        assert kwargs["auto_input"](trust_prompt) is None
        assert kwargs["auto_input"](auth_prompt) == ""
        assert kwargs["auto_input"](auth_prompt) is None
        return MagicMock(
            auth_token=None,
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            device_code="ABCD-1234",
            output="waiting for Gemini auth",
            timed_out=True,
        )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._run_login_command",
        fake_run_login_command,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._gemini_login_working_directory",
        lambda: Path("/workspace/agents"),
    )

    result = await start_external_agent_login_async("gemini", "gemini-auto-enter")

    assert result == {"status": "timed_out"}
    updated_session = store.get_login_session("gemini-auto-enter")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.TIMED_OUT
    assert updated_session.auth_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert updated_session.device_code == "ABCD-1234"


@pytest.mark.asyncio
async def test_start_login_gemini_retries_after_cli_restart_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="gemini-restart",
        provider=ExternalAgentProviderName.GEMINI,
        display_name="Gemini CLI",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="gemini",
        version="0.36.0",
        install_dir=Path("/tmp/gemini"),
        executable_path=Path("/tmp/gemini/bin/gemini"),
        package_name="@google/gemini-cli",
    )
    manifest = RuntimeManifest(provider="gemini", provider_root=Path("/tmp/gemini"))
    provider = MagicMock()
    provider.probe_auth.side_effect = [
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="login required"),
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="still waiting"),
    ]
    provider.oauth_login_command.return_value = [
        "/tmp/gemini/bin/gemini",
        "/auth",
        "signin",
    ]
    provider.build_environment.return_value = {"HOME": "/tmp/gemini-home"}
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {"HOME": "/tmp/gemini-home"}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    calls = {"count": 0}

    def fake_run_login_command(*args, **kwargs):
        calls["count"] += 1
        assert kwargs["cwd"] == Path("/workspace/agents")
        if calls["count"] == 1:
            return MagicMock(
                auth_token=None,
                auth_url=None,
                device_code=None,
                output="Logging in with Google... Restarting Gemini CLI to continue.",
                timed_out=False,
            )
        return MagicMock(
            auth_token=None,
            auth_url="https://accounts.google.com/o/oauth2/v2/auth",
            device_code="ABCD-1234",
            output="Please visit the following URL to authorize the application.",
            timed_out=True,
        )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._run_login_command",
        fake_run_login_command,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._gemini_login_working_directory",
        lambda: Path("/workspace/agents"),
    )

    result = await start_external_agent_login_async("gemini", "gemini-restart")

    assert result == {"status": "timed_out"}
    assert calls["count"] == 2
    updated_session = store.get_login_session("gemini-restart")
    assert updated_session is not None
    assert updated_session.state == ExternalAgentLoginSessionState.TIMED_OUT
    assert updated_session.auth_url == "https://accounts.google.com/o/oauth2/v2/auth"
    assert updated_session.device_code == "ABCD-1234"


@pytest.mark.asyncio
async def test_disconnect_external_agent_clears_worker_state_and_refreshes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import ResolvedRuntime
    from orcheo_backend.worker.external_agents import disconnect_external_agent_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="codex",
        version="1.0.0",
        install_dir=Path("/tmp/codex"),
        executable_path=Path("/tmp/codex/bin/codex"),
        package_name="@openai/codex",
    )
    manager = MagicMock()
    manager.inspect_runtime.return_value = (runtime, None)
    manager.environment_for_provider.return_value = {"CODEX_HOME": "/tmp/codex-home"}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    calls: list[tuple[str, object, object]] = []
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._logout_provider_cli",
        lambda provider_id, runtime_arg, provider_environ: calls.append(
            ("logout", provider_id, runtime_arg)
        ),
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._clear_provider_auth_state",
        lambda provider_id, provider_environ: calls.append(
            ("clear", provider_id, dict(provider_environ))
        ),
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._mark_provider_session_disconnected",
        lambda runtime_store, provider_id: calls.append(
            ("mark", provider_id, runtime_store)
        ),
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.refresh_external_agent_status_async",
        AsyncMock(return_value={"status": "needs_login"}),
    )

    result = await disconnect_external_agent_async("codex")

    assert result == {"status": "needs_login"}
    manager.save_provider_environment.assert_called_once_with(
        "codex",
        {"CODEX_AUTH_JSON": ""},
    )
    assert store.get_provider_environment(ExternalAgentProviderName.CODEX) == {}
    assert calls[0][0] == "logout"
    assert calls[1] == (
        "clear",
        ExternalAgentProviderName.CODEX,
        {"CODEX_HOME": "/tmp/codex-home"},
    )
    assert calls[2][0] == "mark"


@pytest.mark.asyncio
async def test_disconnect_external_agent_removes_gemini_runtime_installation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from orcheo.external_agents.models import ResolvedRuntime
    from orcheo_backend.worker.external_agents import disconnect_external_agent_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="gemini",
        version="0.36.0",
        install_dir=tmp_path / "gemini-runtime",
        executable_path=tmp_path / "gemini-runtime" / "bin" / "gemini",
        package_name="@google/gemini-cli",
    )
    provider_root = tmp_path / "agent-runtimes" / "gemini"
    provider_root.mkdir(parents=True)
    manager = MagicMock()
    manager.runtime_root = tmp_path / "agent-runtimes"
    manager.inspect_runtime.return_value = (runtime, None)
    manager.environment_for_provider.return_value = {"HOME": str(tmp_path / "home")}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._logout_provider_cli",
        lambda provider_id, runtime_arg, provider_environ: None,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._clear_provider_auth_state",
        lambda provider_id, provider_environ: None,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._mark_provider_session_disconnected",
        lambda runtime_store, provider_id: None,
    )
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.refresh_external_agent_status_async",
        AsyncMock(return_value={"status": "not_installed"}),
    )

    result = await disconnect_external_agent_async("gemini")

    assert result == {"status": "not_installed"}
    manager.save_provider_environment.assert_called_once_with(
        "gemini",
        {
            "GEMINI_AUTH_JSON": "",
            "GEMINI_GOOGLE_ACCOUNTS_JSON": "",
            "GEMINI_STATE_JSON": "",
            "GEMINI_OAUTH_CREDS_JSON": "",
        },
    )
    assert not provider_root.exists()


@pytest.mark.asyncio
async def test_start_login_non_gemini_auto_input_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from orcheo.external_agents.models import (
        AuthProbeResult,
        AuthStatus,
        ResolvedRuntime,
        RuntimeManifest,
    )
    from orcheo_backend.worker.external_agents import start_external_agent_login_async

    store = ExternalAgentRuntimeStore()
    store._redis = None
    now = datetime.now(UTC)
    session = ExternalAgentLoginSession(
        session_id="codex-no-auto-enter",
        provider=ExternalAgentProviderName.CODEX,
        display_name="Codex",
        state=ExternalAgentLoginSessionState.PENDING,
        created_at=now,
        updated_at=now,
    )
    store.save_login_session(session)
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.get_external_agent_runtime_store",
        lambda: store,
    )

    runtime = ResolvedRuntime(
        provider="codex",
        version="1.0.0",
        install_dir=Path("/tmp/codex"),
        executable_path=Path("/tmp/codex/bin/codex"),
        package_name="@openai/codex",
    )
    manifest = RuntimeManifest(provider="codex", provider_root=Path("/tmp/codex"))
    provider = MagicMock()
    provider.probe_auth.side_effect = [
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="login required"),
        AuthProbeResult(status=AuthStatus.SETUP_NEEDED, message="still waiting"),
    ]
    provider.oauth_login_command.return_value = ["/tmp/codex/bin/codex", "login"]
    provider.build_environment.return_value = {}
    manager = MagicMock()
    manager.get_provider.return_value = provider
    manager.inspect_runtime.return_value = (runtime, manifest)
    manager.environment_for_provider.return_value = {}
    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents.ExternalAgentRuntimeManager",
        lambda **kwargs: manager,
    )

    def fake_run_login_command(*args, **kwargs):
        assert kwargs["auto_input"]("Paste the device code") is None
        return MagicMock(
            auth_token=None,
            auth_url="https://auth.openai.com/oauth/authorize",
            device_code="CODE-123",
            output="waiting for device auth",
            timed_out=True,
        )

    monkeypatch.setattr(
        "orcheo_backend.worker.external_agents._run_login_command",
        fake_run_login_command,
    )

    result = await start_external_agent_login_async("codex", "codex-no-auto-enter")

    assert result == {"status": "timed_out"}
