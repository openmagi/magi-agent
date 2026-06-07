"""B3 — continuation loop control + after-turn hook tests.

TDD protocol: RED → GREEN → REFACTOR.

The loop-control state machine (priority order):
  1. spend cap hit            → stop  reason="spend_capped"   (cost safety first)
  2. goal status terminal     → stop  reason matches status
  3. new user message pending → stop  reason="preempted"
  4. judge satisfied          → status=satisfied, stop reason="satisfied"
  5. judge not satisfied:
       advance would exhaust  → status=exhausted, stop reason="exhausted"
       else                   → advance, continue with CONTINUATION_PROMPT
  6. judge parse-failure      → fail-open continue, but B2 budget → stop "judge_budget"

Gate MAGI_GOAL_LOOP_ENABLED (default OFF): decision is a no-op stop "disabled".
Shadow (B2 acted=False): decision recorded but observe_only=True.

Prefix-cache invariant: the continuation prompt is a USER-role message; the
decision NEVER carries any system-prompt or toolset mutation.
"""
from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.harness.goal_judge import DEFAULT_JUDGE_PARSE_FAILURE_BUDGET, JudgeVerdict
from magi_agent.harness.goal_state import GoalState, InMemoryGoalStateStore
from magi_agent.harness.goal_loop_control import (
    CONTINUATION_PROMPT_TEMPLATE,
    GOAL_LOOP_ENABLED_ENV_VAR,
    LoopControlInput,
    LoopControlResult,
    build_after_turn_goal_loop_hook,
    build_continuation_prompt,
    decide_loop_continuation,
)
from magi_agent.hooks.manifest import HookManifest, HookPoint
from magi_agent.hooks.result import HookResult


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _AlwaysSatisfied:
    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=True, raw="SATISFIED")


class _AlwaysNotSatisfied:
    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")


class _AlwaysUnparseable:
    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=False, raw="<<no signal>>")


class _RaisingJudge:
    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        raise RuntimeError("model boom")


class _NeverCapped:
    def is_capped(self) -> bool:
        return False


class _AlwaysCapped:
    def is_capped(self) -> bool:
        return True


def _store_with_goal(
    *, session_id: str = "s1", goal: str = "finish the task", turns_used: int = 0, max_turns: int = 20
) -> InMemoryGoalStateStore:
    store = InMemoryGoalStateStore()
    store.set_goal(session_id, goal, max_turns=max_turns)
    if turns_used:
        for _ in range(turns_used):
            store.advance(session_id)
    return store


def _input(
    *,
    store: InMemoryGoalStateStore,
    judge: object = None,
    session_id: str = "s1",
    transcript: str = "Agent: working.",
    consecutive_parse_failures: int = 0,
    user_message_pending: bool = False,
    spend_probe: object | None = None,
    enabled: bool = True,
    shadow: bool = False,
) -> LoopControlInput:
    return LoopControlInput(
        store=store,
        judge=judge if judge is not None else _AlwaysNotSatisfied(),
        session_id=session_id,
        transcript_excerpt=transcript,
        consecutive_parse_failures=consecutive_parse_failures,
        user_message_pending=user_message_pending,
        spend_probe=spend_probe if spend_probe is not None else _NeverCapped(),
        enabled=enabled,
        shadow=shadow,
    )


# ---------------------------------------------------------------------------
# Gate OFF — no-op
# ---------------------------------------------------------------------------


class TestGateOff:
    def test_disabled_returns_stop_no_judge_call(self) -> None:
        class _Boom:
            def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
                raise AssertionError("judge must NOT be called when gate is off")

        store = _store_with_goal()
        result = decide_loop_continuation(_input(store=store, judge=_Boom(), enabled=False))
        assert result.decision == "stop"
        assert result.reason == "disabled"
        assert result.continuation_prompt is None

    def test_disabled_does_not_advance_goal(self) -> None:
        store = _store_with_goal()
        decide_loop_continuation(_input(store=store, enabled=False))
        assert store.get_goal("s1").turns_used == 0


# ---------------------------------------------------------------------------
# Spend cap — highest priority
# ---------------------------------------------------------------------------


class TestSpendCap:
    def test_spend_capped_stops_regardless_of_goal(self) -> None:
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysNotSatisfied(), spend_probe=_AlwaysCapped())
        )
        assert result.decision == "stop"
        assert result.reason == "spend_capped"

    def test_spend_cap_takes_precedence_over_satisfied(self) -> None:
        # Even if the judge would say satisfied, spend cap wins (and short-circuits).
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), spend_probe=_AlwaysCapped())
        )
        assert result.reason == "spend_capped"
        # short-circuit: goal status untouched
        assert store.get_goal("s1").status == "active"


