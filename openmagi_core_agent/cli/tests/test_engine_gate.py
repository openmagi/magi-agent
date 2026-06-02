"""Tests for the permission-gate wiring into ``MagiEngineDriver`` (PR-F-gate).

The existing ``MockRunner`` (test_engine.py) has NO ``agent`` and yields
pre-baked ADK events, so it cannot exercise the ``before_tool_callback``
interception path. Here we build a **gate-aware fake** that mirrors the real
``google-adk`` contract (verified against the installed
``google/adk/flows/llm_flows/functions.py``):

    for callback in agent.canonical_before_tool_callbacks:
        resp = callback(tool=tool, args=function_args, tool_context=tool_context)
        if inspect.isawaitable(resp): resp = await resp
        if resp: break
    if resp is None:
        resp = await __call_tool_async(...)   # tool actually runs

Semantics proven end-to-end through ``MagiEngineDriver.run_turn_stream(...,
gate=<real RulesPermissionGate>)``:
- callback returns a dict  -> tool SKIPPED, dict is the tool result (DENY)
- callback returns None     -> tool RUNS (ALLOW / applied rewrite)
- ``args`` mutated in place -> tool receives the rewritten input (UPDATED_INPUT)

Sync test convention (matches test_engine.py): every test drives async code via
``asyncio.run(...)`` — this package has no pytest-asyncio configured.
"""

from __future__ import annotations

import asyncio
import inspect
import json

from google.adk.events import Event
from google.genai import types

from openmagi_core_agent.cli.contracts import (
    EngineResult,
    PermissionDecision,
    Terminal,
)
from openmagi_core_agent.cli.engine import MagiEngineDriver
from openmagi_core_agent.cli.headless import drain
from openmagi_core_agent.cli.permissions import (
    RulesEngine,
    RulesPermissionGate,
)
from openmagi_core_agent.cli.contracts import PermissionUpdate, PromptSink


# ---------------------------------------------------------------------------
# Gate-aware fake agent + runner mirroring the ADK before_tool_callback contract
# ---------------------------------------------------------------------------
class FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class FakeAgent:
    """Mirror of the ADK ``LlmAgent`` callback surface.

    ``before_tool_callback`` is a settable attribute (single callable OR list OR
    None) and ``canonical_before_tool_callbacks`` normalizes it to a list exactly
    as ADK does.
    """

    def __init__(self) -> None:
        self.before_tool_callback: object | None = None

    @property
    def canonical_before_tool_callbacks(self) -> list:
        cb = self.before_tool_callback
        if not cb:
            return []
        if isinstance(cb, list):
            return cb
        return [cb]


class GateAwareRunner:
    """Runner with an ``.agent`` that drives the before_tool_callback loop.

    For a scripted tool call (``tool_name`` + ``tool_args``) it:
      1. emits a ``function_call`` Event (tool_start),
      2. runs the canonical before-tool callbacks with a MUTABLE ``args`` dict,
      3. if a callback returned a dict -> records ``blocked`` (tool NOT run) and
         emits a ``function_response`` Event carrying that dict,
      4. else -> records the tool as executed with the (possibly mutated) args
         and emits a normal ``function_response`` Event.

    ``self.executed`` / ``self.blocked`` let tests assert what actually happened.
    """

    def __init__(self, *, tool_name: str, tool_args: dict, call_id: str = "call-1") -> None:
        self.agent = FakeAgent()
        self._tool_name = tool_name
        self._tool_args = tool_args
        self._call_id = call_id
        self.executed: list[dict] = []
        self.blocked: list[dict] = []

    async def run_async(self, **_kwargs: object):
        # 1. tool_start
        yield Event(
            author="model",
            content=types.Content(
                role="model",
                parts=[
                    types.Part(
                        function_call=types.FunctionCall(
                            name=self._tool_name,
                            args=dict(self._tool_args),
                            id=self._call_id,
                        )
                    )
                ],
            ),
        )

        # 2. before-tool callback loop (exact ADK shape).
        args = dict(self._tool_args)  # mutable dict the callback may rewrite
        tool = FakeTool(self._tool_name)
        resp = None
        for callback in self.agent.canonical_before_tool_callbacks:
            resp = callback(tool=tool, args=args, tool_context=None)
            if inspect.isawaitable(resp):
                resp = await resp
            if resp:
                break

        if resp is None:
            # tool actually runs with the (possibly mutated) args.
            self.executed.append(dict(args))
            response_dict = {"out": "ok", "received_args": dict(args)}
        else:
            # tool SKIPPED; the dict IS the tool result.
            self.blocked.append(dict(resp))
            response_dict = dict(resp)

        # 3. function_response
        yield Event(
            author="user",
            content=types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            name=self._tool_name,
                            response=response_dict,
                            id=self._call_id,
                        )
                    )
                ],
            ),
        )


