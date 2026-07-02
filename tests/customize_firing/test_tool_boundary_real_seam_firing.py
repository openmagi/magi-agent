"""Real-seam firing tests for the customize tool-boundary bridge (N-01).

The other ``tests/customize_firing`` tool-boundary tests drive the composed
``magi_agent.facades.execute_tool_with_hooks`` facade directly. This file
instead drives an authored rule through the ACTUAL engine path: a fake ADK
runner whose ``run_async`` performs one scripted tool call by invoking the
agent's ``before_tool_callback`` / ``after_tool_callback`` lists (the exact
lists the engine attaches, including the customize bridge from
``magi_agent.cli.customize_tool_wiring``).

The scripted call reproduces the installed ADK
``google/adk/flows/llm_flows/functions.py`` contract at the minimum needed
here: the before callbacks run in order and the FIRST non-None dict SKIPS the
tool and becomes the result (DENY); otherwise the tool runs with the
(possibly mutated) args; then the after callbacks run and the FIRST non-None
dict REPLACES the response. The canonical contract reference is the
``engine.py`` ``_build_gate_before_tool`` docstring; this harness is the
re-verification anchor if ADK's callback list semantics ever change.
"""
from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from magi_agent.adk_bridge.lifecycle_shell_command_control import (
    reset_shared_budget_for_tests,
    shell_budget_for,
)
from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import MagiEngineDriver
from magi_agent.customize.store import set_custom_rule

# Secret-shaped fixture assembled from fragments so GitHub push protection
# never sees a contiguous provider literal.
_AWS_KEY = "AKIA" + "BCDEFGHIJKLMNOPQ"


# ---------------------------------------------------------------------------
# Fake ADK harness with a scripted tool call.
# ---------------------------------------------------------------------------


class _FakeAgent:
    def __init__(self) -> None:
        self.before_tool_callback = None
        self.after_tool_callback = None


def _as_cb_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    return [value]


class _ScriptedStream:
    """ADK stream stub that runs one scripted tool call on first iteration."""

    def __init__(self, script) -> None:
        self._script = script
        self._done = False

    def __aiter__(self) -> "_ScriptedStream":
        return self

    async def __anext__(self) -> Any:
        if not self._done:
            self._done = True
            await self._script()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        pass


class ScriptedRunner:
    def __init__(
        self,
        *,
        agent: _FakeAgent,
        tool_args: dict[str, Any],
        tool_response: dict[str, Any],
    ) -> None:
        self.agent = agent
        self._tool_args = tool_args
        self._tool_response = tool_response
        self.tool_called = False
        self.observed_args: dict[str, Any] | None = None
        self.final_response: Any = None

    async def _run_script(self) -> None:
        tool = SimpleNamespace(name="shell_exec")
        args = dict(self._tool_args)

        denied: dict[str, Any] | None = None
        for cb in _as_cb_list(self.agent.before_tool_callback):
            res = await cb(tool=tool, args=args, tool_context=None)
            if isinstance(res, dict):
                denied = res
                break
        if denied is not None:
            self.tool_called = False
            self.final_response = denied
            return

        # Tool runs with the (possibly mutated) args.
        self.tool_called = True
        self.observed_args = dict(args)
        response: Any = dict(self._tool_response)

        for cb in _as_cb_list(self.agent.after_tool_callback):
            res = await cb(
                tool=tool, args=args, tool_context=None, tool_response=response
            )
            if isinstance(res, dict):
                response = res
        self.final_response = response

    def run_async(
        self, *, user_id, session_id, invocation_id, new_message
    ) -> AsyncIterator[Any]:
        return _ScriptedStream(self._run_script)


class FakeEventBridge:
    def project_adk_event(self, adk_event: object, *, turn_id: str) -> Any:
        return _FakeBridgeResult({})


class _FakeBridgeResult:
    def __init__(self, event_dict: dict[str, Any]) -> None:
        self._event_dict = event_dict

    @property
    def agent_events(self) -> list[dict[str, Any]]:
        return [self._event_dict] if self._event_dict else []


def _fake_sanitize(d: dict[str, Any]) -> dict[str, Any] | None:
    return d if d else None


class FakeRunnerTurnInputCls:
    def __new__(cls, **kwargs: Any) -> "FakeRunnerTurnInputCls":  # type: ignore[misc]
        obj = object.__new__(cls)
        for k, v in kwargs.items():
            setattr(obj, k, v)
        return obj


class FakeRunnerAdapter:
    def __init__(self, *, runner: ScriptedRunner) -> None:
        self.runner = runner

    def run_turn(self, runner_input: Any) -> AsyncIterator[Any]:
        return self.runner.run_async(
            user_id=getattr(runner_input, "userId", "cli"),
            session_id=getattr(runner_input, "sessionId", "s"),
            invocation_id=getattr(runner_input, "invocationId", "t"),
            new_message=getattr(runner_input, "newMessage", None),
        )


class FakeContent:
    def __init__(self, *, role: str, parts: list) -> None:
        self.role = role
        self.parts = parts


class FakePart:
    def __init__(self, *, text: str) -> None:
        self.text = text


class FakeTypes:
    Content = FakeContent
    Part = FakePart


def _patch_lazy_deps(monkeypatch: pytest.MonkeyPatch, runner: ScriptedRunner) -> None:
    import magi_agent.cli.engine as engine_mod

    fake_deps = {
        "types": FakeTypes(),
        "OpenMagiEventBridge": lambda **kwargs: FakeEventBridge(),
        "OpenMagiRunnerAdapter": lambda **kwargs: FakeRunnerAdapter(runner=runner),
        "RunnerTurnInput": FakeRunnerTurnInputCls,
        "sanitize_agent_event": _fake_sanitize,
    }
    monkeypatch.setattr(engine_mod, "_lazy_engine_deps", lambda: fake_deps)


