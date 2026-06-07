"""B4 — Evidence-gated loop termination tests.

TDD protocol: RED → GREEN → REFACTOR.

B4 adds an OPTIONAL ``evidence_gate`` seam to ``LoopControlInput``.  When the
gate env var ``MAGI_GOAL_LOOP_EVIDENCE_GATE`` is OFF (default) OR no gate is
injected, behaviour is byte-identical to B3 (zero regression).  When ON and a
gate is injected:

  judge-satisfied + evidence-pass  → stop   reason="satisfied"    (strong stop)
  judge-satisfied + evidence-fail  → continue reason="evidence_unmet" (advance)
    └─ if advancing would exhaust  → stop   reason="exhausted"
  judge-not-satisfied              → unchanged B3 path
  gate-off (env=false or no gate)  → unchanged B3 path

Evidence gate verdict is recorded as a redacted EvidenceRecord alongside the
existing loop-decision record.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from magi_agent.harness.goal_judge import JudgeVerdict
from magi_agent.harness.goal_state import InMemoryGoalStateStore
from magi_agent.harness.goal_loop_control import (
    EVIDENCE_GATE_ENV_VAR,
    EvidenceGate,
    EvidenceGateVerdict,
    LoopControlInput,
    decide_loop_continuation,
)


# ---------------------------------------------------------------------------
# Fakes (all inline, no model client)
# ---------------------------------------------------------------------------


class _AlwaysSatisfied:
    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=True, raw="SATISFIED")


class _AlwaysNotSatisfied:
    def judge(self, goal: str, transcript_excerpt: str) -> JudgeVerdict:
        return JudgeVerdict(satisfied=False, raw="NOT_SATISFIED")


class _NeverCapped:
    def is_capped(self) -> bool:
        return False


class _PassGate:
    """Fake EvidenceGate that always passes."""

    def check(
        self,
        goal: str,
        transcript_excerpt: str,
        goal_state: object,
    ) -> EvidenceGateVerdict:
        return EvidenceGateVerdict(passed=True, reason="evidence_confirmed")


class _FailGate:
    """Fake EvidenceGate that always fails."""

    def check(
        self,
        goal: str,
        transcript_excerpt: str,
        goal_state: object,
    ) -> EvidenceGateVerdict:
        return EvidenceGateVerdict(passed=False, reason="evidence_missing")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store_with_goal(
    *,
    session_id: str = "s1",
    goal: str = "finish the task",
    turns_used: int = 0,
    max_turns: int = 20,
) -> InMemoryGoalStateStore:
    store = InMemoryGoalStateStore()
    store.set_goal(session_id, goal, max_turns=max_turns)
    for _ in range(turns_used):
        store.advance(session_id)
    return store


def _input(
    *,
    store: InMemoryGoalStateStore,
    judge: object = None,
    session_id: str = "s1",
    transcript: str = "Agent: working.",
    enabled: bool = True,
    shadow: bool = False,
    evidence_gate: object | None = None,
) -> LoopControlInput:
    return LoopControlInput(
        store=store,
        judge=judge if judge is not None else _AlwaysNotSatisfied(),
        sessionId=session_id,
        transcriptExcerpt=transcript,
        spendProbe=_NeverCapped(),
        enabled=enabled,
        shadow=shadow,
        evidence_gate=evidence_gate,
    )


# ---------------------------------------------------------------------------
# EvidenceGateVerdict model
# ---------------------------------------------------------------------------


class TestEvidenceGateVerdict:
    def test_passed_true_frozen(self) -> None:
        v = EvidenceGateVerdict(passed=True, reason="ok")
        assert v.passed is True
        with pytest.raises((TypeError, Exception)):
            v.passed = False  # type: ignore[misc]

    def test_passed_false(self) -> None:
        v = EvidenceGateVerdict(passed=False, reason="missing")
        assert v.passed is False

    def test_reason_stored(self) -> None:
        v = EvidenceGateVerdict(passed=True, reason="confirmed")
        assert v.reason == "confirmed"


# ---------------------------------------------------------------------------
# EvidenceGate Protocol
# ---------------------------------------------------------------------------


class TestEvidenceGateProtocol:
    def test_pass_gate_satisfies_protocol(self) -> None:
        assert isinstance(_PassGate(), EvidenceGate)

    def test_fail_gate_satisfies_protocol(self) -> None:
        assert isinstance(_FailGate(), EvidenceGate)


# ---------------------------------------------------------------------------
# Gate-off (default) == B3 exactly
# ---------------------------------------------------------------------------


class TestGateOffPreservesB3:
    """When MAGI_GOAL_LOOP_EVIDENCE_GATE is unset/false OR no gate injected,
    the satisfied path must be byte-identical to B3."""

    def test_gate_env_off_no_gate_injected_judge_satisfied_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(EVIDENCE_GATE_ENV_VAR, raising=False)
        store = _store_with_goal()
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysSatisfied()))
        assert result.decision == "stop"
        assert result.reason == "satisfied"
        assert store.get_goal("s1").status == "satisfied"

    def test_gate_env_off_with_gate_injected_judge_satisfied_stops(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """env=false + gate injected: env takes precedence → B3 path."""
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "false")
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        # env gate off → gate ignored → satisfied stop (B3 behaviour)
        assert result.decision == "stop"
        assert result.reason == "satisfied"

    def test_gate_env_on_but_no_gate_injected_falls_back_to_b3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """env=true but no gate object → degrade gracefully → B3 behaviour."""
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=None)
        )
        assert result.decision == "stop"
        assert result.reason == "satisfied"

    def test_gate_off_does_not_change_not_satisfied_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(EVIDENCE_GATE_ENV_VAR, raising=False)
        store = _store_with_goal(max_turns=5)
        result = decide_loop_continuation(_input(store=store, judge=_AlwaysNotSatisfied()))
        assert result.decision == "continue"
        assert result.reason == "not_satisfied"


# ---------------------------------------------------------------------------
# Gate ON + evidence passes → satisfied stop
# ---------------------------------------------------------------------------


class TestGateOnEvidencePass:
    def test_judge_satisfied_evidence_pass_stops_satisfied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_PassGate())
        )
        assert result.decision == "stop"
        assert result.reason == "satisfied"
        assert store.get_goal("s1").status == "satisfied"

    def test_judge_satisfied_evidence_pass_goal_state_is_satisfied(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_PassGate())
        )
        assert result.goal_state_after.status == "satisfied"

    def test_evidence_pass_result_has_no_continuation_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_PassGate())
        )
        assert result.continuation_prompt is None


# ---------------------------------------------------------------------------
# Gate ON + evidence fails → continue with evidence_unmet
# ---------------------------------------------------------------------------


class TestGateOnEvidenceFail:
    def test_judge_satisfied_evidence_fail_continues_evidence_unmet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(max_turns=10)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert result.decision == "continue"
        assert result.reason == "evidence_unmet"

    def test_evidence_unmet_does_not_set_satisfied_status(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(max_turns=10)
        decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        # MUST NOT be satisfied — evidence failed
        assert store.get_goal("s1").status == "active"

    def test_evidence_unmet_advances_turn_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(max_turns=10)
        decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert store.get_goal("s1").turns_used == 1

    def test_evidence_unmet_carry_continuation_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(goal="prove the feature works", max_turns=10)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert result.continuation_prompt is not None
        assert "prove the feature works" in result.continuation_prompt

    def test_evidence_unmet_resets_parse_failure_counter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Evidence-unmet is a successful parse (judge parsed fine, gate checked),
        so the parse-failure counter must reset to 0."""
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(max_turns=10)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert result.consecutive_parse_failures_after == 0