def _turn_input(session_id: str, turn_id: str = "turn-1", prompt: str = "go") -> dict:
    return {"prompt": prompt, "session_id": session_id, "turn_id": turn_id}


def _allow_gate(tool: str) -> RulesPermissionGate:
    return RulesPermissionGate(
        rules=RulesEngine(
            default_rules=[PermissionUpdate(tool=tool, matcher="*", decision="allow")]
        )
    )


def _deny_gate(tool: str, *, interrupt_sink: bool = False) -> RulesPermissionGate:
    return RulesPermissionGate(
        rules=RulesEngine(
            default_rules=[PermissionUpdate(tool=tool, matcher="*", decision="deny")]
        )
    )


class _FixedSink(PromptSink):
    """A prompt sink that returns a fixed decision for any ``ask``."""

    def __init__(self, decision: PermissionDecision) -> None:
        self._decision = decision

    async def ask(self, req: object) -> PermissionDecision:
        return self._decision


# ---------------------------------------------------------------------------
# 1. allow rule -> tool executes
# ---------------------------------------------------------------------------
def test_gate_allow_rule_executes_tool() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    driver = MagiEngineDriver(runner=runner)
    gate = _allow_gate("Bash")

    events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-allow"), cancel=asyncio.Event(), gate=gate
            )
        )
    )

    assert terminal.terminal is Terminal.completed
    assert runner.executed == [{"cmd": "ls"}]
    assert runner.blocked == []


# ---------------------------------------------------------------------------
# 2. deny rule -> tool does NOT execute, blocked result surfaced
# ---------------------------------------------------------------------------
def test_gate_deny_rule_blocks_tool() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "rm -rf /"})
    driver = MagiEngineDriver(runner=runner)
    gate = _deny_gate("Bash")

    events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-deny"), cancel=asyncio.Event(), gate=gate
            )
        )
    )

    assert terminal.terminal is Terminal.completed
    assert runner.executed == []
    assert len(runner.blocked) == 1
    blocked = runner.blocked[0]
    assert blocked["status"] == "blocked"
    assert blocked["error"] == "permission_denied"
    assert blocked["tool"] == "Bash"


# ---------------------------------------------------------------------------
# 3. deny + interrupt -> turn aborts (cancel event set)
# ---------------------------------------------------------------------------
def test_gate_deny_interrupt_aborts_turn() -> None:
    # Use an ask verdict + a sink that denies-with-interrupt to exercise interrupt.
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "danger"})
    driver = MagiEngineDriver(runner=runner)
    sink = _FixedSink(PermissionDecision(kind="deny", interrupt=True))
    gate = RulesPermissionGate(rules=RulesEngine(), sinks=[sink])

    cancel = asyncio.Event()
    events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-deny-int"), cancel=cancel, gate=gate
            )
        )
    )

    assert runner.executed == []
    assert len(runner.blocked) == 1
    # The interrupt set the turn's cancel event -> aborted terminal.
    assert cancel.is_set()
    assert terminal.terminal is Terminal.aborted


