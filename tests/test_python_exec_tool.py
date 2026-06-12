"""Hermetic tests for the persistent ``PythonExec`` code-action seam.

The tool is strict opt-in behind ``MAGI_CODE_ACTION_ENABLED`` (default OFF):
with the flag unset the module is never imported, ``PythonExec`` is absent from
the registry and the advertised instruction, and the runtime is byte-identical
to before. With the flag on, a per-session persistent Python interpreter
(namespace survives across calls) is registered through the same gated
registration seam as ``BrowserTask``.

No network. The real ``python3`` worker subprocess is in the same hermetic
class as the existing shell-tool tests; the pool/config are injectable for
everything else.
"""

from __future__ import annotations

import asyncio

from pathlib import Path

import pytest

from magi_agent.config.env import code_action_enabled
from magi_agent.tools.context import ToolContext
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _context(
    session_key: str = "session-a",
    *,
    workspace_root: str | None = None,
) -> ToolContext:
    return ToolContext(
        bot_id="test-bot",
        session_id=session_key,
        session_key=session_key,
        workspace_root=workspace_root,
    )


def _bound_registry(config: object | None = None) -> ToolRegistry:
    from magi_agent.tools.python_exec import (
        bind_python_exec_handler,
        register_python_exec_manifest,
    )

    registry = ToolRegistry()
    register_python_exec_manifest(registry)
    bind_python_exec_handler(registry, config=config)
    return registry


def _handler(registry: ToolRegistry):
    registration = registry.resolve_registration("PythonExec")
    assert registration is not None and registration.handler is not None
    return registration.handler


def _run(
    handler,
    code: str,
    *,
    session_key: str = "session-a",
    reset: bool = False,
    workspace_root: str | None = None,
) -> ToolResult:
    arguments: dict[str, object] = {"code": code}
    if reset:
        arguments["reset"] = True
    return handler(arguments, _context(session_key, workspace_root=workspace_root))


# ---------------------------------------------------------------------------
# 1. Default-OFF proof — flag unset/off keeps the runtime byte-identical
# ---------------------------------------------------------------------------


def test_flag_helper_default_off() -> None:
    assert code_action_enabled(env={}) is False


def test_flag_helper_off_even_in_full_profile() -> None:
    # Strict opt-in: unlike profile flags, the full runtime profile must NOT
    # turn the seam on.
    assert code_action_enabled(env={"MAGI_RUNTIME_PROFILE": "full"}) is False


def test_flag_helper_on_when_set() -> None:
    assert code_action_enabled(env={"MAGI_CODE_ACTION_ENABLED": "true"}) is True


def test_flag_helper_off_when_set_false() -> None:
    assert code_action_enabled(env={"MAGI_CODE_ACTION_ENABLED": "false"}) is False


def test_python_exec_absent_from_runtime_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MAGI_CODE_ACTION_ENABLED", raising=False)
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    names = {manifest.name for manifest in runtime.registry.list_all()}
    assert "PythonExec" not in names


def test_python_exec_absent_from_instruction_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("MAGI_CODE_ACTION_ENABLED", raising=False)
    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="test-session",
        workspace_root=str(tmp_path),
    )
    assert "PythonExec" not in instruction


# ---------------------------------------------------------------------------
# 2. Flag-ON registration
# ---------------------------------------------------------------------------


def test_python_exec_registered_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_CODE_ACTION_ENABLED", "true")
    from magi_agent.cli.tool_runtime import build_cli_tool_runtime

    runtime = build_cli_tool_runtime(workspace_root=str(tmp_path))
    registration = runtime.registry.resolve_registration("PythonExec")
    assert registration is not None
    assert registration.enabled is True
    assert registration.handler is not None
    manifest = registration.manifest
    assert manifest.dangerous is True
    assert manifest.permission == "execute"
    assert manifest.available_in_modes == ("act",)
    assert manifest.parallel_safety == "unsafe"
    assert "requires-approval" in manifest.tags


def test_python_exec_advertised_when_flag_on(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("MAGI_CODE_ACTION_ENABLED", "true")
    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="test-session",
        workspace_root=str(tmp_path),
    )
    assert "PythonExec" in instruction