# ---------------------------------------------------------------------------
# Gate ON + evidence fails + would exhaust → exhausted stop
# ---------------------------------------------------------------------------


class TestGateOnEvidenceFailExhaustion:
    def test_evidence_unmet_at_boundary_exhausts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        # turns_used=4, max_turns=5 → advancing reaches 5 == max → exhausted
        store = _store_with_goal(turns_used=4, max_turns=5)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert result.decision == "stop"
        assert result.reason == "exhausted"
        assert store.get_goal("s1").status == "exhausted"

    def test_evidence_unmet_exhaustion_no_continuation_prompt(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(turns_used=4, max_turns=5)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert result.continuation_prompt is None

    def test_evidence_unmet_exhaustion_advances_turn_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(turns_used=4, max_turns=5)
        decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert store.get_goal("s1").turns_used == 5


# ---------------------------------------------------------------------------
# Evidence (redacted gate record)
# ---------------------------------------------------------------------------


class TestGateEvidence:
    def test_gate_pass_evidence_in_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(goal="secret goal text")
        result = decide_loop_continuation(
            _input(
                store=store,
                judge=_AlwaysSatisfied(),
                evidence_gate=_PassGate(),
                transcript="raw private transcript",
            )
        )
        assert result.evidence is not None

    def test_gate_pass_evidence_redacted_no_raw_goal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(goal="secret goal text")
        result = decide_loop_continuation(
            _input(
                store=store,
                judge=_AlwaysSatisfied(),
                evidence_gate=_PassGate(),
                transcript="raw private transcript",
            )
        )
        fields_str = str(dict(result.evidence.fields))
        assert "secret goal text" not in fields_str
        assert "raw private transcript" not in fields_str

    def test_gate_fail_evidence_records_gate_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(max_turns=10)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        assert result.evidence is not None
        fields = dict(result.evidence.fields)
        # The decision must reflect the evidence_unmet outcome
        assert fields.get("reason") == "evidence_unmet"

    def test_gate_pass_evidence_records_satisfied_reason(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_PassGate())
        )
        fields = dict(result.evidence.fields)
        assert fields.get("reason") == "satisfied"

    def test_gate_evidence_includes_gate_passed_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal()
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_PassGate())
        )
        fields = dict(result.evidence.fields)
        assert "gatePassed" in fields
        assert fields["gatePassed"] is True

    def test_gate_fail_evidence_includes_gate_passed_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(EVIDENCE_GATE_ENV_VAR, "true")
        store = _store_with_goal(max_turns=10)
        result = decide_loop_continuation(
            _input(store=store, judge=_AlwaysSatisfied(), evidence_gate=_FailGate())
        )
        fields = dict(result.evidence.fields)
        assert fields.get("gatePassed") is False


