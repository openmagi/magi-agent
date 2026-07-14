"""PR-S: sandboxed Bash for child runners (allowlist + tempdir + env-strip).

PR-N excluded ``Bash`` from the child readonly toolset citing security. PR-S
closes the resulting asymmetry (parent gate5b has Bash live, so models learn
to expect Bash; children get ``Tool 'Bash' not found``) by exposing a REAL
``Bash`` under a bounded sandbox: command allowlist, per-turn tempdir cwd,
env-key stripping, wall-clock timeout, process-group kill.

These tests pin the sandbox's security invariants end-to-end:

1. Happy-path whitelisted commands run and return their real stdout.
2. A non-whitelisted binary is rejected with a structured tool_result naming
   the allowlist (so the model can retry with a different approach).
3. Pipe stages are ALL validated (any stage argument that escapes the
   tempdir - absolute path - rejects the whole pipeline).
4. Wall-clock timeout kills the process group (so ``sleep 60`` cannot hold
   the child hostage).
5. The subprocess env is stripped: ``MAGI_*`` / ``OPENAI_*`` / etc. never
   reach a whitelisted ``env`` invocation.
6. The subprocess cwd is the per-child tempdir, not the operator's cwd.
7. Under the default-OFF flag the child toolset stays byte-identical to PR-N
   (``Bash`` absent from ``_resolve_turn_toolset``); under ON, both
   ``Bash`` + ``Calculation`` appear alongside the source-inspection set.
8. Write attempts to any absolute path (e.g. ``tee /tmp/foo``) are rejected
   before the subprocess spawns.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from magi_agent.runtime.child_bash import (
    CHILD_BASH_ALLOWLIST,
    CHILD_BASH_SANDBOX_ENV,
    ChildBashSandbox,
    build_sandbox_env,
    child_bash_sandbox_enabled,
    validate_pipeline,
)
from magi_agent.runtime.child_runner_live import RealLocalChildRunner
from magi_agent.runtime.child_toolset import (
    READONLY_TOOL_NAMES,
    toolset_allowlist,
)

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _has(binary: str) -> bool:
    """Return True iff ``binary`` is on the runner's PATH.

    Some CI images ship a stripped coreutils that lacks ``bc`` / ``node``;
    tests that depend on those binaries call this helper and skip cleanly so
    the security invariant tests still run.
    """
    return shutil.which(binary) is not None


def _tool_name(tool: object) -> str:
    name = getattr(tool, "name", None)
    return str(name) if name is not None else ""


# --------------------------------------------------------------------------- #
# 1. Happy-path whitelisted commands                                           #
# --------------------------------------------------------------------------- #


def test_whitelisted_echo_runs() -> None:
    """``echo hello`` returns ``hello\\n`` from the sandboxed subprocess."""
    sandbox = ChildBashSandbox()
    result = sandbox.run("echo hello")

    assert result.status == "ok", f"echo should succeed; got {result!r}"
    output = result.output
    assert isinstance(output, dict)
    assert output["stdout"] == "hello\n"
    # The metadata carries the sandbox marker so downstream collectors can
    # distinguish sandbox Bash from parent gate5b Bash.
    assert result.metadata["sandbox"] == "child_bash"
    assert result.metadata["exitCode"] == 0
    assert result.metadata["timedOut"] is False


def test_whitelisted_bc_arithmetic() -> None:
    """``echo 1+1 | bc`` returns ``2``. pipe validation lets safe stages through."""
    if not _has("bc"):
        pytest.skip("bc not installed on this runner")
    sandbox = ChildBashSandbox()
    result = sandbox.run("echo 1+1 | bc")

    assert result.status == "ok", f"bc pipeline should succeed; got {result!r}"
    output = result.output
    assert isinstance(output, dict)
    # bc emits "2\n" for a single-line expression.
    assert output["stdout"].strip() == "2"


# --------------------------------------------------------------------------- #
# 2. Refusal path. allowlist rejection                                        #
# --------------------------------------------------------------------------- #


def test_non_whitelisted_command_returns_structured_error() -> None:
    """``curl example.com`` is rejected with a tool_result naming the allowlist."""
    sandbox = ChildBashSandbox()
    result = sandbox.run("curl example.com")

    assert result.status == "blocked"
    assert result.error_code == "child_bash_sandbox_denied"
    assert "curl" in (result.error_message or "")
    assert "allowlist" in (result.error_message or "").lower()
    # metadata surfaces the allowlist so the model can react programmatically.
    assert result.metadata["sandbox"] == "child_bash"
    assert result.metadata["offending"] == "curl"
    listed = result.metadata["allowlist"]
    assert isinstance(listed, list)
    assert "echo" in listed and "cat" in listed and "curl" not in listed


def test_non_whitelisted_command_never_spawns_subprocess(monkeypatch) -> None:
    """Refusal happens BEFORE subprocess.Popen. no process is ever created."""
    import magi_agent.runtime.child_bash as cb

    call_count = {"n": 0}

    def _boom(*args, **kwargs):
        call_count["n"] += 1
        raise AssertionError("subprocess must not spawn for refused command")

    monkeypatch.setattr(cb.subprocess, "Popen", _boom)
    sandbox = ChildBashSandbox()
    result = sandbox.run("curl https://example.com")

    assert result.status == "blocked"
    assert call_count["n"] == 0


# --------------------------------------------------------------------------- #
# 3. Pipe stages are ALL validated                                             #
# --------------------------------------------------------------------------- #


def test_pipe_all_stages_must_be_whitelisted() -> None:
    """``cat /etc/passwd | grep root`` rejected: absolute path escapes the sandbox.

    ``cat`` IS in the allowlist but ``/etc/passwd`` is an absolute path outside
    the per-child tempdir, so the pipeline is rejected pre-launch. The failure
    surface names the offending argument so the model can rewrite the request
    against a cwd-relative file.
    """
    sandbox = ChildBashSandbox()
    result = sandbox.run("cat /etc/passwd | grep root")

    assert result.status == "blocked"
    assert result.error_code == "child_bash_sandbox_denied"
    # The rejection reason names the escaping arg.
    assert "/etc/passwd" in (result.error_message or "")


def test_pipe_rejects_non_whitelisted_second_stage() -> None:
    """A whitelisted first stage cannot smuggle a non-whitelisted second stage."""
    sandbox = ChildBashSandbox()
    result = sandbox.run("echo foo | curl https://example.com")

    assert result.status == "blocked"
    assert result.metadata["offending"] == "curl"


# --------------------------------------------------------------------------- #
# 4. Wall-clock timeout                                                        #
# --------------------------------------------------------------------------- #


def test_timeout_kills_process_group() -> None:
    """``sleep 60`` at timeout=1s is killed, returns error+timedOut=True.

    The design pins the default at 30s; the test uses a tight override to keep
    CI fast. What matters is the invariant: any command that outruns
    ``timeout_s`` is killed and the ToolResult carries ``timedOut=True``.
    """
    sandbox = ChildBashSandbox(timeout_s=1.0)
    result = sandbox.run("sleep 60")

    assert result.status == "error"
    assert result.error_code == "child_bash_sandbox_timeout"
    assert result.metadata["timedOut"] is True
    # exit code is None on timeout (process was killed before it could exit).
    assert result.metadata["exitCode"] is None


# --------------------------------------------------------------------------- #
# 5. Env stripping                                                             #
# --------------------------------------------------------------------------- #


def test_env_stripped_at_source() -> None:
    """``build_sandbox_env`` strips MAGI_/OPENAI_/ANTHROPIC_/GEMINI_/API keys.

    The unit test pins the input-output invariant directly so we do not depend
    on the subprocess ``env`` binary being installed on the CI runner.
    """
    src = {
        "PATH": "/usr/bin:/bin",
        "LANG": "en_US.UTF-8",
        "HOME": "/root",
        "MAGI_MEMORY_ENABLED": "1",
        "OPENAI_API_KEY": "sk-should-never-leak",
        "ANTHROPIC_API_KEY": "sk-should-never-leak",
        "GEMINI_API_KEY": "should-never-leak",
        "SOMETHING_TOKEN": "leaky",
    }
    env = build_sandbox_env(src)

    assert env.get("PATH") == "/usr/bin:/bin"
    assert env.get("LANG") == "en_US.UTF-8"
    # None of the disallowed keys survive.
    for key in (
        "HOME",
        "MAGI_MEMORY_ENABLED",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "SOMETHING_TOKEN",
    ):
        assert key not in env, f"{key!r} must not survive env stripping"


def test_env_stripped_via_subprocess() -> None:
    """End-to-end: ``env`` in the sandbox emits no MAGI_/OPENAI_/ANTHROPIC_/GEMINI_ lines.

    Uses the whitelisted ``env`` binary as the observation point. Even with
    provider keys set on the outer process, the subprocess sees an empty
    intersection with the leaky namespaces.
    """
    if not _has("env"):
        pytest.skip("env binary not installed on this runner")
    # Layer a fake env source that DOES contain MAGI_ / OPENAI_ / ANTHROPIC_ /
    # GEMINI_ to guarantee the deny path is exercised even on a CI image with
    # a clean host env.
    source = dict(os.environ)
    source.update(
        {
            "MAGI_MEMORY_ENABLED": "1",
            "OPENAI_API_KEY": "sk-should-never-leak",
            "ANTHROPIC_API_KEY": "sk-should-never-leak",
            "GEMINI_API_KEY": "should-never-leak",
        }
    )
    sandbox = ChildBashSandbox(env_source=source)
    result = sandbox.run("env")

    assert result.status == "ok", f"env should succeed; got {result!r}"
    output = result.output
    assert isinstance(output, dict)
    stdout = output["stdout"]
    for prefix in ("MAGI_", "OPENAI_", "ANTHROPIC_", "GEMINI_"):
        assert prefix not in stdout, f"env output leaked {prefix!r}; sandbox env-strip broken"


# --------------------------------------------------------------------------- #
# 6. Cwd isolation                                                             #
# --------------------------------------------------------------------------- #


def test_cwd_isolated_from_operator() -> None:
    """``pwd`` returns the per-child tempdir, not the process's cwd."""
    if not _has("pwd"):
        pytest.skip("pwd binary not installed on this runner")
    sandbox = ChildBashSandbox()
    result = sandbox.run("pwd")

    assert result.status == "ok"
    output = result.output
    assert isinstance(output, dict)
    reported_cwd = output["stdout"].strip()
    # macOS resolves /tmp to /private/tmp; tolerate that by resolving both.
    assert Path(reported_cwd).resolve() == Path(sandbox.cwd()).resolve()
    # And the tempdir must not be the operator's cwd (fail-close test).
    assert Path(reported_cwd).resolve() != Path(os.getcwd()).resolve()