# ---------------------------------------------------------------------------
# Terminal goal status
# ---------------------------------------------------------------------------


class TestTerminalStatus:
    @pytest.mark.parametrize("status", ["satisfied", "exhausted", "preempted", "cleared"])
    def test_terminal_status_stops(self, status: str) -> None:
        store = InMemoryGoalStateStore()
        store.set_goal("s1", "g")
        store._states["s1"] = store._states["s1"].model_copy(update={"status": status})
        result = decide_loop_continuation(_input(store=store))
        assert result.decision == "stop"
        assert result.reason == status


# ---------------------------------------------------------------------------
# Preemption
# ---------------------------------------------------------------------------


class TestPreemption:
    def test_user_message_pending_stops_preempted(self) -> None:
        store = _store_with_goal()
        result = decide_loop_continuation(_input(store=store, user_message_pending=True))
        assert result.decision == "stop"
        assert result.reason == "preempted"

    def test_preemption_does_not_call_judge(self) -> None:
        class _Boom:
            def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
                raise AssertionError("judge must not run on preemption")

        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_Boom(), user_message_pending=True)
        )
        assert result.reason == "preempted"


# ---------------------------------------------------------------------------
# Satisfied
# ---------------------------------------------------------------------------


class TestSatisfied:
    def test_satisfied_sets_status_and_stops(self) -> None:
        store = _store_with_goal()
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysSatisfied()))
        assert result.decision == "stop"
        assert result.reason == "satisfied"
        assert store.get_goal("s1").status == "satisfied"
        assert result.goal_state_after.status == "satisfied"

    def test_satisfied_resets_failure_counter(self) -> None:
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), consecutive_parse_failures=2)
        )
        assert result.consecutive_parse_failures_after == 0


# ---------------------------------------------------------------------------
# Continue (not satisfied, room left)
# ---------------------------------------------------------------------------


class TestContinue:
    def test_not_satisfied_advances_and_continues(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert result.decision == "continue"
        assert result.reason == "not_satisfied"
        assert store.get_goal("s1").turns_used == 1
        assert result.goal_state_after.turns_used == 1

    def test_continue_carries_continuation_prompt_referencing_goal(self) -> None:
        store = _store_with_goal(goal="ship the feature", max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert result.continuation_prompt is not None
        assert "ship the feature" in result.continuation_prompt

    def test_continue_resets_failure_counter(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysNotSatisfied(), consecutive_parse_failures=2)
        )
        assert result.consecutive_parse_failures_after == 0


# ---------------------------------------------------------------------------
# Exhaustion
# ---------------------------------------------------------------------------


class TestExhaustion:
    def test_advance_would_exhaust_stops(self) -> None:
        # turns_used=4, max_turns=5 → advancing reaches 5 == max → exhausted
        store = _store_with_goal(turns_used=4, max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert result.decision == "stop"
        assert result.reason == "exhausted"
        assert store.get_goal("s1").status == "exhausted"
        assert result.continuation_prompt is None

    def test_exhaustion_advances_turn_count(self) -> None:
        store = _store_with_goal(turns_used=4, max_turns=5)
        decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert store.get_goal("s1").turns_used == 5


# ---------------------------------------------------------------------------
# Judge parse-failure budget
# ---------------------------------------------------------------------------


class TestJudgeBudget:
    def test_parse_failure_fail_open_continues(self) -> None:
        store = _store_with_goal(max_turns=20)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysUnparseable(), consecutive_parse_failures=0)
        )
        assert result.decision == "continue"
        assert result.reason == "parse_failure_fail_open"
        assert result.consecutive_parse_failures_after == 1

    def test_parse_failure_advances_goal_on_continue(self) -> None:
        store = _store_with_goal(max_turns=20)
        decide_loop_continuation(
            _input(store=store, judge=_AlwaysUnparseable(), consecutive_parse_failures=0)
        )
        assert store.get_goal("s1").turns_used == 1

    def test_nth_consecutive_failure_stops_judge_budget(self) -> None:
        store = _store_with_goal(max_turns=100)
        # going in already at budget-1 prior failures; this call is the Nth.
        result = decide_loop_continuation(
            _input(
                store=store,
                judge=_AlwaysUnparseable(),
                consecutive_parse_failures=DEFAULT_JUDGE_PARSE_FAILURE_BUDGET - 1,
            )
        )
        assert result.decision == "stop"
        assert result.reason == "judge_budget"

    def test_judge_budget_stop_does_not_continue_prompt(self) -> None:
        store = _store_with_goal(max_turns=100)
        result = decide_loop_continuation(
            _input(
                store=store,
                judge=_AlwaysUnparseable(),
                consecutive_parse_failures=DEFAULT_JUDGE_PARSE_FAILURE_BUDGET - 1,
            )
        )
        assert result.continuation_prompt is None

    def test_raising_judge_treated_as_parse_failure(self) -> None:
        store = _store_with_goal(max_turns=20)
        result = decide_loop_continuation(
            _input(store=store, judge=_RaisingJudge(), consecutive_parse_failures=0)
        )
        assert result.decision == "continue"
        assert result.consecutive_parse_failures_after == 1


