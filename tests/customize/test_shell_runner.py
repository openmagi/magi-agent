"""F-EXEC-AUDIT — tests for shell_runner.py foundation.

Covers:
* :func:`validate_shell_payload` — accept/reject matrix.
* :func:`build_scoped_env` — whitelist + operator-declared + secret exclusion.
* :func:`truncate_output` — short pass-through + long marker.
* :func:`run_shell_payload` — inline echo, non-zero exit, timeout kill,
  env scoping in a real subprocess, stdin_json piping, Windows
  honest-degrade.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from magi_agent.customize.shell_runner import (
    ShellPayload,
    ShellRunResult,
    build_scoped_env,
    run_shell_payload,
    truncate_output,
    validate_shell_payload,
)


# ---------------------------------------------------------------------------
# validate_shell_payload
# ---------------------------------------------------------------------------


def test_validate_payload_accepts_inline_minimal() -> None:
    errors = validate_shell_payload({"source": "inline", "inline": "echo hi"})
    assert errors == []


def test_validate_payload_accepts_file_path() -> None:
    errors = validate_shell_payload({"source": "file", "path": "/tmp/check.sh"})
    assert errors == []


def test_validate_payload_rejects_missing_inline_when_source_is_inline() -> None:
    errors = validate_shell_payload({"source": "inline"})
    assert any("inline" in e for e in errors)


def test_validate_payload_rejects_missing_path_when_source_is_file() -> None:
    errors = validate_shell_payload({"source": "file"})
    assert any("path" in e for e in errors)


@pytest.mark.parametrize("bad_timeout", [0, 601])
def test_validate_payload_rejects_timeout_out_of_range(bad_timeout: int) -> None:
    errors = validate_shell_payload(
        {"source": "inline", "inline": "echo hi", "timeout_seconds": bad_timeout}
    )
    assert any("timeout_seconds" in e for e in errors)


def test_validate_payload_rejects_unknown_shell() -> None:
    errors = validate_shell_payload(
        {"source": "inline", "inline": "echo hi", "shell": "zsh"}
    )
    assert any("shell" in e for e in errors)


def test_validate_payload_rejects_inline_too_long() -> None:
    errors = validate_shell_payload(
        {"source": "inline", "inline": "x" * 4001}
    )
    assert any("inline" in e for e in errors)


# ---------------------------------------------------------------------------
# build_scoped_env
# ---------------------------------------------------------------------------


def test_build_scoped_env_includes_whitelist() -> None:
    source = {"PATH": "/usr/bin", "HOME": "/home/me", "USER": "me", "OTHER": "x"}
    env = build_scoped_env([], source_env=source)
    assert env["PATH"] == "/usr/bin"
    assert env["HOME"] == "/home/me"
    assert env["USER"] == "me"


def test_build_scoped_env_excludes_secrets() -> None:
    source = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "sk-secret-do-not-leak",
        "ANTHROPIC_API_KEY": "ant-secret",
    }
    env = build_scoped_env([], source_env=source)
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert env["PATH"] == "/usr/bin"


def test_build_scoped_env_includes_operator_declared() -> None:
    source = {"PATH": "/usr/bin", "MY_VAR": "hello", "OTHER": "ignored"}
    env = build_scoped_env(["MY_VAR"], source_env=source)
    assert env["MY_VAR"] == "hello"
    assert "OTHER" not in env


# ---------------------------------------------------------------------------
# truncate_output
# ---------------------------------------------------------------------------


def test_truncate_output_preserves_short() -> None:
    s = "short text"
    assert truncate_output(s) == s


def test_truncate_output_appends_marker_on_long() -> None:
    s = "A" * 5000
    out = truncate_output(s, max_bytes=4096)
    assert out.endswith("... [truncated]")
    # Body before the marker is exactly the byte-truncated prefix.
    assert out[:-len("... [truncated]")] == "A" * 4096


# ---------------------------------------------------------------------------
# run_shell_payload — real subprocess
# ---------------------------------------------------------------------------


def _run(coro):
    """Helper: run an awaitable in a fresh event loop (test isolation)."""
    return asyncio.run(coro)


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only runner")
def test_run_shell_payload_inline_echo() -> None:
    payload = ShellPayload(source="inline", inline="echo hi")
    result = _run(run_shell_payload(payload))
    assert isinstance(result, ShellRunResult)
    assert result.exit_code == 0
    assert result.stdout == "hi\n"
    assert result.timed_out is False
    assert result.reason is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only runner")
def test_run_shell_payload_exit_code_nonzero() -> None:
    payload = ShellPayload(source="inline", inline="exit 7")
    result = _run(run_shell_payload(payload))
    assert result.exit_code == 7
    assert result.timed_out is False


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only runner")
def test_run_shell_payload_timeout_kills_long_sleep() -> None:
    payload = ShellPayload(source="inline", inline="sleep 5", timeout_seconds=1)
    result = _run(run_shell_payload(payload))
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.reason == "timed_out"
    # PR-F-EXEC-AUDIT review pass (WARN 1): bound wall-clock duration so
    # a regression that removed the SIGKILL call would surface as a
    # hung sleep rather than a silent orphaned process. timeout_seconds=1
    # + sleep 5 → must finish well under 3000ms if the kill actually
    # fires; sleep 5's natural completion would take ~5000ms.
    assert result.duration_ms < 3000, (
        f"timeout path took {result.duration_ms}ms — SIGKILL likely missed"
    )


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only runner")
def test_run_shell_payload_fail_open_on_missing_file() -> None:
    """PR-F-EXEC-AUDIT review pass (WARN 2): the internal_error path
    is the safety-critical fail-open branch — any uncaught exception in
    F-EXEC1 wiring must surface as ``exit_code=-1, reason='internal_error'``
    rather than crash the hook turn."""
    payload = ShellPayload(
        source="file", path="/nonexistent/definitely-not-a-script-xyz.sh"
    )
    result = _run(run_shell_payload(payload))
    assert result.exit_code == -1
    assert result.reason == "internal_error"
    assert result.timed_out is False
    # stderr carries the underlying exception text so operators get a
    # diagnostic clue without a Python traceback.
    assert "not readable" in result.stderr or "not found" in result.stderr.lower()


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only runner")
def test_run_shell_payload_env_scoping_real_subprocess() -> None:
    # Build source env containing a secret + PATH; declare no extra env vars.
    source = dict(os.environ)
    source["OPENAI_API_KEY"] = "sk-leak-canary-123"
    payload = ShellPayload(
        source="inline",
        inline='echo "PATH=$PATH"; echo "SECRET=${OPENAI_API_KEY:-MISSING}"',
    )
    # Inject source via build_scoped_env indirectly: run_shell_payload reads
    # os.environ by default, so use monkeypatched env via source_env.
    result = _run(run_shell_payload(payload, source_env=source))
    assert result.exit_code == 0
    assert "PATH=" in result.stdout
    # Secret must not be in subprocess env, so the shell sees MISSING.
    assert "SECRET=MISSING" in result.stdout
    assert "sk-leak-canary-123" not in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX-only runner")
def test_run_shell_payload_stdin_json() -> None:
    payload = ShellPayload(
        source="inline",
        inline='read line; echo "got=$line"',
    )
    result = _run(run_shell_payload(payload, stdin_json={"x": 1}))
    assert result.exit_code == 0
    assert '"x"' in result.stdout
    assert "got=" in result.stdout


def test_run_shell_payload_windows_honest_degrade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("magi_agent.customize.shell_runner.sys.platform", "win32")
    payload = ShellPayload(source="inline", inline="echo hi")
    result = _run(run_shell_payload(payload))
    assert result.exit_code == -2
    assert result.reason == "unsupported_platform"
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.timed_out is False