# ---------------------------------------------------------------------------
# 4. ask + sink-allows -> executes;  ask + sink-denies (and no-sink) -> blocked
# ---------------------------------------------------------------------------
def test_gate_ask_sink_allows_executes() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    driver = MagiEngineDriver(runner=runner)
    sink = _FixedSink(PermissionDecision(kind="allow"))
    gate = RulesPermissionGate(rules=RulesEngine(), sinks=[sink])

    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-ask-allow"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    assert runner.executed == [{"cmd": "ls"}]
    assert runner.blocked == []


def test_gate_ask_sink_denies_blocks() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    driver = MagiEngineDriver(runner=runner)
    sink = _FixedSink(PermissionDecision(kind="deny", feedback="nope"))
    gate = RulesPermissionGate(rules=RulesEngine(), sinks=[sink])

    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-ask-deny"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    assert runner.executed == []
    assert len(runner.blocked) == 1
    assert runner.blocked[0]["feedback"] == "nope"


def test_gate_ask_no_sink_safe_denies() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    driver = MagiEngineDriver(runner=runner)
    gate = RulesPermissionGate(rules=RulesEngine(), sinks=[])  # ask -> safe deny

    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-ask-none"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    assert runner.executed == []
    assert len(runner.blocked) == 1


# ---------------------------------------------------------------------------
# 5. updated_input allowed by rules -> tool receives the REWRITTEN args
# ---------------------------------------------------------------------------
def test_gate_updated_input_applied() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls /etc"})
    driver = MagiEngineDriver(runner=runner)
    # Rules: allow the rewritten cmd. Sink returns allow + updated_input.
    rules = RulesEngine(
        default_rules=[
            PermissionUpdate(tool="Bash", matcher="cmd=ls /safe", decision="allow"),
        ]
    )
    sink = _FixedSink(
        PermissionDecision(kind="allow", updated_input={"cmd": "ls /safe"})
    )
    gate = RulesPermissionGate(rules=rules, sinks=[sink])

    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-rewrite"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    # Tool ran with the REWRITTEN args, not the original.
    assert runner.executed == [{"cmd": "ls /safe"}]
    assert runner.blocked == []


# ---------------------------------------------------------------------------
# 6. updated_input that the rules DENY -> blocked, rewrite NOT applied
# ---------------------------------------------------------------------------
def test_gate_updated_input_denied_by_rules_blocks() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    driver = MagiEngineDriver(runner=runner)
    # The original `ls` would be ask->allowed by the sink, BUT the sink rewrites
    # it to a forbidden command that the rules deny. Escalation must be closed.
    rules = RulesEngine(
        default_rules=[
            PermissionUpdate(tool="Bash", matcher="*", decision="ask"),
            PermissionUpdate(tool="Bash", matcher="cmd=rm -rf /", decision="deny"),
        ]
    )
    sink = _FixedSink(
        PermissionDecision(kind="allow", updated_input={"cmd": "rm -rf /"})
    )
    gate = RulesPermissionGate(rules=rules, sinks=[sink])

    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-rewrite-deny"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    # Tool BLOCKED; the forbidden rewrite was NOT applied.
    assert runner.executed == []
    assert len(runner.blocked) == 1
    assert runner.blocked[0]["error"] == "permission_denied"


# ---------------------------------------------------------------------------
# 7. No agent on runner + gate passed -> no crash, behaves as before
# ---------------------------------------------------------------------------
def test_gate_no_agent_runner_no_crash() -> None:
    # The plain MockRunner shape (test_engine.py) has no `.agent`.
    class _AgentlessRunner:
        async def run_async(self, **_kwargs: object):
            yield Event(
                author="model",
                partial=True,
                content=types.Content(role="model", parts=[types.Part(text="hi")]),
            )

    runner = _AgentlessRunner()
    driver = MagiEngineDriver(runner=runner)
    gate = _deny_gate("Bash")  # gate passed but cannot attach (no agent)

    events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-noagent"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    assert any(ev.type == "token" for ev in events)