# ---------------------------------------------------------------------------
# LoopControlInput — evidence_gate is optional (no gate = no change to schema)
# ---------------------------------------------------------------------------


class TestLoopControlInputBackcompat:
    def test_no_evidence_gate_field_still_valid(self) -> None:
        store = _store_with_goal()
        inp = LoopControlInput(
            store=store,
            judge=_AlwaysNotSatisfied(),
            sessionId="s1",
            transcriptExcerpt="t",
            spendProbe=_NeverCapped(),
            enabled=True,
        )
        assert inp.evidence_gate is None

    def test_evidence_gate_field_accepts_gate_object(self) -> None:
        store = _store_with_goal()
        inp = LoopControlInput(
            store=store,
            judge=_AlwaysNotSatisfied(),
            sessionId="s1",
            transcriptExcerpt="t",
            spendProbe=_NeverCapped(),
            enabled=True,
            evidence_gate=_PassGate(),
        )
        assert inp.evidence_gate is not None


# ---------------------------------------------------------------------------
# env gate constant exported
# ---------------------------------------------------------------------------


class TestEnvGateConstant:
    def test_evidence_gate_env_var_exported(self) -> None:
        assert EVIDENCE_GATE_ENV_VAR == "MAGI_GOAL_LOOP_EVIDENCE_GATE"


# ---------------------------------------------------------------------------
# Import boundary — no ADK at top level (B4 module must stay import-clean)
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
