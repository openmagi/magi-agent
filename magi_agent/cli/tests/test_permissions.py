"""PR-C1 permission rules-engine + gate-skeleton tests.

Covers:
- ``RulesEngine`` matcher semantics + precedence (specific > ``"*"``,
  deny-beats-allow tie-break, default-when-no-rule is ``ask``).
- ``RulesPermissionGate.check`` short-circuits on allow/deny (NO sink call),
  the ``ask`` path (one sink / no sink), and remember-rule persistence.

Style: sync tests driving async via ``asyncio.run(...)`` (matches A1/A2/A3).
Everything is mocked; no model, no network.
"""

from __future__ import annotations

import asyncio
import os

from magi_agent.cli.contracts import (
    ControlRequest,
    PermissionDecision,
    PermissionUpdate,
)
from magi_agent.cli.permissions import (
    EDIT_CLASS_TOOLS,
    HeadlessSink,
    RulesEngine,
    RulesPermissionGate,
    canonical_request_key,
)
from magi_agent.cli.protocol import ControlRequestFrame, ControlResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _req(
    tool: str = "Bash",
    arguments: dict | None = None,
    *,
    request_id: str = "r1",
    turn_id: str = "t1",
    reason: str = "",
) -> ControlRequest:
    return ControlRequest(
        request_id=request_id,
        turn_id=turn_id,
        tool_name=tool,
        arguments=arguments if arguments is not None else {},
        reason=reason,
    )


class RecordingSink:
    """A fake PromptSink that records calls and returns a scripted decision."""

    def __init__(self, decision: PermissionDecision) -> None:
        self._decision = decision
        self.calls: list[ControlRequest] = []

    async def ask(self, req: ControlRequest) -> PermissionDecision:
        self.calls.append(req)
        return self._decision


# ---------------------------------------------------------------------------
# canonical_request_key
# ---------------------------------------------------------------------------
def test_canonical_key_is_sorted_and_deterministic() -> None:
    a = _req("Bash", {"b": "2", "a": "1"})
    b = _req("Bash", {"a": "1", "b": "2"})
    assert canonical_request_key(a) == canonical_request_key(b) == "a=1 b=2"


# ---------------------------------------------------------------------------
# RulesEngine: matcher semantics + precedence
# ---------------------------------------------------------------------------
def test_default_when_no_rule_is_ask() -> None:
    engine = RulesEngine()
    assert engine.evaluate(_req("Bash", {"cmd": "ls"})) == "ask"


def test_wildcard_matches_any_args() -> None:
    engine = RulesEngine([PermissionUpdate(tool="Bash", matcher="*", decision="allow")])
    assert engine.evaluate(_req("Bash", {"cmd": "ls"})) == "allow"
    assert engine.evaluate(_req("Bash", {"cmd": "rm -rf /"})) == "allow"


def test_rule_does_not_apply_to_other_tool() -> None:
    engine = RulesEngine([PermissionUpdate(tool="Bash", matcher="*", decision="allow")])
    assert engine.evaluate(_req("WebFetch", {"url": "x"})) == "ask"


def test_specific_matcher_beats_wildcard() -> None:
    engine = RulesEngine(
        [
            PermissionUpdate(tool="Bash", matcher="*", decision="allow"),
            PermissionUpdate(tool="Bash", matcher="cmd=rm*", decision="deny"),
        ]
    )
    # Specific glob applies and wins over the catch-all allow.
    assert engine.evaluate(_req("Bash", {"cmd": "rm -rf /"})) == "deny"
    # Non-matching specific glob => only the wildcard applies.
    assert engine.evaluate(_req("Bash", {"cmd": "ls"})) == "allow"


def test_deny_beats_allow_on_equal_specificity() -> None:
    engine = RulesEngine(
        [
            PermissionUpdate(tool="Bash", matcher="cmd=ls", decision="allow"),
            PermissionUpdate(tool="Bash", matcher="cmd=ls", decision="deny"),
        ]
    )
    assert engine.evaluate(_req("Bash", {"cmd": "ls"})) == "deny"