# ---------------------------------------------------------------------------
# 8. Callback restoration -> before_tool_callback back to original after turn
# ---------------------------------------------------------------------------
def test_gate_restores_callback_after_turn() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})

    def _sentinel(**_kw: object) -> None:  # pre-existing callback
        return None

    runner.agent.before_tool_callback = _sentinel
    driver = MagiEngineDriver(runner=runner)
    gate = _allow_gate("Bash")

    asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-restore"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    # Restored to EXACTLY the original value.
    assert runner.agent.before_tool_callback is _sentinel


def test_gate_composes_with_existing_callback_gate_first() -> None:
    # An existing callback that records calls; the gate deny must short-circuit
    # FIRST so the existing callback never sees the call.
    seen: list[str] = []

    def _existing(**kw: object) -> None:
        seen.append("existing")
        return None

    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "x"})
    runner.agent.before_tool_callback = _existing
    driver = MagiEngineDriver(runner=runner)
    gate = _deny_gate("Bash")

    asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-compose"), cancel=asyncio.Event(), gate=gate
            )
        )
    )
    # Gate denied first -> the existing callback was never reached.
    assert seen == []
    assert runner.executed == []
    assert len(runner.blocked) == 1
    # And the original callback was restored.
    assert runner.agent.before_tool_callback is _existing


# ---------------------------------------------------------------------------
# 9. gate=None -> no attach, today's behavior (byte-for-byte)
# ---------------------------------------------------------------------------
def test_gate_none_does_not_attach() -> None:
    runner = GateAwareRunner(tool_name="Bash", tool_args={"cmd": "ls"})
    driver = MagiEngineDriver(runner=runner)

    _events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None, _turn_input("s-gate-none"), cancel=asyncio.Event(), gate=None
            )
        )
    )
    assert terminal.terminal is Terminal.completed
    # No callback was attached, so the tool ran with original args.
    assert runner.executed == [{"cmd": "ls"}]
    assert runner.agent.before_tool_callback is None


def test_composio_mcp_tool_reaches_permission_gate_and_blocks_without_sink() -> None:
    runner = GateAwareRunner(
        tool_name="composio_COMPOSIO_MULTI_EXECUTE_TOOL",
        tool_args={
            "tool": "GMAIL_SEND_EMAIL",
            "arguments": {"to": "person@example.com", "body": "hello"},
        },
    )
    driver = MagiEngineDriver(runner=runner)
    gate = RulesPermissionGate(rules=RulesEngine(), sinks=[])

    events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                _turn_input("s-composio-deny"),
                cancel=asyncio.Event(),
                gate=gate,
            )
        )
    )

    assert terminal.terminal is Terminal.completed
    assert runner.executed == []
    assert len(runner.blocked) == 1
    assert runner.blocked[0]["tool"] == "composio_COMPOSIO_MULTI_EXECUTE_TOOL"
    assert runner.blocked[0]["error"] == "permission_denied"


def test_composio_mcp_tool_can_be_explicitly_allowed_by_existing_rules() -> None:
    runner = GateAwareRunner(
        tool_name="composio_COMPOSIO_SEARCH_TOOLS",
        tool_args={"query": "gmail read latest messages"},
    )
    driver = MagiEngineDriver(runner=runner)
    gate = _allow_gate("composio_COMPOSIO_SEARCH_TOOLS")

    events, terminal = asyncio.run(
        drain(
            driver.run_turn_stream(
                None,
                _turn_input("s-composio-allow"),
                cancel=asyncio.Event(),
                gate=gate,
            )
        )
    )

    assert terminal.terminal is Terminal.completed
    assert runner.executed == [{"query": "gmail read latest messages"}]
    assert runner.blocked == []
