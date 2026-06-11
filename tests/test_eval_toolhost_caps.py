"""Eval-profile toolhost caps must be REAL: env-driven config, call-time fuzzy
flag, and bash observability (partial output on timeout, head+tail truncation).

Prior state: ``bind_core_toolhost_handlers`` built ``CoreToolhostHandlerSet()``
with no arguments, so the eval profile's ``MAGI_TOOL_COMMAND_TIMEOUT_MS`` /
``MAGI_TOOL_MAX_OUTPUT_BYTES`` / ``MAGI_READ_QUALITY_ENABLED`` env defaults were
dead wiring — every CLI run executed with a 5s bash timeout, 8KB head-only
output slices, and no line numbers.
"""

import asyncio

import pytest

from magi_agent.tools import ToolDispatcher, ToolRegistry, register_core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.core_toolhost import bind_core_toolhost_handlers


def _context(workspace_root) -> ToolContext:
    return ToolContext(
        bot_id="bot-test",
        turn_id="turn-test",
        workspace_root=str(workspace_root),
        permission_scope={
            "mode": "selected_full_toolhost",
            "source": "selected_full_toolhost",
        },
    )


def _dispatch(registry: ToolRegistry, tool: str, args: dict, workspace_root):
    return asyncio.run(
        ToolDispatcher(registry).dispatch(tool, args, _context(workspace_root), mode="act")
    )


def _bound_registry() -> ToolRegistry:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    bind_core_toolhost_handlers(registry)
    return registry


# ---------------------------------------------------------------------------
# Schema bounds: the previous hard ceilings made generous budgets impossible.
# ---------------------------------------------------------------------------


def test_gate5b_config_accepts_120s_command_timeout() -> None:
    from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolHostConfig

    config = Gate5BFullToolHostConfig.model_validate({"commandTimeoutMs": 120_000})
    assert config.command_timeout_ms == 120_000


def test_gate5b_config_accepts_512_tool_calls_per_turn() -> None:
    from magi_agent.gates.gate5b_full_toolhost import Gate5BFullToolHostConfig

    config = Gate5BFullToolHostConfig.model_validate({"maxToolCallsPerTurn": 512})
    assert config.max_tool_calls_per_turn == 512


# ---------------------------------------------------------------------------
# Env plumbing: bind_core_toolhost_handlers must honor the eval-profile envs.
# ---------------------------------------------------------------------------


def test_bash_timeout_honors_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TOOL_COMMAND_TIMEOUT_MS", "500")
    registry = _bound_registry()

    result = _dispatch(registry, "Bash", {"command": "yes hello"}, tmp_path)

    # ``yes`` runs forever; only an enforced 500ms timeout (instead of the
    # hardcoded 5000ms default) ends it this fast with a timeout outcome.
    assert result.output is not None
    assert result.output.get("timedOut") is True


def test_bash_output_cap_honors_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TOOL_MAX_OUTPUT_BYTES", "2048")
    registry = _bound_registry()

    result = _dispatch(registry, "Bash", {"command": "seq 1 2000"}, tmp_path)

    assert result.status == "ok"
    stdout = result.output["stdout"]
    # Default cap is 8192; seq 1 2000 is ~8.9KB. Only the env-driven 2048-byte
    # cap produces output this small (plus the elision marker).
    assert len(stdout) < 4000


def test_file_read_quality_honors_env_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_READ_QUALITY_ENABLED", "1")
    (tmp_path / "sample.py").write_text("alpha\nbeta\n", encoding="utf-8")
    registry = _bound_registry()

    result = _dispatch(registry, "FileRead", {"path": "sample.py"}, tmp_path)

    assert result.status == "ok"
    # Read-quality mode numbers lines; the legacy path returns raw content.
    assert "1" in str(result.output["content"]).splitlines()[0]
    assert "alpha" in str(result.output["content"])


# ---------------------------------------------------------------------------
# Bash observability: partial output on timeout, head+tail truncation.
# ---------------------------------------------------------------------------


def test_bash_timeout_returns_partial_stdout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TOOL_COMMAND_TIMEOUT_MS", "500")
    registry = _bound_registry()

    result = _dispatch(registry, "Bash", {"command": "yes hello"}, tmp_path)

    assert result.output is not None
    assert result.output.get("timedOut") is True
    # The model must see what the command printed before the timeout, not a
    # bare {"error": "command_timeout"}.
    assert "hello" in str(result.output.get("stdout", ""))


def test_bash_truncation_keeps_head_and_tail(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TOOL_MAX_OUTPUT_BYTES", "1024")
    registry = _bound_registry()

    result = _dispatch(registry, "Bash", {"command": "seq 1 2000"}, tmp_path)

    stdout = result.output["stdout"]
    # Test-runner failure summaries print at the END of output; a head-only
    # slice hides them. Keep both ends with an explicit elision marker.
    assert stdout.lstrip().startswith("1\n2\n")
    assert "2000" in stdout
    assert "elided" in stdout


# ---------------------------------------------------------------------------
# Fuzzy-edit flag: module-level constant froze at import time, BEFORE the eval
# profile's setdefault ran — so eval runs silently lost the fuzzy cascade.
# ---------------------------------------------------------------------------


def test_edit_fuzzy_match_enabled_reads_env_at_call_time() -> None:
    from magi_agent.config.env import edit_fuzzy_match_enabled

    assert edit_fuzzy_match_enabled(
        {"MAGI_RUNTIME_PROFILE": "eval", "MAGI_EDIT_FUZZY_MATCH_ENABLED": "1"}
    )
    assert not edit_fuzzy_match_enabled({"MAGI_RUNTIME_PROFILE": "eval"})
    assert edit_fuzzy_match_enabled({})  # full profile default stays ON


def test_file_edit_fuzzy_respects_call_time_env(tmp_path, monkeypatch) -> None:
    # Fresh registry per phase: identical (tool, args) within one turn would
    # otherwise hit the duplicate-call dedup instead of re-executing.
    edit_args = {
        "path": "mod.py",
        "old_text": "value = compute(a, b)",
        "new_text": "value = compute(a, b, c)",
    }
    (tmp_path / "mod.py").write_text("value = compute(a,  b)\n", encoding="utf-8")

    # old_text differs only in internal whitespace — exact match fails, the
    # fuzzy cascade lands it. With env=0 it must NOT fuzz.
    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "0")
    registry = _bound_registry()
    _dispatch(registry, "FileRead", {"path": "mod.py"}, tmp_path)
    denied = _dispatch(registry, "FileEdit", edit_args, tmp_path)
    assert denied.status == "error"

    monkeypatch.setenv("MAGI_EDIT_FUZZY_MATCH_ENABLED", "1")
    registry = _bound_registry()
    _dispatch(registry, "FileRead", {"path": "mod.py"}, tmp_path)
    applied = _dispatch(registry, "FileEdit", edit_args, tmp_path)
    assert applied.status == "ok"
    assert "compute(a, b, c)" in (tmp_path / "mod.py").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Eval profile defaults: budgets must match what a coding agent actually needs.
# ---------------------------------------------------------------------------


def test_eval_defaults_set_generous_tool_budgets() -> None:
    from magi_agent.runtime.local_defaults import EVAL_RUNTIME_ENV_DEFAULTS

    assert int(EVAL_RUNTIME_ENV_DEFAULTS["MAGI_TOOL_COMMAND_TIMEOUT_MS"]) >= 120_000
    assert int(EVAL_RUNTIME_ENV_DEFAULTS["MAGI_TOOL_MAX_CALLS_PER_TURN"]) >= 512