def _run(driver: MagiEngineDriver) -> list[Any]:
    async def _collect() -> list[Any]:
        cancel = asyncio.Event()
        items: list[Any] = []
        async for item in driver.run_turn_stream(
            runtime=None,
            turn_input={"prompt": "go", "session_id": "s1", "turn_id": "t1"},
            cancel=cancel,
        ):
            items.append(item)
        return items

    return asyncio.run(_collect())


def _drive_turn(
    monkeypatch: pytest.MonkeyPatch,
    *,
    tool_args: dict[str, Any],
    tool_response: dict[str, Any],
) -> ScriptedRunner:
    agent = _FakeAgent()
    runner = ScriptedRunner(
        agent=agent, tool_args=tool_args, tool_response=tool_response
    )
    _patch_lazy_deps(monkeypatch, runner)
    driver = MagiEngineDriver(runner=runner, user_hook_bus=None)
    items = _run(driver)
    assert items[-1].terminal == Terminal.completed
    return runner


# ---------------------------------------------------------------------------
# Rule builders.
# ---------------------------------------------------------------------------


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
def cfg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("MAGI_CUSTOMIZE_VERIFICATION_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_CUSTOM_RULES_ENABLED", "1")
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    cfile = tmp_path / "customize.json"
    monkeypatch.setenv("MAGI_CUSTOMIZE", str(cfile))
    reset_shared_budget_for_tests()
    return cfile


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)
def test_authored_shell_command_block_rule_blocks_live_dispatch(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    set_custom_rule(
        _shell_command_rule(rid="cr_rs_block", action="block", inline="exit 1"),
        path=cfg,
    )
    runner = _drive_turn(
        monkeypatch,
        tool_args={"command": "ls"},
        tool_response={"output": "should not run", "status": "ok"},
    )
    # The authored block rule blocked the live dispatch.
    assert runner.tool_called is False
    assert isinstance(runner.final_response, dict)
    assert runner.final_response["status"] == "blocked"
    assert runner.final_response["blocked_by"] == "shell_command_rule"


def test_authored_prompt_injection_rule_rewrites_live_tool_args(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_PROMPT_INJECTION_ENABLED", "1")
    set_custom_rule(
        _prompt_injection_rule(rid="cr_rs_inject", value=" --dry-run"),
        path=cfg,
    )
    runner = _drive_turn(
        monkeypatch,
        tool_args={"command": "ls"},
        tool_response={"output": "ran", "status": "ok"},
    )
    assert runner.tool_called is True
    assert runner.observed_args == {"command": "ls --dry-run"}


def test_authored_output_rewrite_rule_redacts_live_tool_response(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAGI_CUSTOMIZE_OUTPUT_REWRITE_ENABLED", "1")
    set_custom_rule(
        _output_rewrite_rule(
            rid="cr_rs_redact", pattern="AKIA[0-9A-Z]{16}", replacement="***"
        ),
        path=cfg,
    )
    runner = _drive_turn(
        monkeypatch,
        tool_args={"command": "ls"},
        tool_response={"output": f"leak {_AWS_KEY} tail", "status": "ok"},
    )
    assert runner.tool_called is True
    assert isinstance(runner.final_response, dict)
    assert _AWS_KEY not in runner.final_response["output"]
    assert "***" in runner.final_response["output"]


def test_flags_off_real_seam_byte_identical(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No customize master flag ON: the bridge does not attach and the tool
    # observes original args + response.
    set_custom_rule(
        _prompt_injection_rule(rid="cr_rs_off", value=" --dry-run"),
        path=cfg,
    )
    runner = _drive_turn(
        monkeypatch,
        tool_args={"command": "ls"},
        tool_response={"output": f"raw {_AWS_KEY}", "status": "ok"},
    )
    assert runner.tool_called is True
    assert runner.observed_args == {"command": "ls"}
    assert runner.final_response == {"output": f"raw {_AWS_KEY}", "status": "ok"}


@pytest.mark.skipif(
    sys.platform.startswith("win"),
    reason="shell_runner honest-degrades on Windows",
)
def test_shell_budget_shared_across_tool_boundary_and_lifecycle_slots(
    cfg: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A block rule cannot block once the shared per-turn budget is exhausted:
    the tool-boundary shell stage short-circuits to budget_exhausted (no spawn,
    no verdict) instead of running, so dispatch proceeds. This proves the
    tool-boundary slot shares the same (session, turn) counter as the 9
    lifecycle slots."""
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_COMMAND_ENABLED", "1")
    monkeypatch.setenv("MAGI_CUSTOMIZE_SHELL_AUDIT_BUDGET", "1")
    set_custom_rule(
        _shell_command_rule(rid="cr_rs_budget", action="block", inline="exit 1"),
        path=cfg,
    )
    # Pre-exhaust the shared budget for the turn identity used by _run.
    reset_shared_budget_for_tests()
    _remaining, decrement = shell_budget_for("s1", "t1")
    decrement()  # remaining -> 0

    runner = _drive_turn(
        monkeypatch,
        tool_args={"command": "ls"},
        tool_response={"output": "real output", "status": "ok"},
    )
    # Budget exhausted -> the block rule's shell never spawns -> no block.
    assert runner.tool_called is True
    assert runner.final_response == {"output": "real output", "status": "ok"}
