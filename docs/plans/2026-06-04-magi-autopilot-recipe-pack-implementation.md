# magi-autopilot Recipe Pack/Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 1st-party `autopilot` workflow to Magi Agent (Python ADK) — a strict
FSM (interview → consensus-plan → execute → review → adversarial-QA, with
gate-failure return-to-plan) exposed as a default-off recipe pack and harness
scaffold, ported from oh-my-codex `$autopilot`.

**Architecture:** Two layers, both following the existing Magi Agent
**metadata-only / traffic-free / default-off** discipline:
(1) a harness scaffold `harness/autopilot.py` — frozen pydantic phase/policy/transition
models plus a **pure** transition function (no I/O, no execution); (2) a recipe
manifest `openmagi.autopilot` registered in `recipes/compiler.py` plus default-off,
env-gated presets in `harness/presets.py`. Live ADK runtime attachment (driving the
FSM against real turns) is explicitly **out of scope** here and deferred to a later
flag-gated PR in the canonical OSS repo.

**Tech Stack:** Python 3.12+, pydantic v2 (frozen models, alias-based camelCase
serialization), pytest. Package: Magi Agent. Tests run with
`uv run --extra dev pytest`.

---

## Critical constraints (read before starting)

- **Canonical runtime is the OSS repo `openmagi/magi-agent`.** This plan's PR1–PR4
  are metadata-only scaffolds (no durable runtime logic). PR5 (live attachment)
  belongs in OSS first and must remain separately gated.
- **Always work in a git worktree** (project rule). Create one via
  `superpowers:using-git-worktrees` before Task 1.
- **Every new model is metadata-only:** frozen, `extra="forbid"`, `*_attached`
  fields pinned to `Literal[False]` or validated false, `enabled=False` default.
- All commands below assume the `openmagi/magi-agent` repository root unless noted.

## File Structure

| File | Responsibility | PR |
|---|---|---|
| `magi_agent/harness/autopilot.py` (create) | FSM phase/verdict enums, `AutopilotFsmPolicy`, `AutopilotPhaseTransition`, pure `evaluate_autopilot_transition`, `AutopilotAmbiguityScore` | PR1, PR4 |
| `tests/test_autopilot_fsm_contract.py` (create) | Unit + integration tests for the FSM scaffold | PR1, PR4 |
| `magi_agent/recipes/compiler.py` (modify `_first_party_packs`) | Register `openmagi.autopilot` manifest | PR2 |
| `tests/test_autopilot_recipe_pack.py` (create) | Manifest registration, default-off, dependency resolution, selector activation, opt-out | PR2 |
| `magi_agent/harness/presets.py` (modify `_BUILTIN_PRESETS`) | 5 default-off, env-gated autopilot presets | PR3 |
| `tests/test_autopilot_presets.py` (create) | Preset keys present, default-off, env-gated, verifier-gate refs | PR3 |

---

# PR1 — FSM scaffold (`harness/autopilot.py`)

All models here are pure data + a pure function. No imports of ADK runtime, no I/O.

### Task 1: Phase + verdict enums and constants

**Files:**
- Create: `magi_agent/harness/autopilot.py`
- Test: `tests/test_autopilot_fsm_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autopilot_fsm_contract.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from magi_agent.harness.autopilot import (
    AUTOPILOT_FEATURE_KEY,
    DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES,
    TERMINAL_PHASES,
    AutopilotGateVerdict,
    AutopilotPhase,
)


def test_phase_and_verdict_enum_values() -> None:
    assert [p.value for p in AutopilotPhase] == [
        "interview", "plan", "execute", "review", "qa", "complete", "blocked",
    ]
    assert [v.value for v in AutopilotGateVerdict] == ["pass", "fail", "skip"]
    assert TERMINAL_PHASES == (AutopilotPhase.COMPLETE, AutopilotPhase.BLOCKED)
    assert AUTOPILOT_FEATURE_KEY == "autopilot-fsm"
    assert DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'magi_agent.harness.autopilot'`

- [ ] **Step 3: Write minimal implementation**

```python
# magi_agent/harness/autopilot.py
from __future__ import annotations

from enum import StrEnum


AUTOPILOT_FEATURE_KEY = "autopilot-fsm"
DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES = 3


class AutopilotPhase(StrEnum):
    INTERVIEW = "interview"
    PLAN = "plan"
    EXECUTE = "execute"
    REVIEW = "review"
    QA = "qa"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class AutopilotGateVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"


TERMINAL_PHASES: tuple[AutopilotPhase, ...] = (
    AutopilotPhase.COMPLETE,
    AutopilotPhase.BLOCKED,
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add magi_agent/harness/autopilot.py tests/test_autopilot_fsm_contract.py
git commit -m "feat(magi): autopilot PR1.1 — FSM phase/verdict enums"
```

### Task 2: `AutopilotFsmPolicy` (default-off, traffic-free)