# --------------------------------------------------------------------------- #
# 7. Toolset wiring. flag OFF (default) vs ON                                 #
# --------------------------------------------------------------------------- #


def test_flag_off_bash_not_in_toolset(monkeypatch, tmp_path: Path) -> None:
    """Default-OFF: ``_resolve_turn_toolset`` never forwards ``Bash`` to the child.

    Pinned end-to-end: no matter which flag might be set upstream, when the
    child-bash sandbox flag is off, ``Bash`` is absent from the readonly
    child's ADK tool list. Byte-identical to PR-N.
    """
    monkeypatch.delenv(CHILD_BASH_SANDBOX_ENV, raising=False)

    runner = RealLocalChildRunner(
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
    )
    tools, _collector = runner._resolve_turn_toolset("child-session-off")
    forwarded = {_tool_name(t) for t in tools}

    assert "Bash" not in forwarded, (
        "OFF path must be byte-identical to PR-N; Bash leaked into readonly."
    )
    # The pre-existing PR-N contract (Calculation is present) is preserved.
    assert "Calculation" in forwarded


def test_flag_on_bash_appears_alongside_calculation(monkeypatch, tmp_path: Path) -> None:
    """Flag ON: ``Bash`` joins the readonly toolset alongside ``Calculation``.

    Both should be forwarded to the ADK runtime under their canonical names
    so a model that calls either does not hit ``Tool not found``.
    """
    monkeypatch.setenv(CHILD_BASH_SANDBOX_ENV, "1")

    runner = RealLocalChildRunner(
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
        env=dict(os.environ),
    )
    tools, _collector = runner._resolve_turn_toolset("child-session-on")
    forwarded = {_tool_name(t) for t in tools}

    assert "Bash" in forwarded, "flag ON but Bash missing from readonly toolset; wiring broken."
    assert "Calculation" in forwarded, (
        "PR-N Calculation invariant regressed under the PR-S ON path."
    )
    # Pre-existing inspection tools survive.
    assert {"FileRead", "Glob", "Grep", "GitDiff"} <= forwarded