# ---------------------------------------------------------------------------
# Shadow — observe only
# ---------------------------------------------------------------------------


class TestShadow:
    def test_shadow_continue_marked_observe_only(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysNotSatisfied(), shadow=True)
        )
        # decision computed normally (continue) but flagged observe-only
        assert result.decision == "continue"
        assert result.observe_only is True

    def test_live_not_observe_only(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysNotSatisfied(), shadow=False)
        )
        assert result.observe_only is False

    def test_shadow_still_records_goal_advance(self) -> None:
        # The decision logic is computed (advance happens) even in shadow; the
        # DRIVER is what no-ops. This mirrors B2 run_judge recording verdict.
        store = _store_with_goal(max_turns=5)
        decide_loop_continuation(
            _input(store=store, judge=_AlwaysNotSatisfied(), shadow=True)
        )
        assert store.get_goal("s1").turns_used == 1

    def test_shadow_satisfied_mutates_store_observe_only(self) -> None:
        # shadow=True + satisfied: store IS mutated to status="satisfied"
        # while observe_only=True (decision recorded, driver no-ops).
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), shadow=True)
        )
        assert result.observe_only is True
        assert result.decision == "stop"
        assert result.reason == "satisfied"
        # Store state is mutated regardless of shadow — the driver no-ops, not
        # the state machine.
        assert store.get_goal("s1").status == "satisfied"


# ---------------------------------------------------------------------------
# Prefix-cache invariant
# ---------------------------------------------------------------------------


class TestPrefixCacheInvariant:
    def test_continuation_prompt_renders_goal(self) -> None:
        prompt = build_continuation_prompt("my goal")
        assert "my goal" in prompt
        # Renders from the template's static prefix (the part before {goal}).
        static_prefix = CONTINUATION_PROMPT_TEMPLATE.split("{goal}")[0]
        assert prompt.startswith(static_prefix)

    def test_result_has_no_system_or_toolset_mutation_fields(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        field_names = set(LoopControlResult.model_fields)
        # No field that could mutate the cached static prefix.
        assert not any("system_prompt" in f for f in field_names)
        assert not any("toolset" in f for f in field_names)
        assert not any("tool_set" in f for f in field_names)
        # Continuation is a plain user-role string only.
        assert isinstance(result.continuation_prompt, str)

    def test_continuation_role_is_user(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert result.continuation_role == "user"


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class TestEvidence:
    def test_result_carries_redacted_evidence(self) -> None:
        store = _store_with_goal(goal="secret goal text", max_turns=5)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysNotSatisfied(), transcript="raw transcript secret")
        )
        assert result.evidence is not None
        fields_str = str(dict(result.evidence.fields))
        assert "secret goal text" not in fields_str
        assert "raw transcript secret" not in fields_str

    def test_evidence_type_is_custom(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert result.evidence.type.startswith("custom:")

    def test_evidence_records_decision_and_reason(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysSatisfied()))
        fields = dict(result.evidence.fields)
        assert fields.get("decision") == "stop"
        assert fields.get("reason") == "satisfied"


# ---------------------------------------------------------------------------
# Frozen result model
# ---------------------------------------------------------------------------


