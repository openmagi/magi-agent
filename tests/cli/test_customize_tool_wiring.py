"""Unit tests for the customize tool-boundary ADK callback bridge (N-01).

Covers :mod:`magi_agent.cli.customize_tool_wiring`: the third agent-level
before/after-tool bridge that fires authored customize rules at the live ADK
tool boundary (after the permission gate and the user settings.json HookBus).

The bridge callback contract is the SAME one documented on
``engine.py`` ``_build_gate_before_tool``: a before callback returning a dict
SKIPS the tool and uses the dict as the result (DENY), returning None lets the
tool run (ALLOW), and mutating ``args`` in place rewrites the tool input
(UPDATED_INPUT). An after callback returning a non-None dict replaces the tool
response.

The bridges are exercised by attaching them to a fake agent and calling the
resulting callables directly, so the tests do not need a real ADK runner.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from magi_agent.adk_bridge.lifecycle_shell_command_control import (
    reset_shared_budget_for_tests,
)
from magi_agent.cli.customize_tool_wiring import (
    attach_customize_tool_callbacks,
    customize_tool_boundary_enabled,
)
from magi_agent.customize.store import set_custom_rule

# A secret-shaped fixture assembled from fragments so GitHub push protection
# never sees a contiguous provider literal.
_AWS_KEY = "AKIA" + "BCDEFGHIJKLMNOPQ"


def _fake_runner() -> SimpleNamespace:
    agent = SimpleNamespace(before_tool_callback=None, after_tool_callback=None)
    return SimpleNamespace(agent=agent)


def _attach(session_id: str = "s1", turn_id: str = "t1") -> SimpleNamespace:
    runner = _fake_runner()
    attachment = attach_customize_tool_callbacks(
        runner=runner, session_id=session_id, turn_id=turn_id
    )
    return SimpleNamespace(runner=runner, attachment=attachment)


def _before_bridge(runner: SimpleNamespace):
    callbacks = runner.agent.before_tool_callback
    assert isinstance(callbacks, list) and callbacks
    return callbacks[-1]


def _after_bridge(runner: SimpleNamespace):
    callbacks = runner.agent.after_tool_callback
    assert isinstance(callbacks, list) and callbacks
    return callbacks[-1]


def _shell_command_rule(*, rid: str, action: str, inline: str) -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": "before_tool_use",
        "action": action,
        "what": {
            "kind": "shell_command",
            "payload": {
                "source": "inline",
                "inline": inline,
                "timeout_seconds": 5,
                "shell": "bash",
            },
        },
    }


def _shell_check_rule(*, rid: str, action: str, inline: str) -> dict:
    rule = _shell_command_rule(rid=rid, action=action, inline=inline)
    rule["what"]["kind"] = "shell_check"
    return rule


def _prompt_injection_rule(*, rid: str, value: str) -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": "before_tool_use",
        "action": "audit",
        "what": {
            "kind": "prompt_injection",
            "payload": {
                "mode": "append",
                "target_arg_key": "command",
                "value": value,
                "condition": {"tool": "shell_exec"},
            },
        },
    }


def _output_rewrite_rule(*, rid: str, pattern: str, replacement: str) -> dict:
    return {
        "id": rid,
        "scope": "always",
        "enabled": True,
        "firesAt": "after_tool_use",
        "action": "audit",
        "what": {
            "kind": "output_rewrite",
            "payload": {
                "mode": "redact",
                "pattern": pattern,
                "replacement": replacement,
            },
        },
    }


@pytest.fixture
def cfg_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    return cfile


def test_attach_returns_none_when_all_customize_flags_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No customize master flag set -> attach is a no-op, callbacks untouched.

    Explicitly zeroes every per-slot flag so the test is hermetic under the
    full-profile overlay (which now seeds MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED=1
    and peers ON by default).
    """
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "0")
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "0")
    assert customize_tool_boundary_enabled() is False
    state = _attach()
    assert state.attachment is None
    assert state.runner.agent.before_tool_callback is None
    assert state.runner.agent.after_tool_callback is None


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)
def test_before_bridge_denies_on_shell_command_block_rule(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    set_custom_rule(
        _shell_command_rule(rid="cr_tw_block", action="block", inline="exit 1"),
        path=cfg_path,
    )
    state = _attach()
    before = _before_bridge(state.runner)
    result = asyncio.run(
        before(tool=SimpleNamespace(name="shell_exec"), args={"command": "ls"})
    )
    assert isinstance(result, dict)
    assert result["status"] == "blocked"
    assert result["blocked_by"] == "shell_command_rule"


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)
def test_before_bridge_denies_on_shell_check_failed_rule(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_CHECK_ENABLED", "1")
    set_custom_rule(
        _shell_check_rule(rid="cr_tw_check", action="block", inline="exit 1"),
        path=cfg_path,
    )
    state = _attach()
    before = _before_bridge(state.runner)
    result = asyncio.run(
        before(tool=SimpleNamespace(name="shell_exec"), args={"command": "ls"})
    )
    assert isinstance(result, dict)
    assert result["status"] == "blocked"
    assert result["blocked_by"] == "shell_check_rule"


def test_before_bridge_mutates_args_in_place_for_prompt_injection(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    set_custom_rule(
        _prompt_injection_rule(rid="cr_tw_inject", value=" --dry-run"),
        path=cfg_path,
    )
    state = _attach()
    before = _before_bridge(state.runner)
    args = {"command": "ls"}
    result = asyncio.run(before(tool=SimpleNamespace(name="shell_exec"), args=args))
    # ALLOW (None) with the args rewritten in place (ADK UPDATED_INPUT contract).
    assert result is None
    assert args["command"] == "ls --dry-run"


def test_after_bridge_rewrites_output_and_llm_output_keys(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    set_custom_rule(
        _output_rewrite_rule(
            rid="cr_tw_redact", pattern="AKIA[0-9A-Z]{16}", replacement="***"
        ),
        path=cfg_path,
    )
    state = _attach()
    after = _after_bridge(state.runner)
    response = {
        "output": f"leak {_AWS_KEY} tail",
        "llmOutput": f"also {_AWS_KEY} here",
        "status": "ok",
    }
    result = asyncio.run(
        after(
            tool=SimpleNamespace(name="shell_exec"),
            args={"command": "ls"},
            tool_response=response,
        )
    )
    assert isinstance(result, dict)
    assert _AWS_KEY not in result["output"]
    assert _AWS_KEY not in result["llmOutput"]
    assert "***" in result["output"]
    assert "***" in result["llmOutput"]
    # Non-str keys preserved.
    assert result["status"] == "ok"


def test_after_bridge_returns_none_when_no_rule_matches(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    set_custom_rule(
        _output_rewrite_rule(
            rid="cr_tw_nomatch", pattern="AKIA[0-9A-Z]{16}", replacement="***"
        ),
        path=cfg_path,
    )
    state = _attach()
    after = _after_bridge(state.runner)
    response = {"output": "nothing sensitive here", "status": "ok"}
    result = asyncio.run(
        after(
            tool=SimpleNamespace(name="shell_exec"),
            args={"command": "ls"},
            tool_response=response,
        )
    )
    # No key changed -> original response preserved (None).
    assert result is None


def test_bridges_fail_open_on_malformed_customize_store(
    cfg_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    # Broken JSON on disk: the triple-gated helpers must swallow the parse
    # error and the bridges must never propagate.
    cfg_path.write_text("{ this is not json", encoding="utf-8")
    state = _attach()
    before = _before_bridge(state.runner)
    after = _after_bridge(state.runner)

    before_result = asyncio.run(
        before(tool=SimpleNamespace(name="shell_exec"), args={"command": "ls"})
    )
    after_result = asyncio.run(
        after(
            tool=SimpleNamespace(name="shell_exec"),
            args={"command": "ls"},
            tool_response={"output": "x", "status": "ok"},
        )
    )
    assert before_result is None
    assert after_result is None