def test_flag_on_bash_func_is_sandbox(monkeypatch, tmp_path: Path) -> None:
    """Flag ON: the Bash ADK tool's ``func`` is the sandbox wrapper.

    Guards against a wiring regression where the allowlist adds ``Bash`` but
    the ADK tool is still the parent gate5b handler. The sandbox rewrite
    stamps ``_magi_child_bash_sandbox=True`` on the tool object.
    """
    monkeypatch.setenv(CHILD_BASH_SANDBOX_ENV, "1")

    runner = RealLocalChildRunner(
        toolset_profile="readonly",
        workspace_root=str(tmp_path),
        env=dict(os.environ),
    )
    tools, _collector = runner._resolve_turn_toolset("child-session-func")
    bash_tools = [t for t in tools if _tool_name(t) == "Bash"]

    assert len(bash_tools) == 1
    assert getattr(bash_tools[0], "_magi_child_bash_sandbox", False) is True, (
        "Bash tool must be sandbox-wrapped when the child-bash flag is ON"
    )


def test_wrapped_bash_func_passes_adk_declaration_build() -> None:
    """After wrapping, the ADK declaration builder must not raise.

    ``wrap_child_bash_tool`` previously kept ``_sandbox: ChildBashSandbox``
    as a keyword-only parameter on ``_sandboxed_func``. The ADK
    ``FunctionTool._get_declaration()`` path calls
    ``build_function_declaration`` which iterates every parameter except
    ``tool_context`` / ``input_stream`` -- it does NOT skip underscore-prefixed
    names -- and cannot serialize ``ChildBashSandbox`` to a JSON schema, so it
    raised before any LLM call.  The fix captures ``sandbox`` via closure and
    removes the parameter entirely, making the signature
    ``(arguments, tool_context)`` to match the clean first-party callable.
    """
    from google.adk.tools.function_tool import FunctionTool

    from magi_agent.runtime.child_bash import wrap_child_bash_tool

    async def _stub_bash(arguments: dict[str, object], tool_context: object) -> dict[str, object]:
        return {}

    real_tool = FunctionTool(_stub_bash)
    real_tool.name = "Bash"  # type: ignore[assignment]

    sandbox = ChildBashSandbox()
    [wrapped] = wrap_child_bash_tool([real_tool], sandbox=sandbox)

    # The production path: must not raise.
    decl = wrapped._get_declaration()

    assert decl is not None, "_get_declaration() returned None for the wrapped Bash tool"
    param_names = list(decl.parameters.properties.keys()) if decl.parameters else []
    assert "_sandbox" not in param_names, (
        "_sandbox leaked into the ADK declaration; closure capture regression"
    )