def test_wildcard_deny_beats_wildcard_allow() -> None:
    engine = RulesEngine(
        [
            PermissionUpdate(tool="Bash", matcher="*", decision="allow"),
            PermissionUpdate(tool="Bash", matcher="*", decision="deny"),
        ]
    )
    assert engine.evaluate(_req("Bash", {"cmd": "anything"})) == "deny"


def test_unknown_decision_string_coerces_to_ask() -> None:
    engine = RulesEngine(
        [PermissionUpdate(tool="Bash", matcher="*", decision="weird")]
    )
    assert engine.evaluate(_req("Bash", {"cmd": "ls"})) == "ask"


def test_add_rule_persists_and_can_override_static_wildcard() -> None:
    engine = RulesEngine([PermissionUpdate(tool="Bash", matcher="*", decision="allow")])
    engine.add_rule(PermissionUpdate(tool="Bash", matcher="cmd=rm*", decision="deny"))
    assert engine.evaluate(_req("Bash", {"cmd": "rm x"})) == "deny"


# ---------------------------------------------------------------------------
# RulesPermissionGate: short-circuit + ask path + persistence
# ---------------------------------------------------------------------------
def test_allow_short_circuits_without_calling_sink() -> None:
    sink = RecordingSink(PermissionDecision(kind="deny"))
    gate = RulesPermissionGate(
        rules=RulesEngine([PermissionUpdate(tool="Bash", matcher="*", decision="allow")]),
        sinks=[sink],
    )
    decision = asyncio.run(gate.check(_req("Bash", {"cmd": "ls"})))
    assert decision.kind == "allow"
    assert sink.calls == []  # never asked


def test_deny_short_circuits_without_calling_sink() -> None:
    sink = RecordingSink(PermissionDecision(kind="allow"))
    gate = RulesPermissionGate(
        rules=RulesEngine([PermissionUpdate(tool="Bash", matcher="*", decision="deny")]),
        sinks=[sink],
    )
    decision = asyncio.run(gate.check(_req("Bash", {"cmd": "ls"})))
    assert decision.kind == "deny"
    assert sink.calls == []  # never asked


def test_ask_path_with_one_sink_calls_sink_and_returns_its_decision() -> None:
    scripted = PermissionDecision(kind="allow", feedback="ok")
    sink = RecordingSink(scripted)
    gate = RulesPermissionGate(sinks=[sink])  # empty rules => ask
    decision = asyncio.run(gate.check(_req("Bash", {"cmd": "ls"})))
    assert decision is scripted
    assert len(sink.calls) == 1


def test_no_sink_ask_is_safe_deny() -> None:
    gate = RulesPermissionGate()  # empty rules, no sinks
    decision = asyncio.run(gate.check(_req("Bash", {"cmd": "ls"})))
    assert decision.kind == "deny"


def test_remember_rule_persists_so_second_check_short_circuits() -> None:
    sink = RecordingSink(
        PermissionDecision(
            kind="allow",
            updates=[PermissionUpdate(tool="Bash", matcher="cmd=ls", decision="allow")],
        )
    )
    gate = RulesPermissionGate(sinks=[sink])
    req = _req("Bash", {"cmd": "ls"})

    first = asyncio.run(gate.check(req))
    assert first.kind == "allow"
    assert len(sink.calls) == 1

    # Second identical check must short-circuit in the rules engine.
    second = asyncio.run(gate.check(_req("Bash", {"cmd": "ls"}, request_id="r2")))
    assert second.kind == "allow"
    assert len(sink.calls) == 1  # sink NOT called again


def test_remember_deny_rule_persists() -> None:
    sink = RecordingSink(
        PermissionDecision(
            kind="deny",
            updates=[PermissionUpdate(tool="Bash", matcher="cmd=rm*", decision="deny")],
        )
    )
    gate = RulesPermissionGate(sinks=[sink])
    asyncio.run(gate.check(_req("Bash", {"cmd": "rm x"})))
    assert len(sink.calls) == 1

    second = asyncio.run(gate.check(_req("Bash", {"cmd": "rm y"}, request_id="r2")))
    assert second.kind == "deny"
    assert len(sink.calls) == 1  # short-circuited by the remembered deny rule


