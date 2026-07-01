"""WS5 PR5b: bounded second attempt + honest blocked-notice escalation.

These tests reuse the exact FakeRunner harness from
``test_engine_empty_response_recovery.py`` (no real ADK import). PR5b adds an
escalation flag (``MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED``) that, when ON,
does a bounded second corrective re-invocation whose final message is the
blocked-or-final prompt, and (if every attempt stays empty) emits one
``empty_response_blocked`` status event plus a SYNTHETIC ``text_delta``
RuntimeEvent carrying ``build_blocked_notice()`` so the streamed answer-body is
non-empty (the web fallback banner is suppressed) before ``Terminal.completed``.

Contract anchor: ``EngineResult`` has NO ``final_text`` field. Every assertion
about the blocked notice keys off the STREAMED ``text_delta`` RuntimeEvent (the
same event kind the model would have produced), never a terminal field.
"""

from __future__ import annotations

import pytest

from magi_agent.cli.contracts import Terminal
from magi_agent.cli.engine import (
    MagiEngineDriver,
    build_empty_response_recovery_config,
)
from magi_agent.runtime.empty_response_recovery import (
    EmptyResponseRecoveryConfig,
    build_blocked_notice,
    build_blocked_or_final_message,
    build_empty_response_message,
)
from magi_agent.runtime.events import RuntimeEvent

# Reuse the verified harness rather than re-implementing it.
from tests.cli.test_engine_empty_response_recovery import (
    FakeRunner,
    _patch_lazy_deps,
    _run_drive,
    _status_events,
    _TEXT_EVENT,
    _TOOL_EVENTS,
)

# Master ON + escalation ON, default max becomes 2 (mirrors the env resolution).
_ESC_CFG = EmptyResponseRecoveryConfig(
    enabled=True, max_recoveries=2, escalate=True
)
# PR5a baseline: master ON, escalation OFF (max 1).
_PR5A_CFG = EmptyResponseRecoveryConfig(enabled=True, max_recoveries=1)


def _text_deltas(items: list[object]) -> list[RuntimeEvent]:
    return [
        i
        for i in items
        if isinstance(i, RuntimeEvent)
        and isinstance(i.payload, dict)
        and i.payload.get("type") == "text_delta"
    ]


def _terminal(items: list[object]) -> RuntimeEvent:
    return items[-1]


# ---------------------------------------------------------------------------
# Escalation OFF -> identical to PR5a recovery (no second attempt, no notice)
# ---------------------------------------------------------------------------


class TestEscalationOffParity:
    def test_escalation_off_is_pr5a_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Make sure no process-wide ON env leaks in: this test builds its config
        # directly, but delete the escalation env to be belt-and-suspenders.
        monkeypatch.delenv(
            "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED", raising=False
        )
        runner = FakeRunner(
            events_per_call=[list(_TOOL_EVENTS), list(_TOOL_EVENTS), [dict(_TEXT_EVENT)]]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_PR5A_CFG)
        items = _run_drive(driver)

        # PR5a: initial + ONE recovery; the third configured call never happens.
        assert len(runner.calls) == 2
        assert runner.calls[1].new_message_text == build_empty_response_message()
        assert len(_status_events(items, "empty_response_recovery")) == 1
        assert _status_events(items, "empty_response_blocked") == []
        # No synthetic blocked notice was streamed.
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )
        assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# Escalation ON: two empty attempts then text -> blocked-or-final on the final
# recovery; no blocked notice (the model recovered).
# ---------------------------------------------------------------------------


class TestEscalationTwoAttemptsThenText:
    def test_escalation_two_attempts_then_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[
                list(_TOOL_EVENTS),
                list(_TOOL_EVENTS),
                [dict(_TEXT_EVENT)],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)
        items = _run_drive(driver)

        assert len(runner.calls) == 3
        # First recovery uses the plain message, the FINAL recovery uses
        # the blocked-or-final message (post-increment equality).
        assert runner.calls[1].new_message_text == build_empty_response_message()
        assert runner.calls[2].new_message_text == build_blocked_or_final_message()

        recovery = _status_events(items, "empty_response_recovery")
        assert len(recovery) == 2
        assert all(s.payload["max"] == 2 for s in recovery)

        # The model's own text reached the consumer; no blocked notice.
        assert any(
            d.payload.get("delta") == "the final answer" for d in _text_deltas(items)
        )
        assert _status_events(items, "empty_response_blocked") == []
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )
        assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# Escalation ON: every attempt empty -> blocked notice (synthetic text_delta)