**Files:**
- Modify: `magi_agent/harness/autopilot.py`
- Test: `tests/test_autopilot_fsm_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_autopilot_fsm_contract.py
from magi_agent.harness.autopilot import (  # noqa: E402
    AutopilotFsmPolicy,
    AutopilotOptOutState,
    build_autopilot_policy,
)


def test_default_policy_is_disabled_and_traffic_free() -> None:
    dumped = build_autopilot_policy().model_dump(by_alias=True)
    assert dumped["featureKey"] == "autopilot-fsm"
    assert dumped["enabled"] is False
    assert dumped["maxReviewCycles"] == 3
    assert dumped["qaSkipAllowedForNonruntime"] is True
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["optOut"]["optedOut"] is False


def test_policy_rejects_attachment_flags() -> None:
    with pytest.raises(ValidationError, match="traffic-free"):
        AutopilotFsmPolicy(enabled=True, trafficAttached=True)


def test_opt_out_must_disable_fsm() -> None:
    with pytest.raises(ValidationError, match="opt-out"):
        AutopilotFsmPolicy(enabled=True, optOut=AutopilotOptOutState(optedOut=True))


def test_build_policy_opt_out_forces_disabled() -> None:
    policy = build_autopilot_policy(
        enabled=True, opt_out=AutopilotOptOutState(optedOut=True)
    )
    assert policy.enabled is False


def test_max_review_cycles_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        AutopilotFsmPolicy(maxReviewCycles=0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v -k policy`
Expected: FAIL with `ImportError: cannot import name 'AutopilotFsmPolicy'`

- [ ] **Step 3: Write minimal implementation**

```python
# add imports at top of magi_agent/harness/autopilot.py
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
```

```python
# append to magi_agent/harness/autopilot.py
class AutopilotOptOutState(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    opted_out: bool = Field(default=False, alias="optedOut")
    disabled_reason: str | None = Field(default=None, alias="disabledReason")


class AutopilotFsmPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    feature_key: Literal["autopilot-fsm"] = Field(
        default=AUTOPILOT_FEATURE_KEY, alias="featureKey"
    )
    enabled: bool = False
    max_review_cycles: int = Field(
        default=DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES, alias="maxReviewCycles", ge=1
    )
    qa_skip_allowed_for_nonruntime: bool = Field(
        default=True, alias="qaSkipAllowedForNonruntime"
    )
    opt_out: AutopilotOptOutState = Field(
        default_factory=AutopilotOptOutState, alias="optOut"
    )
    traffic_attached: bool = Field(default=False, alias="trafficAttached")
    execution_attached: bool = Field(default=False, alias="executionAttached")

    @model_validator(mode="after")
    def _validate_traffic_free_and_opt_out(self) -> Self:
        if self.traffic_attached or self.execution_attached:
            raise ValueError("autopilot fsm scaffold must remain traffic-free")
        if self.opt_out.opted_out and self.enabled:
            raise ValueError("autopilot opt-out must disable the fsm")
        return self


def build_autopilot_policy(
    *,
    enabled: bool = False,
    max_review_cycles: int = DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES,
    qa_skip_allowed_for_nonruntime: bool = True,
    opt_out: AutopilotOptOutState | None = None,
) -> AutopilotFsmPolicy:
    resolved_opt_out = opt_out or AutopilotOptOutState()
    if resolved_opt_out.opted_out:
        enabled = False
    return AutopilotFsmPolicy(
        enabled=enabled,
        max_review_cycles=max_review_cycles,
        qa_skip_allowed_for_nonruntime=qa_skip_allowed_for_nonruntime,
        opt_out=resolved_opt_out,
        traffic_attached=False,
        execution_attached=False,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v -k policy`
Expected: PASS (all 5)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/harness/autopilot.py tests/test_autopilot_fsm_contract.py
git commit -m "feat(magi): autopilot PR1.2 — default-off traffic-free FSM policy"
```

### Task 3: `AutopilotPhaseTransition` snapshot model

**Files:**
- Modify: `magi_agent/harness/autopilot.py`
- Test: `tests/test_autopilot_fsm_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_autopilot_fsm_contract.py
from magi_agent.harness.autopilot import AutopilotPhaseTransition  # noqa: E402


def _transition(**kw: object) -> AutopilotPhaseTransition:
    base: dict[str, object] = {
        "fromPhase": AutopilotPhase.EXECUTE,
        "toPhase": AutopilotPhase.REVIEW,
        "gate": "execution-evidence",
        "verdict": AutopilotGateVerdict.PASS,
    }
    base.update(kw)
    return AutopilotPhaseTransition(**base)