def test_gate_builds_defaults_when_omitted() -> None:
    gate = RulesPermissionGate()
    assert isinstance(gate.rules, RulesEngine)
    assert gate.sinks == []
    assert gate.store is not None


# ===========================================================================
# PR-C2: resolve-once race + HeadlessSink + decision options
# ===========================================================================
class FakeWriter:
    """An async writer that records every frame it is asked to write."""

    def __init__(self) -> None:
        self.frames: list[object] = []
        self.wrote = asyncio.Event()

    async def write(self, frame: object) -> None:
        self.frames.append(frame)
        self.wrote.set()


async def _await_frame(writer: FakeWriter) -> None:
    """Wait until at least one control_request frame has been written.

    A single ``sleep(0)`` is not enough when the sink task is nested inside
    ``RulesPermissionGate._race`` (multiple await hops). Awaiting the writer's
    ``wrote`` event is deterministic and avoids a delivery-before-frame race.
    """
    await writer.wrote.wait()


def _ctrl_frames(writer: FakeWriter) -> list[ControlRequestFrame]:
    return [f for f in writer.frames if isinstance(f, ControlRequestFrame)]


class _ControlledSink:
    """A PromptSink whose ``ask`` blocks on an event until released.

    Used to deterministically order finishers in a multi-sink race.
    """

    def __init__(self, decision: PermissionDecision) -> None:
        self._decision = decision
        self.gate = asyncio.Event()
        self.started = asyncio.Event()
        self.cancelled = False

    async def ask(self, req: ControlRequest) -> PermissionDecision:
        self.started.set()
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return self._decision


# ---------------------------------------------------------------------------
# HeadlessSink.ask: exactly one frame, resolves on matching response
# ---------------------------------------------------------------------------
def test_headless_ask_emits_one_frame_and_resolves() -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer)
    req = _req("Bash", {"cmd": "ls"}, request_id="rid-1")

    async def scenario() -> PermissionDecision:
        task = asyncio.ensure_future(sink.ask(req))
        # Let ask() emit the frame and start awaiting.
        await _await_frame(writer)
        assert len(_ctrl_frames(writer)) == 1
        frame = _ctrl_frames(writer)[0]
        assert frame.request_id == "rid-1"
        assert frame.request["tool_name"] == "Bash"
        assert frame.request["arguments"] == {"cmd": "ls"}
        sink.deliver(
            ControlResponse(request_id="rid-1", response={"decision": "allow"})
        )
        return await task

    decision = asyncio.run(scenario())
    assert decision.kind == "allow"
    # Exactly one frame, ever.
    assert len(_ctrl_frames(writer)) == 1


def test_headless_resolve_once_drops_late_duplicate_response() -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer)
    req = _req("Bash", {"cmd": "ls"}, request_id="rid-dup")

    async def scenario() -> PermissionDecision:
        task = asyncio.ensure_future(sink.ask(req))
        await _await_frame(writer)
        sink.deliver(
            ControlResponse(request_id="rid-dup", response={"decision": "allow"})
        )
        result = await task
        # Late duplicate: must be dropped, no exception, no double-resolve.
        sink.deliver(
            ControlResponse(request_id="rid-dup", response={"decision": "deny"})
        )
        return result

    decision = asyncio.run(scenario())
    assert decision.kind == "allow"