def test_readonly_constant_unchanged_by_pr_s() -> None:
    """The static ``READONLY_TOOL_NAMES`` constant does NOT list Bash.

    Preserves PR-N's `test_readonly_allowlist_excludes_mutating_and_exec_tools`
    contract: the CONSTANT is Bash-free; expansion happens at read-time inside
    :func:`toolset_allowlist`.
    """
    assert "Bash" not in READONLY_TOOL_NAMES


def test_toolset_allowlist_reads_flag(monkeypatch) -> None:
    """``toolset_allowlist`` includes Bash iff the flag is truthy at call time."""
    monkeypatch.delenv(CHILD_BASH_SANDBOX_ENV, raising=False)
    allow_off = toolset_allowlist("readonly")
    assert allow_off is not None and "Bash" not in allow_off

    monkeypatch.setenv(CHILD_BASH_SANDBOX_ENV, "1")
    allow_on = toolset_allowlist("readonly", env=dict(os.environ))
    assert allow_on is not None and "Bash" in allow_on


# --------------------------------------------------------------------------- #
# 8. Read-before-mutation: no fs writes outside tempdir                        #
# --------------------------------------------------------------------------- #


def test_write_attempt_outside_tempdir_is_rejected() -> None:
    """``tee /tmp/foo`` is rejected: absolute path escapes the sandbox tempdir.

    Even though ``tee`` is in the allowlist for cwd-scoped output, an absolute
    path arg triggers the pre-launch escape check.
    """
    sandbox = ChildBashSandbox()
    result = sandbox.run("echo x | tee /tmp/should-never-be-written")

    assert result.status == "blocked"
    assert result.error_code == "child_bash_sandbox_denied"
    assert "/tmp/should-never-be-written" in (result.error_message or "")
    # And the target file must not exist (no side effect leaked).
    assert not Path("/tmp/should-never-be-written").exists()


