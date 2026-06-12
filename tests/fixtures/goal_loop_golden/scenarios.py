"""Goal-loop decision-matrix scenario driver (Pack C2 oracle).

One trace entry per ``decide_loop_continuation`` branch (module-docstring state
machine §1-7 of ``magi_agent/harness/goal_loop_control.py``). Fakes follow
``tests/test_goal_loop_control_b3.py``. Evidence records carry a wall-clock
``observedAt``, so the trace records ONLY the deterministic result scalars +
a continuation-prompt digest (machine-independent, byte-stable).

Env hermeticity: ``shadow=False`` is passed explicitly (bypasses the
``MAGI_GOAL_LOOP_JUDGE_SHADOW`` env read) and ``MAGI_GOAL_LOOP_EVIDENCE_GATE``
is pinned OFF for the whole run except the one ``evidence_unmet`` entry that
needs it ON — so an ambient developer environment cannot skew the trace
(mirrors tests/fixtures/gate5b_golden/scenarios.py ``_pin_env``).
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

from magi_agent.harness.goal_judge import DEFAULT_JUDGE_PARSE_FAILURE_BUDGET, JudgeVerdict
from magi_agent.harness.goal_loop_control import (
    EVIDENCE_GATE_ENV_VAR,
    EvidenceGateVerdict,
    LoopControlInput,
    decide_loop_continuation,
)
from magi_agent.harness.goal_state import GoalState, InMemoryGoalStateStore


class _Judge:
    """GoalJudge fake: returns a JudgeVerdict whose ``raw`` drives
    ``parse_verdict`` (JSON-first contract from harness/goal_judge.py).
    ``run_judge`` re-parses ``raw`` — the constructed ``satisfied`` field is
    never consulted."""

    def __init__(self, raw: str) -> None:
        self._raw = raw

    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=False, raw=self._raw)


class _RaisingJudge:
    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        raise RuntimeError("judge exploded")


class _Probe:
    def __init__(self, capped: bool) -> None:
        self._capped = capped

    def is_capped(self) -> bool:
        return self._capped


class _FailingGate:
    def check(
        self, goal: str, transcript_excerpt: str, goal_state: GoalState
    ) -> EvidenceGateVerdict:
        return EvidenceGateVerdict(passed=False, reason="evidence_missing")


def _store(
    max_turns: int = 8, *, status: str = "active", turns_used: int = 0
) -> InMemoryGoalStateStore:
    store = InMemoryGoalStateStore()
    state = store.set_goal("s1", "ship the feature", max_turns=max_turns)
    if status != "active" or turns_used:
        store.upsert(
            state.model_copy(update={"status": status, "turns_used": turns_used})
        )
    return store


def _decide(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        store=_store(),
        judge=_Judge('{"satisfied": false}'),
        sessionId="s1",
        transcriptExcerpt="did a step",
        spendProbe=_Probe(False),
        enabled=True,
        shadow=False,  # explicit: acted decisions, no env dependence
    )
    base.update(overrides)
    result = decide_loop_continuation(LoopControlInput.model_validate(base))
    prompt = result.continuation_prompt or ""
    return {
        "decision": result.decision,
        "reason": result.reason,
        "observeOnly": result.observe_only,
        "turnsUsed": result.goal_state_after.turns_used,
        "statusAfter": result.goal_state_after.status,
        "failuresAfter": result.consecutive_parse_failures_after,
        "continuationDigest": (
            "sha256:" + hashlib.sha256(prompt.encode()).hexdigest()[:16]
            if prompt
            else None
        ),
    }


def run_decision_matrix_scenario() -> list[dict[str, Any]]:
    previous = os.environ.get(EVIDENCE_GATE_ENV_VAR)
    os.environ.pop(EVIDENCE_GATE_ENV_VAR, None)  # pin OFF for the base matrix
    try:
        trace: list[dict[str, Any]] = []

        def add(name: str, **overrides: Any) -> None:
            entry: dict[str, Any] = {"scenario": name}
            entry.update(_decide(**overrides))
            trace.append(entry)

        add("disabled", enabled=False)
        add("spend_capped", spendProbe=_Probe(True))
        add("terminal_cleared", store=_store(status="cleared"))
        add("preempted", userMessagePending=True)
        add("satisfied_gate_off", judge=_Judge('{"satisfied": true}'))
        add("not_satisfied_continue")
        add("exhausted_on_advance", store=_store(max_turns=1))
        add("parse_failure_fail_open", judge=_Judge("garbage with no marker"))
        add("judge_raised_fail_open", judge=_RaisingJudge())
        # judge_budget: consecutiveParseFailures at the budget edge — trigger
        # copied from tests/test_goal_loop_control_b3.py (budget - 1 prior
        # failures going in; this call is the Nth).
        add(
            "judge_budget",
            judge=_Judge("garbage"),
            consecutiveParseFailures=DEFAULT_JUDGE_PARSE_FAILURE_BUDGET - 1,
        )

        # evidence_unmet needs the env gate ON + a failing gate (B4 branch).
        os.environ[EVIDENCE_GATE_ENV_VAR] = "1"
        try:
            add(
                "evidence_unmet",
                judge=_Judge('{"satisfied": true}'),
                evidenceGate=_FailingGate(),
            )
        finally:
            os.environ.pop(EVIDENCE_GATE_ENV_VAR, None)
        return trace
    finally:
        if previous is None:
            os.environ.pop(EVIDENCE_GATE_ENV_VAR, None)
        else:
            os.environ[EVIDENCE_GATE_ENV_VAR] = previous