# ---------------------------------------------------------------------------
# RulesPermissionGate._race: resolve-once + loser teardown
# ---------------------------------------------------------------------------
def test_race_first_finisher_wins_and_loser_is_cancelled() -> None:
    fast = _ControlledSink(PermissionDecision(kind="allow", feedback="fast"))
    slow = _ControlledSink(PermissionDecision(kind="deny", feedback="slow"))
    gate = RulesPermissionGate(sinks=[fast, slow])
    req = _req("Bash", {"cmd": "ls"})

    async def scenario() -> PermissionDecision:
        task = asyncio.ensure_future(gate._race(req))
        await fast.started.wait()
        await slow.started.wait()
        fast.gate.set()  # fast answers first -> wins
        decision = await task
        return decision

    decision = asyncio.run(scenario())
    assert decision.kind == "allow"
    assert decision.feedback == "fast"
    # Loser's task was cancelled (its ask saw CancelledError); no leak.
    assert slow.cancelled is True


def test_race_single_sink_goes_through_machinery() -> None:
    sink = RecordingSink(PermissionDecision(kind="allow"))
    gate = RulesPermissionGate(sinks=[sink])
    decision = asyncio.run(gate._race(_req("Bash", {"cmd": "ls"})))
    assert decision.kind == "allow"
    assert len(sink.calls) == 1
    # A store record was created and resolved for the win.
    assert gate.store.terminal_requests  # at least one terminal record


def test_race_no_sinks_is_safe_deny() -> None:
    gate = RulesPermissionGate()
    decision = asyncio.run(gate._race(_req("Bash", {"cmd": "ls"})))
    assert decision.kind == "deny"


def test_race_no_leaked_tasks() -> None:
    fast = _ControlledSink(PermissionDecision(kind="allow"))
    slow = _ControlledSink(PermissionDecision(kind="deny"))
    gate = RulesPermissionGate(sinks=[fast, slow])

    async def scenario() -> int:
        before = len(asyncio.all_tasks())
        task = asyncio.ensure_future(gate._race(_req("Bash", {"cmd": "ls"})))
        await fast.started.wait()
        await slow.started.wait()
        fast.gate.set()
        await task
        # Give cancelled tasks a tick to settle.
        await asyncio.sleep(0)
        return len(asyncio.all_tasks()) - before

    leaked = asyncio.run(scenario())
    # Only the (now-finished) scenario coroutine itself remains; no leaked
    # sink tasks.
    assert leaked <= 1


# ---------------------------------------------------------------------------
# End-to-end: remember-rule via gate.check + HeadlessSink, short-circuits next
# ---------------------------------------------------------------------------
def test_headless_remember_rule_persists_and_short_circuits() -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer)
    gate = RulesPermissionGate(sinks=[sink])
    req = _req("Bash", {"cmd": "ls"}, request_id="rid-rem")

    async def scenario() -> tuple[PermissionDecision, PermissionDecision]:
        task = asyncio.ensure_future(gate.check(req))
        await _await_frame(writer)
        sink.deliver(
            ControlResponse(
                request_id="rid-rem",
                response={"decision": "allow", "remember": True, "matcher": "cmd=ls"},
            )
        )
        first = await task
        # Second identical check must short-circuit in the rules engine: no new
        # frame is emitted.
        second = await gate.check(_req("Bash", {"cmd": "ls"}, request_id="rid-2"))
        return first, second

    first, second = asyncio.run(scenario())
    assert first.kind == "allow"
    assert first.updates and first.updates[0].matcher == "cmd=ls"
    assert second.kind == "allow"
    # Only ONE control frame across both checks (second short-circuited).
    assert len(_ctrl_frames(writer)) == 1


# ---------------------------------------------------------------------------
# Permission modes
# ---------------------------------------------------------------------------
def test_bypass_permissions_emits_no_frame_and_allows() -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer, permission_mode="bypassPermissions")
    decision = asyncio.run(sink.ask(_req("Bash", {"cmd": "rm -rf /"})))
    assert decision.kind == "allow"
    assert _ctrl_frames(writer) == []


def test_accept_edits_auto_allows_edit_tool_no_frame() -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer, permission_mode="acceptEdits")
    edit_tool = next(iter(EDIT_CLASS_TOOLS))
    decision = asyncio.run(sink.ask(_req(edit_tool, {"path": "x"})))
    assert decision.kind == "allow"
    assert _ctrl_frames(writer) == []