def test_home_relative_path_also_rejected() -> None:
    """``ls ~/`` rejected: home-relative paths also escape the sandbox tempdir."""
    sandbox = ChildBashSandbox()
    result = sandbox.run("ls ~/")

    assert result.status == "blocked"
    assert result.error_code == "child_bash_sandbox_denied"


def test_find_delete_operator_is_rejected() -> None:
    """``find . -delete`` is rejected: destructive ``find`` operators are banned."""
    sandbox = ChildBashSandbox()
    result = sandbox.run("find . -delete")

    assert result.status == "blocked"
    assert result.error_code == "child_bash_sandbox_denied"
    assert "-delete" in (result.error_message or "")


def test_python3_denies_import_os() -> None:
    """``python3 -c 'import os; ...'`` rejected: interpreter denylist scan."""
    sandbox = ChildBashSandbox()
    result = sandbox.run("python3 -c 'import os; print(1)'")

    assert result.status == "blocked"
    assert result.error_code == "child_bash_sandbox_denied"
    assert "import os" in (result.error_message or "")


# --------------------------------------------------------------------------- #
# Direct helpers                                                               #
# --------------------------------------------------------------------------- #


def test_child_bash_sandbox_enabled_default_off(monkeypatch) -> None:
    """Strict default-OFF: unset / non-truthy env yields ``False``."""
    monkeypatch.delenv(CHILD_BASH_SANDBOX_ENV, raising=False)
    assert child_bash_sandbox_enabled() is False
    monkeypatch.setenv(CHILD_BASH_SANDBOX_ENV, "0")
    assert child_bash_sandbox_enabled(dict(os.environ)) is False
    monkeypatch.setenv(CHILD_BASH_SANDBOX_ENV, "yes")
    assert child_bash_sandbox_enabled(dict(os.environ)) is True


def test_allowlist_snapshot() -> None:
    """Pin the shipping allowlist so a silent expansion cannot slip through review.

    Every entry MUST be a pure computation / text-munging / cwd-only metadata
    binary. Adding a new entry is a security decision requiring a bar identical
    to :data:`_PURE_NON_INSPECTION_TOOL_NAMES`: no fs mutation outside explicit
    path args, no network, no privilege escalation. This test breaks if any
    binary is added or removed so review sees the change.
    """
    expected = {
        "echo",
        "printf",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "awk",
        "sed",
        "sort",
        "uniq",
        "tr",
        "cut",
        "tee",
        "test",
        "[",
        "true",
        "false",
        "expr",
        "seq",
        "bc",
        "ls",
        "find",
        "pwd",
        "date",
        "basename",
        "dirname",
        "python3",
        "node",
        "sleep",
        "env",
        "printenv",
    }
    assert set(CHILD_BASH_ALLOWLIST) == expected


def test_validate_pipeline_reports_empty() -> None:
    """An empty command line is rejected with a descriptive reason."""
    v = validate_pipeline("")
    assert v.ok is False
    assert "empty" in v.reason