def test_transition_dumps_aliases_and_is_route_free() -> None:
    dumped = _transition().model_dump(by_alias=True)
    assert dumped["fromPhase"] == "execute"
    assert dumped["toPhase"] == "review"
    assert dumped["routeAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["terminal"] is False


def test_transition_rejects_empty_gate() -> None:
    with pytest.raises(ValidationError, match="gate"):
        _transition(gate="  ")


def test_return_to_plan_requires_reason() -> None:
    with pytest.raises(ValidationError, match="returnToPlanReason"):
        _transition(
            fromPhase=AutopilotPhase.REVIEW,
            toPhase=AutopilotPhase.PLAN,
            gate="review-clean",
            verdict=AutopilotGateVerdict.FAIL,
        )


def test_terminal_flag_must_match_target_phase() -> None:
    with pytest.raises(ValidationError, match="terminal"):
        _transition(toPhase=AutopilotPhase.COMPLETE, terminal=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v -k transition`
Expected: FAIL with `ImportError: cannot import name 'AutopilotPhaseTransition'`

- [ ] **Step 3: Write minimal implementation**

```python
# add to imports in magi_agent/harness/autopilot.py
from pydantic import field_validator
```

```python
# append to magi_agent/harness/autopilot.py
class AutopilotPhaseTransition(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    from_phase: AutopilotPhase = Field(alias="fromPhase")
    to_phase: AutopilotPhase = Field(alias="toPhase")
    gate: str
    verdict: AutopilotGateVerdict
    review_cycle: int = Field(default=0, alias="reviewCycle", ge=0)
    return_to_plan_reason: str | None = Field(default=None, alias="returnToPlanReason")
    terminal: bool = False
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")
    execution_attached: Literal[False] = Field(default=False, alias="executionAttached")

    @field_validator("gate")
    @classmethod
    def _reject_empty_gate(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("gate must be non-empty")
        return value

    @field_validator("return_to_plan_reason")
    @classmethod
    def _reject_empty_reason(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("returnToPlanReason must be non-empty when provided")
        return value

    @model_validator(mode="after")
    def _validate_transition_semantics(self) -> Self:
        returning_to_plan = self.to_phase == AutopilotPhase.PLAN and self.from_phase in (
            AutopilotPhase.REVIEW,
            AutopilotPhase.QA,
        )
        if returning_to_plan and not self.return_to_plan_reason:
            raise ValueError("return-to-plan transition requires returnToPlanReason")
        if self.terminal != (self.to_phase in TERMINAL_PHASES):
            raise ValueError("terminal flag must match terminal target phase")
        return self
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v -k transition`
Expected: PASS (4)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/harness/autopilot.py tests/test_autopilot_fsm_contract.py
git commit -m "feat(magi): autopilot PR1.3 — phase transition snapshot model"
```

### Task 4: Pure transition function `evaluate_autopilot_transition`

**Files:**
- Modify: `magi_agent/harness/autopilot.py`
- Test: `tests/test_autopilot_fsm_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_autopilot_fsm_contract.py
from magi_agent.harness.autopilot import (  # noqa: E402
    evaluate_autopilot_transition,
    gate_for_phase,
)


def _eval(current: AutopilotPhase, verdict: AutopilotGateVerdict, cycle: int = 0):
    return evaluate_autopilot_transition(
        current=current,
        verdict=verdict,
        review_cycle=cycle,
        policy=build_autopilot_policy(enabled=True),
    )


def test_gate_for_phase_maps_each_active_phase() -> None:
    assert gate_for_phase(AutopilotPhase.INTERVIEW) == "interview-ambiguity-cleared"
    assert gate_for_phase(AutopilotPhase.PLAN) == "consensus-architect-then-critic"
    assert gate_for_phase(AutopilotPhase.EXECUTE) == "execution-evidence"
    assert gate_for_phase(AutopilotPhase.REVIEW) == "review-clean"
    assert gate_for_phase(AutopilotPhase.QA) == "adversarial-qa"
    with pytest.raises(ValueError):
        gate_for_phase(AutopilotPhase.COMPLETE)


def test_happy_path_advances_each_phase() -> None:
    assert _eval(AutopilotPhase.INTERVIEW, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.PLAN
    assert _eval(AutopilotPhase.PLAN, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.EXECUTE
    assert _eval(AutopilotPhase.EXECUTE, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.REVIEW
    assert _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.PASS).to_phase == AutopilotPhase.QA
    qa_pass = _eval(AutopilotPhase.QA, AutopilotGateVerdict.PASS)
    assert qa_pass.to_phase == AutopilotPhase.COMPLETE
    assert qa_pass.terminal is True


def test_qa_skip_completes_only_when_allowed() -> None:
    skip = _eval(AutopilotPhase.QA, AutopilotGateVerdict.SKIP)
    assert skip.to_phase == AutopilotPhase.COMPLETE and skip.terminal is True
    with pytest.raises(ValueError, match="skip"):
        _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.SKIP)
    policy = build_autopilot_policy(enabled=True, qa_skip_allowed_for_nonruntime=False)
    with pytest.raises(ValueError, match="skip"):
        evaluate_autopilot_transition(
            current=AutopilotPhase.QA, verdict=AutopilotGateVerdict.SKIP,
            review_cycle=0, policy=policy,
        )


def test_review_fail_returns_to_plan_and_increments_cycle() -> None:
    t = _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.FAIL, cycle=0)
    assert t.to_phase == AutopilotPhase.PLAN
    assert t.review_cycle == 1
    assert t.return_to_plan_reason


def test_review_fail_past_max_cycles_blocks() -> None:
    t = _eval(AutopilotPhase.REVIEW, AutopilotGateVerdict.FAIL, cycle=3)
    assert t.to_phase == AutopilotPhase.BLOCKED
    assert t.terminal is True


def test_early_phase_fail_stays_in_phase() -> None:
    for phase in (AutopilotPhase.INTERVIEW, AutopilotPhase.PLAN, AutopilotPhase.EXECUTE):
        t = _eval(phase, AutopilotGateVerdict.FAIL)
        assert t.to_phase == phase


def test_cannot_transition_from_terminal_phase() -> None:
    with pytest.raises(ValueError, match="terminal"):
        _eval(AutopilotPhase.COMPLETE, AutopilotGateVerdict.PASS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v -k "transition or happy or qa or fail or gate_for or terminal"`
Expected: FAIL with `ImportError: cannot import name 'evaluate_autopilot_transition'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to magi_agent/harness/autopilot.py
_GATE_BY_PHASE: dict[AutopilotPhase, str] = {
    AutopilotPhase.INTERVIEW: "interview-ambiguity-cleared",
    AutopilotPhase.PLAN: "consensus-architect-then-critic",
    AutopilotPhase.EXECUTE: "execution-evidence",
    AutopilotPhase.REVIEW: "review-clean",
    AutopilotPhase.QA: "adversarial-qa",
}

_PASS_NEXT_PHASE: dict[AutopilotPhase, AutopilotPhase] = {
    AutopilotPhase.INTERVIEW: AutopilotPhase.PLAN,
    AutopilotPhase.PLAN: AutopilotPhase.EXECUTE,
    AutopilotPhase.EXECUTE: AutopilotPhase.REVIEW,
    AutopilotPhase.REVIEW: AutopilotPhase.QA,
    AutopilotPhase.QA: AutopilotPhase.COMPLETE,
}

_REVIEW_PHASES: tuple[AutopilotPhase, ...] = (AutopilotPhase.REVIEW, AutopilotPhase.QA)


def gate_for_phase(phase: AutopilotPhase) -> str:
    try:
        return _GATE_BY_PHASE[phase]
    except KeyError as exc:
        raise ValueError(f"phase {phase} has no gate") from exc


def _build_transition(
    *,
    from_phase: AutopilotPhase,
    to_phase: AutopilotPhase,
    gate: str,
    verdict: AutopilotGateVerdict,
    review_cycle: int,
    return_to_plan_reason: str | None = None,
) -> AutopilotPhaseTransition:
    return AutopilotPhaseTransition(
        from_phase=from_phase,
        to_phase=to_phase,
        gate=gate,
        verdict=verdict,
        review_cycle=review_cycle,
        return_to_plan_reason=return_to_plan_reason,
        terminal=to_phase in TERMINAL_PHASES,
    )


def evaluate_autopilot_transition(
    *,
    current: AutopilotPhase,
    verdict: AutopilotGateVerdict,
    review_cycle: int,
    policy: AutopilotFsmPolicy,
) -> AutopilotPhaseTransition:
    """Pure FSM transition. No I/O, no execution; returns the next transition snapshot."""
    if current in TERMINAL_PHASES:
        raise ValueError("cannot transition from a terminal phase")
    gate = gate_for_phase(current)

    if verdict is AutopilotGateVerdict.SKIP:
        if current is not AutopilotPhase.QA or not policy.qa_skip_allowed_for_nonruntime:
            raise ValueError(
                "skip verdict only allowed for QA when qaSkipAllowedForNonruntime is true"
            )
        return _build_transition(
            from_phase=current, to_phase=AutopilotPhase.COMPLETE, gate=gate,
            verdict=verdict, review_cycle=review_cycle,
        )

    if verdict is AutopilotGateVerdict.PASS:
        return _build_transition(
            from_phase=current, to_phase=_PASS_NEXT_PHASE[current], gate=gate,
            verdict=verdict, review_cycle=review_cycle,
        )

    # FAIL
    if current in _REVIEW_PHASES:
        next_cycle = review_cycle + 1
        if next_cycle > policy.max_review_cycles:
            return _build_transition(
                from_phase=current, to_phase=AutopilotPhase.BLOCKED, gate=gate,
                verdict=verdict, review_cycle=review_cycle,
            )
        return _build_transition(
            from_phase=current, to_phase=AutopilotPhase.PLAN, gate=gate,
            verdict=verdict, review_cycle=next_cycle,
            return_to_plan_reason=f"{current.value} gate not clean",
        )
    # interview / plan / execute fail: stay in phase (retry/continue)
    return _build_transition(
        from_phase=current, to_phase=current, gate=gate,
        verdict=verdict, review_cycle=review_cycle,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/harness/autopilot.py tests/test_autopilot_fsm_contract.py
git commit -m "feat(magi): autopilot PR1.4 — pure FSM transition function"
```

### Task 5: `__all__` exports + full-loop integration test

**Files:**
- Modify: `magi_agent/harness/autopilot.py`
- Test: `tests/test_autopilot_fsm_contract.py`

- [ ] **Step 1: Write the failing test** (drives a complete loop including one return-to-plan)

```python
# append to tests/test_autopilot_fsm_contract.py
def test_full_loop_with_one_review_failure_reaches_complete() -> None:
    policy = build_autopilot_policy(enabled=True)
    phase = AutopilotPhase.INTERVIEW
    cycle = 0
    verdicts = iter([
        ("interview", AutopilotGateVerdict.PASS),
        ("plan", AutopilotGateVerdict.PASS),
        ("execute", AutopilotGateVerdict.PASS),
        ("review", AutopilotGateVerdict.FAIL),   # bounce back to plan
        ("plan", AutopilotGateVerdict.PASS),
        ("execute", AutopilotGateVerdict.PASS),
        ("review", AutopilotGateVerdict.PASS),
        ("qa", AutopilotGateVerdict.PASS),
    ])
    visited: list[str] = []
    for _label, verdict in verdicts:
        t = evaluate_autopilot_transition(
            current=phase, verdict=verdict, review_cycle=cycle, policy=policy
        )
        visited.append(t.to_phase.value)
        phase, cycle = t.to_phase, t.review_cycle
    assert phase == AutopilotPhase.COMPLETE
    assert cycle == 1  # one review failure consumed one cycle
    assert visited[-1] == "complete"
```

- [ ] **Step 2: Run test to verify it fails (or passes pre-export)**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py::test_full_loop_with_one_review_failure_reaches_complete -v`
Expected: PASS (logic already correct) — this is a regression guard. If it FAILS, fix the transition function before continuing.

- [ ] **Step 3: Add `__all__` export block**

```python
# append to magi_agent/harness/autopilot.py
__all__ = [
    "AUTOPILOT_FEATURE_KEY",
    "DEFAULT_AUTOPILOT_MAX_REVIEW_CYCLES",
    "TERMINAL_PHASES",
    "AutopilotFsmPolicy",
    "AutopilotGateVerdict",
    "AutopilotOptOutState",
    "AutopilotPhase",
    "AutopilotPhaseTransition",
    "build_autopilot_policy",
    "evaluate_autopilot_transition",
    "gate_for_phase",
]
```

- [ ] **Step 4: Run the full file's tests**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add magi_agent/harness/autopilot.py tests/test_autopilot_fsm_contract.py
git commit -m "feat(magi): autopilot PR1.5 — exports + full-loop integration test"
```

---

# PR2 — Recipe manifest (`recipes/compiler.py`)

Registers `openmagi.autopilot` as a default-off, opt-out, customizable pack that
depends on the existing `openmagi.agent-methodology` and `openmagi.dev-coding` packs,
and activates on autopilot-flavored task types.

### Task 6: Register the `openmagi.autopilot` manifest

**Files:**
- Modify: `magi_agent/recipes/compiler.py` (inside `_first_party_packs()` return tuple, after the `openmagi.dev-coding` manifest)
- Test: `tests/test_autopilot_recipe_pack.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autopilot_recipe_pack.py
from __future__ import annotations

from magi_agent.recipes.compiler import (
    AgentRecipeCompiler,
    PackRegistry,
    ProfileResolutionRequest,
    ProfileResolver,
)


def test_autopilot_pack_registered_and_default_off() -> None:
    registry = PackRegistry.with_first_party_packs()
    pack = registry.get("openmagi.autopilot")
    assert pack.default_enabled is False
    assert pack.opt_out_allowed is True
    assert pack.customizable is True
    assert pack.hard_safety is False
    assert pack.depends_on_pack_ids == (
        "openmagi.agent-methodology",
        "openmagi.dev-coding",
    )
    assert "autopilot" in pack.task_profile_selectors


def test_autopilot_not_selected_by_default() -> None:
    registry = PackRegistry.with_first_party_packs()
    resolved = ProfileResolver(registry).resolve(ProfileResolutionRequest())
    assert "openmagi.autopilot" not in resolved.selected_pack_ids


def test_autopilot_selected_by_task_type_and_pulls_dependencies() -> None:
    registry = PackRegistry.with_first_party_packs()
    resolved = ProfileResolver(registry).resolve(
        ProfileResolutionRequest(taskProfile={"taskType": "autopilot"})
    )
    assert "openmagi.autopilot" in resolved.selected_pack_ids
    assert "openmagi.agent-methodology" in resolved.selected_pack_ids
    assert "openmagi.dev-coding" in resolved.selected_pack_ids


def test_autopilot_opt_out_respected() -> None:
    registry = PackRegistry.with_first_party_packs()
    resolved = ProfileResolver(registry).resolve(
        ProfileResolutionRequest(
            taskProfile={"taskType": "autopilot"},
            recipePackConfig={"packs": {"disable": ["openmagi.autopilot"]}},
        )
    )
    assert "openmagi.autopilot" not in resolved.selected_pack_ids


def test_autopilot_snapshot_aggregates_validator_refs() -> None:
    registry = PackRegistry.with_first_party_packs()
    snapshot = AgentRecipeCompiler(registry).compile(
        ProfileResolutionRequest(taskProfile={"taskType": "autopilot"})
    )
    assert "validator:autopilot:review-clean" in snapshot.validator_refs
    assert "checkpoint:autopilot:return-to-plan" in snapshot.checkpoint_refs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_autopilot_recipe_pack.py -v`
Expected: FAIL with `KeyError: unknown recipe pack id: openmagi.autopilot`

- [ ] **Step 3: Write minimal implementation** — insert this manifest into the tuple returned by `_first_party_packs()` in `magi_agent/recipes/compiler.py`, immediately after the `packId="openmagi.dev-coding"` manifest block:

```python
        RecipePackManifest(
            packId="openmagi.autopilot",
            displayName="Autopilot",
            description=(
                "Default-off strict autonomous FSM workflow metadata: interview -> "
                "consensus-plan -> execute -> review -> adversarial-QA with "
                "gate-failure return-to-plan."
            ),
            taskProfileSelectors=(
                "autopilot",
                "autonomous",
                "full-auto",
                "build-me",
            ),
            dependsOnPackIds=(
                "openmagi.agent-methodology",
                "openmagi.dev-coding",
            ),
            instructionRefs=("instruction:autopilot:strict-loop-contract",),
            callbackRefs=("callback:autopilot:phase-router",),
            validatorRefs=(
                "validator:autopilot:interview-ambiguity-cleared",
                "validator:autopilot:consensus-architect-then-critic",
                "validator:autopilot:review-clean",
                "validator:autopilot:qa-passed-or-skipped",
                "validator:autopilot:max-review-cycle-bounded",
            ),
            approvalGateRefs=(
                "approval:autopilot:execution-lane",
                "approval:autopilot:live-behavior",
            ),
            checkpointRefs=(
                "checkpoint:autopilot:interview",
                "checkpoint:autopilot:consensus-plan",
                "checkpoint:autopilot:execute",
                "checkpoint:autopilot:review",
                "checkpoint:autopilot:qa",
                "checkpoint:autopilot:return-to-plan",
            ),
            evidenceRefs=(
                "evidence:autopilot:clarified-spec",
                "evidence:autopilot:consensus-record",
                "evidence:autopilot:phase-transition",
            ),
            auditRefs=("audit:autopilot:fsm-lifecycle",),
            adkPrimitiveOwnership=common_adk_owners,
            openmagiBoundaryOwnership=common_openmagi_owners
            + (
                "OpenMagi autopilot owns recipe-selected FSM transition metadata; "
                "live phase driving attaches through ADK callbacks/plugins later",
                "OpenMagi autopilot does not own ADK Runner, Agent, Event, "
                "FunctionTool, SessionService, MemoryService, or ArtifactService",
            ),
            callbackSetMetadata=("CallbackSet:autopilot:phase-router-metadata-only",),
            validatorSetMetadata=("ValidatorSet:autopilot:fsm-gates-metadata-only",),
            approvalGateMetadata=("ApprovalGate:autopilot:metadata-only",),
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_autopilot_recipe_pack.py -v`
Expected: PASS (5)

- [ ] **Step 5: Run the existing resolver regression suite (no drift)**

Run: `uv run --extra dev pytest tests/test_gate2_recipe_profile_resolver.py -v`
Expected: PASS. If any test asserts an exact pack count or full selected-id tuple, update that expectation to include `openmagi.autopilot` and re-run.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/recipes/compiler.py tests/test_autopilot_recipe_pack.py
git commit -m "feat(magi): autopilot PR2 — register openmagi.autopilot recipe manifest"
```

---

# PR3 — Default-off presets (`harness/presets.py`)

Adds 5 autopilot presets that map each FSM gate to a hook point. All are
`category=TASK`, `default_on=False`, env-gated by `MAGI_AUTOPILOT`, and reference the
FSM gate names as `verifier_gates` metadata strings (no live verifier wiring).

### Task 7: Add autopilot presets

**Files:**
- Modify: `magi_agent/harness/presets.py` (add 5 `_preset(...)` entries inside the `_BUILTIN_PRESETS` tuple, alongside the existing `task-contract` / `goal-progress` entries)
- Test: `tests/test_autopilot_presets.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_autopilot_presets.py
from __future__ import annotations

from magi_agent.harness.presets import (
    PresetCategory,
    builtin_preset_by_key,
    builtin_preset_keys,
)

AUTOPILOT_PRESET_KEYS = {
    "autopilot-phase-router",
    "autopilot-interview-gate",
    "autopilot-consensus-gate",
    "autopilot-review-gate",
    "autopilot-qa-gate",
}


def test_all_autopilot_presets_present() -> None:
    assert AUTOPILOT_PRESET_KEYS <= set(builtin_preset_keys())


def test_autopilot_presets_are_default_off_and_env_gated() -> None:
    for key in AUTOPILOT_PRESET_KEYS:
        preset = builtin_preset_by_key(key)
        assert preset.category is PresetCategory.TASK
        assert preset.default_on is False
        assert preset.opt_out is True
        assert preset.hard_safety is False
        assert "MAGI_AUTOPILOT" in preset.env_gates


def test_autopilot_gate_presets_reference_fsm_gates() -> None:
    assert "interview-ambiguity-cleared" in builtin_preset_by_key("autopilot-interview-gate").verifier_gates
    assert "consensus-architect-then-critic" in builtin_preset_by_key("autopilot-consensus-gate").verifier_gates
    assert "review-clean" in builtin_preset_by_key("autopilot-review-gate").verifier_gates
    assert "adversarial-qa" in builtin_preset_by_key("autopilot-qa-gate").verifier_gates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_autopilot_presets.py -v`
Expected: FAIL with `KeyError: 'autopilot-phase-router'`

- [ ] **Step 3: Write minimal implementation** — add these 5 entries inside the `_BUILTIN_PRESETS = tuple(sorted((...)))` literal in `magi_agent/harness/presets.py`, next to the other `PresetCategory.TASK` presets:

```python
            _preset(
                "autopilot-phase-router",
                PresetCategory.TASK,
                default_on=False,
                hook_points=("beforeTurnStart",),
                blocking=True,
                fail_open=True,
                env_gates=("MAGI_AUTOPILOT",),
                scope_hints=("autopilot",),
            ),
            _preset(
                "autopilot-interview-gate",
                PresetCategory.TASK,
                default_on=False,
                hook_points=("beforeLLMCall",),
                blocking=True,
                fail_open=True,
                env_gates=("MAGI_AUTOPILOT",),
                verifier_gates=("interview-ambiguity-cleared",),
            ),
            _preset(
                "autopilot-consensus-gate",
                PresetCategory.TASK,
                default_on=False,
                hook_points=("onTaskCheckpoint",),
                env_gates=("MAGI_AUTOPILOT",),
                verifier_gates=("consensus-architect-then-critic",),
            ),
            _preset(
                "autopilot-review-gate",
                PresetCategory.TASK,
                default_on=False,
                hook_points=("afterCommit",),
                env_gates=("MAGI_AUTOPILOT",),
                verifier_gates=("review-clean", "coding-child-review"),
            ),
            _preset(
                "autopilot-qa-gate",
                PresetCategory.TASK,
                default_on=False,
                hook_points=("afterTurnEnd",),
                env_gates=("MAGI_AUTOPILOT",),
                verifier_gates=("adversarial-qa",),
            ),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_autopilot_presets.py -v`
Expected: PASS (3)

- [ ] **Step 5: Run the existing preset regression suite**

Run: `uv run --extra dev pytest tests/test_harness_builtin_presets.py tests/test_harness_preset_boundary.py -v`
Expected: PASS. If `test_harness_builtin_presets.py` asserts an exact preset count or a frozen `REQUIRED_PRESET_KEYS` set, add the 5 new keys to that expectation and re-run.

- [ ] **Step 6: Commit**

```bash
git add magi_agent/harness/presets.py tests/test_autopilot_presets.py
git commit -m "feat(magi): autopilot PR3 — default-off env-gated autopilot presets"
```

---

# PR4 — Interview ambiguity scoring contract (`harness/autopilot.py`)

Implements the quantitative ambiguity gate behind the `interview` phase: a depth
profile (quick/standard/deep) with a threshold, and a pure function turning a score
into a gate verdict. This makes the `interview-ambiguity-cleared` gate concrete.

### Task 8: `AutopilotAmbiguityScore` + depth thresholds + verdict function

**Files:**
- Modify: `magi_agent/harness/autopilot.py`
- Test: `tests/test_autopilot_fsm_contract.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_autopilot_fsm_contract.py
from magi_agent.harness.autopilot import (  # noqa: E402
    AMBIGUITY_THRESHOLD_BY_PROFILE,
    AutopilotAmbiguityScore,
    AutopilotInterviewDepth,
    interview_gate_verdict,
)


def test_depth_thresholds() -> None:
    assert AMBIGUITY_THRESHOLD_BY_PROFILE[AutopilotInterviewDepth.QUICK] == 0.30
    assert AMBIGUITY_THRESHOLD_BY_PROFILE[AutopilotInterviewDepth.STANDARD] == 0.20
    assert AMBIGUITY_THRESHOLD_BY_PROFILE[AutopilotInterviewDepth.DEEP] == 0.15


def test_ambiguity_score_bounds() -> None:
    with pytest.raises(ValidationError):
        AutopilotAmbiguityScore(depth=AutopilotInterviewDepth.STANDARD, score=1.5, rounds=1)
    with pytest.raises(ValidationError):
        AutopilotAmbiguityScore(depth=AutopilotInterviewDepth.STANDARD, score=0.2, rounds=0)


def test_interview_gate_passes_when_score_at_or_below_threshold() -> None:
    cleared = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.STANDARD, score=0.20, rounds=3
    )
    assert interview_gate_verdict(cleared) is AutopilotGateVerdict.PASS


def test_interview_gate_fails_when_score_above_threshold() -> None:
    unresolved = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.STANDARD, score=0.25, rounds=3
    )
    assert interview_gate_verdict(unresolved) is AutopilotGateVerdict.FAIL


def test_interview_gate_fails_at_max_rounds_even_if_unresolved() -> None:
    # hitting the round cap does not force PASS; the FSM treats FAIL-at-cap as
    # "carry forward unresolved", handled by the caller, not by faking a clear.
    capped = AutopilotAmbiguityScore(
        depth=AutopilotInterviewDepth.QUICK, score=0.9, rounds=5, max_rounds=5
    )
    assert capped.at_round_cap is True
    assert interview_gate_verdict(capped) is AutopilotGateVerdict.FAIL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v -k "depth or ambiguity or interview_gate"`
Expected: FAIL with `ImportError: cannot import name 'AutopilotAmbiguityScore'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to magi_agent/harness/autopilot.py
class AutopilotInterviewDepth(StrEnum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


AMBIGUITY_THRESHOLD_BY_PROFILE: dict[AutopilotInterviewDepth, float] = {
    AutopilotInterviewDepth.QUICK: 0.30,
    AutopilotInterviewDepth.STANDARD: 0.20,
    AutopilotInterviewDepth.DEEP: 0.15,
}

_DEFAULT_MAX_ROUNDS_BY_PROFILE: dict[AutopilotInterviewDepth, int] = {
    AutopilotInterviewDepth.QUICK: 5,
    AutopilotInterviewDepth.STANDARD: 12,
    AutopilotInterviewDepth.DEEP: 20,
}


class AutopilotAmbiguityScore(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    depth: AutopilotInterviewDepth
    score: float = Field(ge=0.0, le=1.0)
    rounds: int = Field(ge=1)
    max_rounds: int | None = Field(default=None, alias="maxRounds", ge=1)

    @property
    def threshold(self) -> float:
        return AMBIGUITY_THRESHOLD_BY_PROFILE[self.depth]

    @property
    def resolved_max_rounds(self) -> int:
        return self.max_rounds or _DEFAULT_MAX_ROUNDS_BY_PROFILE[self.depth]

    @property
    def at_round_cap(self) -> bool:
        return self.rounds >= self.resolved_max_rounds


def interview_gate_verdict(score: AutopilotAmbiguityScore) -> AutopilotGateVerdict:
    """Pure: ambiguity at/below the depth threshold clears the interview gate."""
    if score.score <= score.threshold:
        return AutopilotGateVerdict.PASS
    return AutopilotGateVerdict.FAIL
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v -k "depth or ambiguity or interview_gate"`
Expected: PASS (5)

- [ ] **Step 5: Extend `__all__`** — add these names to the existing `__all__` list in `magi_agent/harness/autopilot.py`:

```python
    "AMBIGUITY_THRESHOLD_BY_PROFILE",
    "AutopilotAmbiguityScore",
    "AutopilotInterviewDepth",
    "interview_gate_verdict",
```

- [ ] **Step 6: Run the full FSM suite + commit**

Run: `uv run --extra dev pytest tests/test_autopilot_fsm_contract.py -v`
Expected: PASS (all)

```bash
git add magi_agent/harness/autopilot.py tests/test_autopilot_fsm_contract.py
git commit -m "feat(magi): autopilot PR4 — interview ambiguity scoring contract"
```

---

# Final verification (after all PRs)

- [ ] **Run the full touched-area suite:**

Run:
```bash
uv run --extra dev pytest \
  tests/test_autopilot_fsm_contract.py \
  tests/test_autopilot_recipe_pack.py \
  tests/test_autopilot_presets.py \
  tests/test_gate2_recipe_profile_resolver.py \
  tests/test_harness_builtin_presets.py \
  tests/test_harness_preset_boundary.py -v
```
Expected: PASS (all).

- [ ] **Confirm metadata-only discipline held:** grep the new module to verify no
  ADK runtime imports and no `*_attached=True` slipped in.

Run: `grep -nE "(import google|adk|attached=True|trafficAttached. True)" magi_agent/harness/autopilot.py`
Expected: no matches.

- [ ] **Port to canonical OSS repo:** mirror `harness/autopilot.py`, the manifest
  block, and the presets into `openmagi/magi-agent` (package name `magi_agent`),
  then sync back per the OSS sync workflow. Required before declaring the work done.

---

# Out of scope (forward reference only — do NOT implement here)

**PR5 — live FSM attachment.** Driving the FSM against real turns via ADK
callbacks/plugins (the `callback:autopilot:phase-router`), wiring real verifier_bus
gates for `consensus-architect-then-critic` and `adversarial-qa`, and goal_loop /
parallel_execution execution-phase integration. This crosses the traffic-free
boundary and must: (a) land in `openmagi/magi-agent` first, (b) ship default-off
behind `MAGI_AUTOPILOT`, (c) use the gate5b-style shadow/dry-run → canary → fleet
rollout pattern. Track against the Track 19 GA live harness work.

## Open questions (resolve before PR5; non-blocking for PR1–4)

1. Should `adversarial-qa` be an autopilot-only gate, or split into a reusable
   `openmagi.qa` pack so non-autopilot flows can use it too?
2. PR5 owner repo / sequencing vs the OSS Track 19 GA live harness.