def test_accept_edits_non_edit_tool_still_prompts() -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer, permission_mode="acceptEdits")
    req = _req("Bash", {"cmd": "ls"}, request_id="rid-ae")

    async def scenario() -> PermissionDecision:
        task = asyncio.ensure_future(sink.ask(req))
        await _await_frame(writer)
        assert len(_ctrl_frames(writer)) == 1  # non-edit tool prompts
        sink.deliver(
            ControlResponse(request_id="rid-ae", response={"decision": "allow"})
        )
        return await task

    decision = asyncio.run(scenario())
    assert decision.kind == "allow"


# ---------------------------------------------------------------------------
# Decision options: reject+feedback, updated_input, interrupt
# ---------------------------------------------------------------------------
def _ask_with_response(response_body: dict, *, tool: str = "Bash") -> PermissionDecision:
    writer = FakeWriter()
    sink = HeadlessSink(writer)
    req = _req(tool, {"cmd": "ls"}, request_id="rid-opt")

    async def scenario() -> PermissionDecision:
        task = asyncio.ensure_future(sink.ask(req))
        await _await_frame(writer)
        sink.deliver(ControlResponse(request_id="rid-opt", response=response_body))
        return await task

    return asyncio.run(scenario())


def test_decision_reject_with_feedback() -> None:
    decision = _ask_with_response({"decision": "deny", "feedback": "not allowed"})
    assert decision.kind == "deny"
    assert decision.feedback == "not allowed"
    assert decision.interrupt is False


def test_decision_updated_input() -> None:
    decision = _ask_with_response(
        {"decision": "allow", "updated_input": {"cmd": "ls -la"}}
    )
    assert decision.kind == "allow"
    assert decision.updated_input == {"cmd": "ls -la"}


def test_decision_interrupt() -> None:
    decision = _ask_with_response({"decision": "deny", "interrupt": True})
    assert decision.kind == "deny"
    assert decision.interrupt is True


def test_decision_allow_once_has_no_updates() -> None:
    decision = _ask_with_response({"decision": "allow"})
    assert decision.kind == "allow"
    assert decision.updates == []


# ---------------------------------------------------------------------------
# run_headless smoke: gate param accepted, no crash, returns int (no edit to
# headless.py — engine treats gate as a no-op today).
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# End-to-end: remember-DENY via gate.check + HeadlessSink, short-circuits next
# ---------------------------------------------------------------------------
def test_headless_remember_deny_rule_persists_and_short_circuits() -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer)
    gate = RulesPermissionGate(sinks=[sink])
    req = _req("Bash", {"cmd": "rm x"}, request_id="rid-rem-deny")

    async def scenario() -> tuple[PermissionDecision, PermissionDecision]:
        task = asyncio.ensure_future(gate.check(req))
        await _await_frame(writer)
        # Deny + remember -> _translate produces a PermissionUpdate(decision="deny")
        # which the gate persists into the engine.
        sink.deliver(
            ControlResponse(
                request_id="rid-rem-deny",
                response={"decision": "deny", "remember": True, "matcher": "*"},
            )
        )
        first = await task
        # Second identical check must short-circuit in the rules engine: no new
        # frame is emitted.
        second = await gate.check(
            _req("Bash", {"cmd": "rm y"}, request_id="rid-rem-deny-2")
        )
        return first, second

    first, second = asyncio.run(scenario())
    assert first.kind == "deny"
    assert first.updates and first.updates[0].decision == "deny"
    assert first.updates[0].matcher == "*"
    assert second.kind == "deny"
    # Only ONE control frame across both checks (second short-circuited via the
    # remembered deny rule).
    assert len(_ctrl_frames(writer)) == 1


# ---------------------------------------------------------------------------
# _race: all sinks error -> safe deny (no winner, store cancelled)
# ---------------------------------------------------------------------------
class RaisingSink:
    """A PromptSink whose ``ask`` always raises (cannot claim the resolution)."""

    def __init__(self) -> None:
        self.calls: list[ControlRequest] = []

    async def ask(self, req: ControlRequest) -> PermissionDecision:
        self.calls.append(req)
        raise RuntimeError("sink boom")