# ---------------------------------------------------------------------------
# 3/4. Persistence and session isolation
# ---------------------------------------------------------------------------


def test_namespace_persists_across_calls() -> None:
    handler = _handler(_bound_registry())
    first = _run(handler, "x = 41", session_key="persist")
    assert first.status == "ok"
    second = _run(handler, "x + 1", session_key="persist")
    assert second.status == "ok"
    assert isinstance(second.output, dict)
    assert second.output["value"] == "42"


def test_sessions_are_isolated() -> None:
    handler = _handler(_bound_registry())
    assert _run(handler, "x = 41", session_key="iso-a").status == "ok"
    other = _run(handler, "x + 1", session_key="iso-b")
    assert other.status == "error"
    assert other.error_code == "python_runtime_error"
    assert "NameError" in (other.error_message or "")


# ---------------------------------------------------------------------------
# 5. Stdout capture + head/tail cap
# ---------------------------------------------------------------------------


def test_stdout_head_tail_cap() -> None:
    from magi_agent.tools.python_exec import PythonExecConfig

    handler = _handler(_bound_registry(PythonExecConfig(max_output_bytes=2048)))
    result = _run(handler, 'print("A" * 50_000)', session_key="cap")
    assert result.status == "ok"
    assert isinstance(result.output, dict)
    stdout = result.output["stdout"]
    assert isinstance(stdout, str)
    assert "elided" in stdout
    assert stdout.startswith("A")
    assert stdout.rstrip().endswith("A")


# ---------------------------------------------------------------------------
# 6. Timeout kills the worker and resets the namespace
# ---------------------------------------------------------------------------


def test_timeout_then_fresh_namespace() -> None:
    from magi_agent.tools.python_exec import PythonExecConfig

    handler = _handler(_bound_registry(PythonExecConfig(timeout_s=0.5)))
    assert _run(handler, "marker = 7", session_key="timeout").status == "ok"

    timed_out = _run(handler, "while True: pass", session_key="timeout")
    assert timed_out.status == "error"
    assert timed_out.error_code == "python_exec_timeout"
    assert timed_out.metadata.get("namespaceReset") is True

    after = _run(handler, "marker", session_key="timeout")
    assert after.status == "error"
    assert after.error_code == "python_runtime_error"

    fresh = _run(handler, "1 + 1", session_key="timeout")
    assert fresh.status == "ok"


# ---------------------------------------------------------------------------
# 7. Import allowlist
# ---------------------------------------------------------------------------


def test_disallowed_import_blocked_without_side_effects() -> None:
    handler = _handler(_bound_registry())
    blocked = _run(handler, "sentinel = 7\nimport os", session_key="imports")
    assert blocked.status == "error"
    assert blocked.error_code == "import_not_allowed"

    # The whole block must not have executed: the sentinel assignment in the
    # same code block must NOT be visible to a later call.
    later = _run(handler, "sentinel", session_key="imports")
    assert later.status == "error"
    assert later.error_code == "python_runtime_error"


def test_allowlisted_import_persists() -> None:
    handler = _handler(_bound_registry())
    assert _run(handler, "import math", session_key="imports-ok").status == "ok"
    result = _run(handler, "math.sqrt(4)", session_key="imports-ok")
    assert result.status == "ok"
    assert isinstance(result.output, dict)
    assert result.output["value"] == "2.0"


def test_dynamic_import_evasion_blocked() -> None:
    handler = _handler(_bound_registry())
    result = _run(handler, '__import__("os").getcwd()', session_key="imports-dyn")
    assert result.status == "error"
    assert result.error_code == "python_runtime_error"
    assert "import_not_allowed" in (result.error_message or "")


# ---------------------------------------------------------------------------
# 8. Final-expression value; runtime error keeps the namespace
# ---------------------------------------------------------------------------


def test_final_expression_value_returned() -> None:
    handler = _handler(_bound_registry())
    result = _run(handler, "z = 41\nz + 1", session_key="final-expr")
    assert result.status == "ok"
    assert isinstance(result.output, dict)
    assert result.output["value"] == "42"