# ---------------------------------------------------------------------------


class TestEscalationExhaustedEmitsBlockedNotice:
    def test_escalation_exhausted_emits_blocked_notice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(
            events_per_call=[
                list(_TOOL_EVENTS),
                list(_TOOL_EVENTS),
                list(_TOOL_EVENTS),
                [dict(_TEXT_EVENT)],  # must NEVER be reached (bounded at 2)
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)
        items = _run_drive(driver)

        # initial + 2 recoveries = 3 model calls; the 4th never happens.
        assert len(runner.calls) == 3
        assert len(_status_events(items, "empty_response_recovery")) == 2

        blocked = _status_events(items, "empty_response_blocked")
        assert len(blocked) == 1
        assert blocked[0].payload["reason"] == "exhausted_empty"
        assert blocked[0].payload["attempts"] == 3
        # Collision-safety: the STATUS event carries no top-level text body.
        assert "text" not in blocked[0].payload
        assert "content" not in blocked[0].payload
        assert "delta" not in blocked[0].payload

        # Exactly one synthetic text_delta carrying the deterministic notice,
        # and it precedes the terminal EngineResult.
        notice_deltas = [
            d for d in _text_deltas(items) if d.payload.get("delta") == build_blocked_notice()
        ]
        assert len(notice_deltas) == 1
        notice_idx = items.index(notice_deltas[0])
        terminal_idx = len(items) - 1
        assert notice_idx < terminal_idx
        # The synthetic event is a token-kind event (what the web reducer reads).
        assert notice_deltas[0].type == "token"
        assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# Bounded: a never-talking model cannot exceed 2 recoveries.
# ---------------------------------------------------------------------------


class TestEscalationBounded:
    def test_escalation_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Five empty attempts configured; the engine must stop at 3 calls.
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)] * 5)
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)
        items = _run_drive(driver)

        assert len(runner.calls) <= 3
        assert _terminal(items).terminal == Terminal.completed


# ---------------------------------------------------------------------------
# Final-attempt message selection: only the FINAL recovery is blocked-or-final.
# ---------------------------------------------------------------------------


class TestEscalationLastAttemptMessage:
    def test_escalation_last_attempt_uses_blocked_or_final_message(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)] * 5)
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)
        _run_drive(driver)

        assert runner.calls[1].new_message_text == build_empty_response_message()
        assert runner.calls[2].new_message_text == build_blocked_or_final_message()


# ---------------------------------------------------------------------------
# Grace family stays single-shot under escalation (E7).
# ---------------------------------------------------------------------------


class TestEscalationGraceNotDouble:
    def test_escalation_grace_not_double(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # max_event_count=2: attempt 1's two tool events exhaust the budget with
        # no text -> exactly one grace, no recovery, no blocked notice.
        runner = FakeRunner(
            events_per_call=[
                list(_TOOL_EVENTS),
                [
                    {"type": "tool_start", "id": f"g{i}", "name": "bash"}
                    for i in range(70)
                ],
                [dict(_TEXT_EVENT)],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(
            runner=runner,
            max_event_count=2,
            empty_response_recovery=_ESC_CFG,
        )
        items = _run_drive(driver)

        assert len(runner.calls) == 2
        assert len(_status_events(items, "empty_response_grace")) == 1
        assert _status_events(items, "empty_response_recovery") == []
        assert _status_events(items, "empty_response_blocked") == []
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )


# ---------------------------------------------------------------------------
# Cancel mid-recovery -> aborted, no blocked notice (E8).
# ---------------------------------------------------------------------------


class TestEscalationCancelMidRecovery:
    def test_escalation_cancel_mid_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import asyncio

        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)] * 5)
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)

        async def _collect() -> list[object]:
            cancel = asyncio.Event()
            cancel.set()  # cancel before the first attempt is consumed
            out: list[object] = []
            async for item in driver.run_turn_stream(
                runtime=None,
                turn_input={
                    "prompt": "do the thing",
                    "session_id": "s",
                    "turn_id": "t",
                },
                cancel=cancel,
            ):
                out.append(item)
            return out

        items = asyncio.run(_collect())
        assert _terminal(items).terminal == Terminal.aborted
        assert _status_events(items, "empty_response_blocked") == []
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )


# ---------------------------------------------------------------------------
# Exception mid-recovery -> error terminal, no blocked notice (E9).
# ---------------------------------------------------------------------------


class _RaisingStream:
    """A fake ADK stream that raises during iteration (mirrors how the real
    runner surfaces an error: not at dispatch, but while consuming events)."""

    def __aiter__(self) -> "_RaisingStream":
        return self

    async def __anext__(self) -> object:
        raise RuntimeError("boom")

    async def aclose(self) -> None:
        pass


class _RaisingRunner(FakeRunner):
    def __init__(self, *, raise_on_call: int) -> None:
        super().__init__(events_per_call=[list(_TOOL_EVENTS)] * 5)
        self._raise_on_call = raise_on_call

    def run_async(self, **kwargs: object):  # type: ignore[override]
        raise_now = len(self.calls) == self._raise_on_call
        stream = super().run_async(**kwargs)  # type: ignore[arg-type]
        if raise_now:
            return _RaisingStream()
        return stream


class TestEscalationErrorMidRecovery:
    def test_escalation_error_mid_recovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = _RaisingRunner(raise_on_call=1)  # raise on the second call
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)
        items = _run_drive(driver)

        assert _terminal(items).terminal == Terminal.error
        assert _status_events(items, "empty_response_blocked") == []
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )


# ---------------------------------------------------------------------------
# Operator explicit max=3 + escalation ON -> blocked-or-final on the 3rd (E11).
# ---------------------------------------------------------------------------


class TestEscalationExplicitMax:
    def test_escalation_explicit_max(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = EmptyResponseRecoveryConfig(
            enabled=True, max_recoveries=3, escalate=True
        )
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)] * 6)
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=cfg)
        _run_drive(driver)

        # initial + 3 recoveries = 4 calls; blocked-or-final on the 3rd recovery.
        assert len(runner.calls) == 4
        assert runner.calls[1].new_message_text == build_empty_response_message()
        assert runner.calls[2].new_message_text == build_empty_response_message()
        assert runner.calls[3].new_message_text == build_blocked_or_final_message()


# ---------------------------------------------------------------------------
# Escalation requires the master flag (E12).
# ---------------------------------------------------------------------------


class TestEscalationRequiresMaster:
    def test_escalation_requires_master(self) -> None:
        # Master OFF -> config is None regardless of escalation flag.
        cfg = build_empty_response_recovery_config(
            {"MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED": "1"}
        )
        assert cfg is None

    def test_escalation_inert_turn_is_byte_identical(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Config None (master OFF) -> single invocation, no events.
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)])
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner)  # config defaults to None
        items = _run_drive(driver)

        assert len(runner.calls) == 1
        assert _status_events(items, "empty_response_recovery") == []
        assert _status_events(items, "empty_response_blocked") == []


# ---------------------------------------------------------------------------
# build_empty_response_recovery_config threads escalate (findings 8 + T-config)
# ---------------------------------------------------------------------------


class TestEscalationGatePreempted:
    """E15/E16 (criticals 2/3/16): a finalizer gate that blocks the empty
    answer pre-empts the WS5 blocked notice; the turn surfaces Terminal.error
    and NO blocked notice / synthetic text_delta is emitted (gates win)."""

    def test_answer_quality_gate_preempts_blocked_notice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)] * 5)
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)

        async def _block(**_kwargs: object) -> str:
            return "non_answer"

        monkeypatch.setattr(driver, "_answer_quality_llm_block", _block)
        items = _run_drive(driver)

        assert _status_events(items, "custom_llm_criterion_blocked")
        assert _terminal(items).terminal == Terminal.error
        assert _status_events(items, "empty_response_blocked") == []
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )

    def test_pre_final_gate_preempts_blocked_notice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)] * 5)
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)

        def _gate(**_kwargs: object) -> dict[str, object]:
            return {"type": "pre_final_evidence_gate", "decision": "block"}

        monkeypatch.setattr(driver, "_pre_final_gate_payload", _gate)
        items = _run_drive(driver)

        assert _terminal(items).terminal == Terminal.error
        assert _terminal(items).error == "pre_final_evidence_gate_blocked"
        assert _status_events(items, "empty_response_blocked") == []
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )


class TestEscalationPostRepairEvaluation:
    """E17 (findings 6/11/16): escalated_blank is evaluated at the terminal on
    the CURRENT emitted_text plus the monotonic net_user_text_streamed flag, so
    a turn that ever streamed net user text and then cleared it is NOT
    mis-classified as blank (no blocked notice)."""

    def test_streamed_then_cleared_text_gets_no_blocked_notice(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # attempt 1: tools, no text -> recovery. attempt 2: streams text, then a
        # response_clear blanks emitted_text. net_user_text_streamed stays True.
        runner = FakeRunner(
            events_per_call=[
                list(_TOOL_EVENTS),
                [dict(_TEXT_EVENT), {"type": "response_clear"}],
            ]
        )
        _patch_lazy_deps(monkeypatch, runner)
        driver = MagiEngineDriver(runner=runner, empty_response_recovery=_ESC_CFG)
        items = _run_drive(driver)

        # Text was streamed before the clear, so no blocked notice fires even
        # though emitted_text is "" at the terminal.
        assert _status_events(items, "empty_response_blocked") == []
        assert all(
            d.payload.get("delta") != build_blocked_notice()
            for d in _text_deltas(items)
        )
        assert _terminal(items).terminal == Terminal.completed


class TestEscalationGoalLoopInteraction:
    """E18 (finding 7): escalation ON AND a goal-loop policy present. The
    recovery cap is not bypassed by a goal-loop continuation, and the blocked
    notice fires only on the final empty terminal, never mid goal-loop."""

    def test_escalation_goal_loop_continuation_interaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from magi_agent.runtime.goal_loop_policy import (
            DEFAULT_CONTINUATION_TEMPLATE,
            GoalLoopPolicy,
        )
        from magi_agent.runtime.per_turn_goal_loop_context import (
            reset_per_turn_goal_loop_policy,
            set_per_turn_goal_loop_policy,
        )

        # Every model attempt is tools-but-empty. Recovery (precedence before the
        # goal-loop judge) consumes the empty stops up to max=2; the judge then
        # says complete on the clean break.
        runner = FakeRunner(events_per_call=[list(_TOOL_EVENTS)] * 6)
        _patch_lazy_deps(monkeypatch, runner)

        async def _caller(_: str) -> str:
            return '{"complete": true, "reason": "done"}'

        driver = MagiEngineDriver(
            runner=runner,
            user_id="cli",
            empty_response_recovery=_ESC_CFG,
            goal_loop_judge_factory=lambda _policy: _caller,  # type: ignore[arg-type]
        )
        policy = GoalLoopPolicy(
            enabled=True,
            objective="finish",
            max_turns=20,
            judge_provider=None,
            judge_model=None,
            judge_parse_failures_budget=2,
            continuation_template=DEFAULT_CONTINUATION_TEMPLATE,
        )
        token = set_per_turn_goal_loop_policy(policy)
        try:
            items = _run_drive(driver)
        finally:
            reset_per_turn_goal_loop_policy(token)

        # The recovery cap is honored: at most initial + 2 recoveries from the
        # recovery branch (a goal-loop continuation cannot push it past max=2).
        recovery = _status_events(items, "empty_response_recovery")
        assert len(recovery) <= 2
        # At most one blocked notice, and if present it is the last token before
        # the terminal EngineResult (never emitted mid goal-loop).
        blocked = _status_events(items, "empty_response_blocked")
        assert len(blocked) <= 1
        notice_deltas = [
            d
            for d in _text_deltas(items)
            if d.payload.get("delta") == build_blocked_notice()
        ]
        assert len(notice_deltas) <= 1
        if notice_deltas:
            assert items.index(notice_deltas[0]) == len(items) - 2
        assert isinstance(_terminal(items), RuntimeEvent) is False  # terminal is EngineResult


class TestBuildConfigEscalate:
    def test_config_off_is_byte_identical_dataclass(self) -> None:
        # Master ON, escalation absent -> config exactly equals today's.
        cfg = build_empty_response_recovery_config(
            {"MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1"}
        )
        assert cfg == EmptyResponseRecoveryConfig(
            enabled=True,
            max_recoveries=1,
            grace_event_allowance=64,
            escalate=False,
        )

    def test_config_escalation_on_sets_escalate_and_max_two(self) -> None:
        cfg = build_empty_response_recovery_config(
            {
                "MAGI_EMPTY_RESPONSE_RECOVERY_ENABLED": "1",
                "MAGI_EMPTY_RESPONSE_ESCALATION_ENABLED": "1",
            }
        )
        assert cfg is not None
        assert cfg.escalate is True
        assert cfg.max_recoveries == 2