def test_race_all_sinks_error_is_safe_deny() -> None:
    first = RaisingSink()
    second = RaisingSink()
    gate = RulesPermissionGate(sinks=[first, second])
    req = _req("Bash", {"cmd": "ls"})

    decision = asyncio.run(gate._race(req))
    assert decision.kind == "deny"
    assert decision.interrupt is False
    # Both sinks were exercised (neither could claim).
    assert len(first.calls) == 1
    assert len(second.calls) == 1
    # No-winner teardown recorded a cancel with the no_sink_decision reason.
    terminal = gate.store.terminal_requests
    assert terminal, "expected a terminal store record for the cancelled request"
    cancelled = [r for r in terminal if r.state == "cancelled"]
    assert cancelled, "expected the request to be cancelled (not resolved)"
    assert any(r.cancel_reason == "no_sink_decision" for r in cancelled)


def test_check_ask_all_sinks_error_is_safe_deny() -> None:
    # The same fail-safe via the public check() path (verdict == "ask").
    gate = RulesPermissionGate(sinks=[RaisingSink(), RaisingSink()])
    decision = asyncio.run(gate.check(_req("Bash", {"cmd": "ls"})))
    assert decision.kind == "deny"


# ---------------------------------------------------------------------------
# _translate: malformed control_response -> fail-safe deny
# ---------------------------------------------------------------------------
def test_decision_empty_response_body_is_safe_deny() -> None:
    decision = _ask_with_response({})
    assert decision.kind == "deny"
    assert decision.updates == []
    assert decision.feedback is None
    assert decision.interrupt is False


def test_decision_missing_decision_key_is_safe_deny() -> None:
    decision = _ask_with_response({"feedback": "no verdict here"})
    assert decision.kind == "deny"
    # Non-allow body is treated as a deny; feedback is still carried through.
    assert decision.feedback == "no verdict here"


# ---------------------------------------------------------------------------
# HeadlessSink dedup: bounded _resolved set evicts the oldest id at cap
# ---------------------------------------------------------------------------
def test_dedup_resolved_set_evicts_oldest_at_cap(monkeypatch) -> None:
    writer = FakeWriter()
    sink = HeadlessSink(writer)
    # Shrink the cap so eviction is observable and deterministic.
    monkeypatch.setattr(HeadlessSink, "_DEDUP_CAP", 2)

    async def resolve(request_id: str) -> None:
        req = _req("Bash", {"cmd": "ls"}, request_id=request_id)
        task = asyncio.ensure_future(sink.ask(req))
        await writer.wrote.wait()
        writer.wrote.clear()
        sink.deliver(
            ControlResponse(request_id=request_id, response={"decision": "allow"})
        )
        await task

    async def scenario() -> None:
        await resolve("rid-a")
        await resolve("rid-b")
        # Both fit under the cap of 2.
        assert "rid-a" in sink._resolved
        assert "rid-b" in sink._resolved
        # Third resolution exceeds the cap -> oldest ("rid-a") is evicted.
        await resolve("rid-c")
        assert "rid-a" not in sink._resolved
        assert "rid-b" in sink._resolved
        assert "rid-c" in sink._resolved
        assert len(sink._resolved) == 2

    asyncio.run(scenario())


def test_run_headless_accepts_gate_smoke() -> None:
    from magi_agent.cli.headless import StubEngineDriver, run_headless

    prev = os.environ.get("MAGI_CLI_ENABLED")
    os.environ["MAGI_CLI_ENABLED"] = "1"
    try:
        gate = RulesPermissionGate()
        code = asyncio.run(
            run_headless(
                "hello",
                output="text",
                gate=gate,
                driver=StubEngineDriver(),
            )
        )
    finally:
        if prev is None:
            os.environ.pop("MAGI_CLI_ENABLED", None)
        else:
            os.environ["MAGI_CLI_ENABLED"] = prev
    assert isinstance(code, int)
    assert code == 0