def test_runtime_error_keeps_namespace() -> None:
    handler = _handler(_bound_registry())
    assert _run(handler, "kept = 41", session_key="err-keep").status == "ok"

    failed = _run(handler, "1 / 0", session_key="err-keep")
    assert failed.status == "error"
    assert failed.error_code == "python_runtime_error"
    assert "ZeroDivisionError" in (failed.error_message or "")

    after = _run(handler, "kept", session_key="err-keep")
    assert after.status == "ok"
    assert isinstance(after.output, dict)
    assert after.output["value"] == "41"


# ---------------------------------------------------------------------------
# 9. Worker crash recovery + reset argument
# ---------------------------------------------------------------------------


def test_worker_crash_recovers_with_fresh_namespace() -> None:
    from magi_agent.tools.python_exec import PythonExecConfig
    from magi_agent.tools.python_exec_worker import PythonExecSessionPool

    config = PythonExecConfig()
    pool = PythonExecSessionPool(config)
    try:
        first = pool.execute("crash", "x = 1", workspace_root=None)
        assert first["ok"] is True

        # Kill the worker out-of-band via the pool handle.
        pool._workers["crash"].close()  # noqa: SLF001

        recovered = pool.execute("crash", "1 + 1", workspace_root=None)
        assert recovered["ok"] is True
        assert recovered["namespace_reset"] is True
        assert recovered["value"] == "2"
    finally:
        pool.close_all()


def test_reset_argument_discards_namespace() -> None:
    handler = _handler(_bound_registry())
    assert _run(handler, "x = 1", session_key="reset-arg").status == "ok"
    result = _run(handler, "x + 1", session_key="reset-arg", reset=True)
    assert result.status == "error"
    assert result.error_code == "python_runtime_error"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_missing_code_rejected() -> None:
    handler = _handler(_bound_registry())
    result = handler({"code": "   "}, _context("edge"))
    assert result.status == "error"
    assert result.error_code == "missing_code"


def test_syntax_error_reported_without_executing() -> None:
    handler = _handler(_bound_registry())
    result = _run(handler, "def broken(:", session_key="edge-syntax")
    assert result.status == "error"
    assert result.error_code == "python_syntax_error"


# ---------------------------------------------------------------------------
# 10. Dispatcher end-to-end
# ---------------------------------------------------------------------------


class _AllowAllPolicy:
    """Permissive permission policy for the end-to-end dispatch test."""

    def decide(self, manifest, arguments, context, *, mode):  # noqa: ANN001, ANN201
        from magi_agent.tools.permission import (
            ToolPermissionDecision,
            base_tool_metadata,
        )

        return ToolPermissionDecision(
            action="allow",
            reason="test-permissive",
            metadata=base_tool_metadata(manifest, mode=mode, reason="test-permissive"),
        )


def test_dispatcher_requires_approval_with_default_policy(tmp_path: Path) -> None:
    """The real policy treats PythonExec like Bash: approval-class execute."""
    from magi_agent.tools.dispatcher import ToolDispatcher

    registry = _bound_registry()
    dispatcher = ToolDispatcher(registry)
    result = asyncio.run(
        dispatcher.dispatch(
            "PythonExec",
            {"code": "40 + 2"},
            _context("dispatch-approval", workspace_root=str(tmp_path)),
            mode="act",
        )
    )
    assert result.status == "needs_approval"


def test_dispatcher_end_to_end(tmp_path: Path) -> None:
    from magi_agent.tools.dispatcher import ToolDispatcher

    registry = _bound_registry()
    dispatcher = ToolDispatcher(registry, permission_policy=_AllowAllPolicy())
    context = ToolContext(
        bot_id="test-bot",
        session_id="dispatch",
        session_key="dispatch",
        workspace_root=str(tmp_path),
    )

    result = asyncio.run(
        dispatcher.dispatch(
            "PythonExec",
            {"code": "40 + 2"},
            context,
            mode="act",
        )
    )
    assert isinstance(result, ToolResult)
    assert result.status == "ok"
    assert result.latency_ms is not None
    assert isinstance(result.output, dict)
    assert result.output["value"] == "42"