class TestLoopControlResultModel:
    def test_frozen(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        with pytest.raises((TypeError, ValidationError)):
            result.decision = "continue"  # type: ignore[misc]

    def test_goal_state_after_is_goal_state(self) -> None:
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert isinstance(result.goal_state_after, GoalState)


# ---------------------------------------------------------------------------
# After-turn hook
# ---------------------------------------------------------------------------


class TestAfterTurnHook:
    def test_hook_registered_at_after_turn_end(self) -> None:
        manifest, _handler = build_after_turn_goal_loop_hook(
            input_provider=lambda ctx: None,
        )
        assert isinstance(manifest, HookManifest)
        assert manifest.point is HookPoint.AFTER_TURN_END

    def test_hook_handler_returns_continue_when_no_goal(self) -> None:
        _manifest, handler = build_after_turn_goal_loop_hook(
            input_provider=lambda ctx: None,  # no active goal-loop for this session
        )
        from magi_agent.hooks.context import HookContext

        result = handler(HookContext(botId="b1", sessionId="s1", turnId="t1"))
        assert isinstance(result, HookResult)
        assert result.action == "continue"

    def test_hook_handler_records_decision_via_sink(self) -> None:
        store = _store_with_goal(max_turns=5)
        recorded: list[LoopControlResult] = []

        _manifest, handler = build_after_turn_goal_loop_hook(
            input_provider=lambda ctx: _input(store=store, judge=_AlwaysNotSatisfied()),
            decision_sink=recorded.append,
        )
        from magi_agent.hooks.context import HookContext

        handler(HookContext(botId="b1", sessionId="s1", turnId="t1"))
        assert len(recorded) == 1
        assert recorded[0].decision == "continue"

    def test_hook_handler_does_not_block(self) -> None:
        store = _store_with_goal(max_turns=5)
        _manifest, handler = build_after_turn_goal_loop_hook(
            input_provider=lambda ctx: _input(store=store, judge=_AlwaysSatisfied()),
        )
        from magi_agent.hooks.context import HookContext

        result = handler(HookContext(botId="b1", sessionId="s1", turnId="t1"))
        # The hook never blocks the turn — it only records a continuation decision.
        assert result.action == "continue"

    def test_hook_manifest_uses_env_gate_var(self) -> None:
        # The env gate constant is exported for the driver/wiring to consult.
        assert GOAL_LOOP_ENABLED_ENV_VAR == "MAGI_GOAL_LOOP_ENABLED"


# ---------------------------------------------------------------------------
# Import boundary — no ADK at top level
# ---------------------------------------------------------------------------


class TestImportBoundary:
    def test_no_adk_top_level_import(self) -> None:
        code = (
            "import sys; "
            "import magi_agent.harness.goal_loop_control; "
            "mods = list(sys.modules.keys()); "
            "bad = [m for m in mods if 'google.adk' in m or 'adk_bridge' in m]; "
            "print(bad)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "[]", (
            f"ADK leaked into top-level imports: {result.stdout.strip()}"
        )


# ---------------------------------------------------------------------------
# Multi-turn termination integration (LOW 2)
# ---------------------------------------------------------------------------


class TestMultiTurnTermination:
    """Integration test: consecutive calls on the SAME store prove that the
    loop terminates as a sequence, not just as two separate units."""

    def test_loop_continues_until_max_turns_then_exhausts(self) -> None:
        """With max_turns=3 and a judge that never satisfies:
        - Turn 1 (turns_used=0 → 1): continue  reason=not_satisfied
        - Turn 2 (turns_used=1 → 2): continue  reason=not_satisfied
        - Turn 3 (turns_used=2 → 3): stop      reason=exhausted
        """
        max_turns = 3
        store = _store_with_goal(max_turns=max_turns)
        judge = _AlwaysNotSatisfied()

        results = []
        for _ in range(max_turns):
            result = decide_loop_continuation(
                _input(store=store, judge=judge, shadow=False)
            )
            results.append(result)

        # First (max_turns-1) decisions must be continue
        for i, r in enumerate(results[:-1]):
            assert r.decision == "continue", f"turn {i + 1} expected continue, got {r.decision}"
            assert r.reason == "not_satisfied"

        # Final decision must be stop/exhausted
        final = results[-1]
        assert final.decision == "stop"
        assert final.reason == "exhausted"
        assert final.continuation_prompt is None

        # Store reflects exhaustion
        state = store.get_goal("s1")
        assert state.status == "exhausted"
        assert state.turns_used == max_turns


# ---------------------------------------------------------------------------
# Sqlite-backed store durability (terminal status via _persist)
# ---------------------------------------------------------------------------


class TestSqliteBackedSatisfied:
    def test_satisfied_persists_to_sqlite(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from magi_agent.harness.goal_state import SqliteGoalStateStore

        db = tmp_path / "goals.db"
        store = SqliteGoalStateStore(db)
        # Seed a sessions row so the goal_states FK constraint is satisfied.
        conn = store._get_conn()
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, app_name, user_id) "
            "VALUES ('s1', 'test-app', 'test-user')"
        )
        conn.commit()
        store.set_goal("s1", "finish", max_turns=5)
        decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied())
        )
        # A fresh store instance must read back the satisfied status (durable).
        store.close()
        fresh = SqliteGoalStateStore(db)
        assert fresh.get_goal("s1").status == "satisfied"
        fresh.close()
