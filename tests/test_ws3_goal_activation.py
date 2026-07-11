"""WS3 PR3c - profile activation + the arch-tdd trap guard (the #641-class guard).

PR3c flips the WS3 deliverable ON for the local ``full`` profile (and ``lab``,
which layers on top of ``full``):

  * ``MAGI_PLAN_LEDGER_DURABLE_ENABLED`` (the durable cross-turn plan/todo
    ledger), and
  * ``MAGI_GOAL_COMPLETION_EVIDENCE_FIRST_ENABLED`` (the deterministic pre-judge
    resolver: all-complete ledger short-circuits to ``done`` and a clean stop
    short of verifiable completion emits an honest ``goal_paused``).

``MAGI_GOAL_LOOP_ENABLED`` has since been promoted to a profile-aware default-ON
(``_pb``) flag: it is the ledger-first auto-continue authority, ambient for every
turn in the full / lab profile via the profile resolver, with a deterministic
measurable-progress brake (ok tool end OR ledger delta OR new evidence), NOT the
cost-bearing LLM judge. ``MAGI_GOAL_NUDGE_REQUIRED_EVIDENCE`` stays unset globally
(recipe-scoped, so free-form chat is never forced to provide evidence). The safe
/ eval / off / hosted profiles keep every WS3 / auto-continue flag OFF.

The arch-tdd trap (design 4.4 / 4.5): the headline "full" deliverable is reached
via SEAM 2, which is hoisted OUTSIDE the goal-loop policy guard, so it fires with
``goal_loop_policy is None``. The two end-to-end driver tests here apply the REAL
full profile, derive ``evidence_first`` FROM that resolved env (never hardcoded),
and assert the deliverable fires with ``MAGI_GOAL_NUDGE_ENABLED`` UNSET.
"""
from __future__ import annotations

from typing import Any, Iterator

import pytest

from magi_agent.config.env import (
    is_goal_completion_evidence_first_enabled,
    is_goal_nudge_enabled,
    is_plan_ledger_durable_enabled,
    read_goal_required_evidence,
)
from magi_agent.config.flags import flag_profile_bool
from magi_agent.runtime.local_defaults import (
    apply_local_eval_runtime_defaults,
    apply_local_full_runtime_defaults,
)
from magi_agent.runtime.plan_ledger import TodoItem
from tests.support.engine_capture import capture_engine_turn
from tests.support.engine_fakes import MockRunner, text_event

# The two WS3 flags PR3c activates in the full profile.
_LEDGER_FLAG = "MAGI_PLAN_LEDGER_DURABLE_ENABLED"
_EVIDENCE_FIRST_FLAG = "MAGI_GOAL_COMPLETION_EVIDENCE_FIRST_ENABLED"
# The cost-bearing lab judge loop that stays lab-only (NOT promoted by WS3).
_GOAL_LOOP_FLAG = "MAGI_GOAL_LOOP_ENABLED"
# The recipe-scoped evidence list + subsystem-A nudge gate, both unset globally.
_REQUIRED_EVIDENCE_FLAG = "MAGI_GOAL_NUDGE_REQUIRED_EVIDENCE"
_NUDGE_FLAG = "MAGI_GOAL_NUDGE_ENABLED"

# Every MAGI_* knob the profile tests resolve must be cleared first so Kevin's
# exported shell env (MAGI_MEMORY_*=1, provider keys, ...) cannot give a false
# green (R4: non-hermetic suites are the documented hazard, ref incident #641).
_HERMETIC_KEYS = (
    _LEDGER_FLAG,
    _EVIDENCE_FIRST_FLAG,
    _GOAL_LOOP_FLAG,
    _REQUIRED_EVIDENCE_FLAG,
    _NUDGE_FLAG,
    "MAGI_RUNTIME_PROFILE",
    "MAGI_AGENT_LOCAL_FULL_RUNTIME_DEFAULTS",
)


@pytest.fixture
def hermetic_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    for key in _HERMETIC_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield


def _todos(*pairs: tuple[str, str]) -> tuple[TodoItem, ...]:
    return tuple(TodoItem(content=c, status=s) for c, s in pairs)  # type: ignore[arg-type]


def _exploding_judge_factory() -> Any:
    """A judge factory whose caller fails loudly if the judge is EVER consulted.

    SEAM 2 (loop OFF) has no judge, so a turn that completes without raising is
    direct proof of ZERO judge calls.
    """

    async def _caller(_: str) -> str:
        raise AssertionError(
            "judge must NOT be called: SEAM 2 (loop OFF) is deterministic"
        )

    def _factory(_policy: object) -> object:
        return _caller

    return _factory


class _BlockedGate:
    """Fake FinalOutputGate that always returns a hard-failure decision."""

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    def evaluate(self, *_a: object, **_k: object) -> object:
        class _Blocked:
            status = "blocked"
            reason_codes = ("numeric_claim_mismatch",)

        return _Blocked()


def _payloads_of_type(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        payload = ev.get("payload")
        if isinstance(payload, dict) and payload.get("type") == type_:
            out.append(payload)
    return out


# --------------------------------------------------------------------------- #
# Profile activation (the #641-class guard at the resolution layer)            #
# --------------------------------------------------------------------------- #


def test_full_profile_enables_plan_ledger_and_evidence_first(
    hermetic_env: None,
) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # Both WS3 flags resolve ON through their single-source-of-truth readers.
    assert env.get(_LEDGER_FLAG) == "1"
    assert is_plan_ledger_durable_enabled(env) is True
    assert env.get(_EVIDENCE_FIRST_FLAG) == "1"
    assert is_goal_completion_evidence_first_enabled(env) is True


def test_full_profile_enables_goal_loop_auto_continue(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # MAGI_GOAL_LOOP_ENABLED is now the ledger-first auto-continue authority,
    # promoted to profile-aware default-ON (_pb). It self-enables under the full
    # profile via the profile resolver (no explicit seed needed) so a mid-multi-
    # step-task clean break re-invokes instead of stopping with "I'll continue".
    # The brake is a deterministic measurable-progress gate, not the LLM judge.
    assert _GOAL_LOOP_FLAG not in env
    assert flag_profile_bool(_GOAL_LOOP_FLAG, env=env) is True


def test_full_profile_does_not_force_required_evidence(hermetic_env: None) -> None:
    env: dict[str, str] = {}
    apply_local_full_runtime_defaults(env)
    # MAGI_GOAL_NUDGE_REQUIRED_EVIDENCE stays unset globally (recipe-scoped), so
    # free-form chat is never forced to provide evidence and the resolver's
    # evidence branch is reached ONLY when a recipe declares it.
    assert _REQUIRED_EVIDENCE_FLAG not in env
    assert read_goal_required_evidence(env) == ()
    # WS3 itself never SETS the synthetic-nudge flag. F1-B demoted
    # MAGI_GOAL_NUDGE_ENABLED to STRICT default-OFF (the legacy nudge is
    # superseded by the ambient goal loop; profile-ON revival caused response
    # duplication), so with the flag unset the legacy nudge stays OFF.
    assert _NUDGE_FLAG not in env
    assert is_goal_nudge_enabled(env) is False


@pytest.mark.parametrize("profile", ["safe", "off", "minimal", "conservative"])
def test_safe_profile_keeps_ws3_off(hermetic_env: None, profile: str) -> None:
    env = {"MAGI_RUNTIME_PROFILE": profile}
    apply_local_full_runtime_defaults(env)
    for flag in (_LEDGER_FLAG, _EVIDENCE_FIRST_FLAG, _GOAL_LOOP_FLAG):
        assert flag not in env, f"{profile}:{flag}"
        # All three WS3 / auto-continue flags are profile-aware default-ON (_pb)
        # and read False under a safe profile.
        assert flag_profile_bool(flag, env=env) is False, f"{profile}:{flag}"
    assert is_plan_ledger_durable_enabled(env) is False, profile
    assert is_goal_completion_evidence_first_enabled(env) is False, profile


def test_eval_profile_keeps_ws3_off(hermetic_env: None) -> None:
    # The eval overlay never touches the WS3 flags; it must NOT inherit the
    # full-profile activation.
    env: dict[str, str] = {}
    apply_local_eval_runtime_defaults(env)
    for flag in (_LEDGER_FLAG, _EVIDENCE_FIRST_FLAG, _GOAL_LOOP_FLAG):
        assert flag not in env, flag
    assert is_plan_ledger_durable_enabled(env) is False
    assert is_goal_completion_evidence_first_enabled(env) is False


def test_explicit_off_overrides_full_profile(hermetic_env: None) -> None:
    # setdefault semantics: an explicit operator "0" walks each feature back.
    env = {flag: "0" for flag in (_LEDGER_FLAG, _EVIDENCE_FIRST_FLAG)}
    apply_local_full_runtime_defaults(env)
    assert env[_LEDGER_FLAG] == "0"
    assert env[_EVIDENCE_FIRST_FLAG] == "0"
    assert is_plan_ledger_durable_enabled(env) is False
    assert is_goal_completion_evidence_first_enabled(env) is False


def test_lab_profile_inherits_ws3_on(hermetic_env: None) -> None:
    # lab layers on top of the full overlay, so it inherits the activation AND
    # additionally turns the judge loop ON (lab opts into the experimental set).
    from magi_agent.runtime.local_defaults import apply_lab_runtime_defaults

    env: dict[str, str] = {}
    apply_lab_runtime_defaults(env)
    assert env.get(_LEDGER_FLAG) == "1"
    assert env.get(_EVIDENCE_FIRST_FLAG) == "1"
    # MAGI_GOAL_LOOP_ENABLED is now profile-aware default-ON (_pb): the lab
    # overlay no longer seeds it explicitly (it left LAB_EXPERIMENTAL_FLAGS), but
    # it self-enables under the lab / full profile resolver.
    assert _GOAL_LOOP_FLAG not in env
    assert flag_profile_bool(_GOAL_LOOP_FLAG, env=env) is True


def test_hosted_resilience_keeps_ws3_off() -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "resilience"}
    apply_hosted_runtime_defaults(env)
    # Durable JSONL on the per-pod PVC + evidence-first hard paths stay OFF on
    # hosted pending sign-off; the resilience overlay sets NO WS3 flags.
    for flag in (_LEDGER_FLAG, _EVIDENCE_FIRST_FLAG, _GOAL_LOOP_FLAG):
        assert flag not in env, flag


def test_hosted_off_stage_keeps_ws3_off() -> None:
    from magi_agent.runtime.hosted_defaults import apply_hosted_runtime_defaults

    env = {"MAGI_DEPLOYMENT": "hosted", "MAGI_CONTROL_STAGE": "off"}
    apply_hosted_runtime_defaults(env)
    for flag in (_LEDGER_FLAG, _EVIDENCE_FIRST_FLAG, _GOAL_LOOP_FLAG):
        assert flag not in env, flag


# --------------------------------------------------------------------------- #
# The arch-tdd trap (design 4.4 / 4.5): the SHIPPED "full" deliverable must be  #
# reachable via SEAM 2 with the lab loop UNSET and the nudge gate UNSET.        #
# `evidence_first` is derived FROM the applied full profile, never hardcoded.   #
# --------------------------------------------------------------------------- #


def test_seam2_full_profile_done_with_loop_off(hermetic_env: None) -> None:
    # 1) Resolve the REAL full profile and prove the loop is OFF under it.
    full_env: dict[str, str] = {}
    apply_local_full_runtime_defaults(full_env)
    assert _GOAL_LOOP_FLAG not in full_env  # lab judge loop NOT promoted
    evidence_first = is_goal_completion_evidence_first_enabled(full_env)
    assert evidence_first is True  # derived from the profile, not hardcoded
    assert read_goal_required_evidence(full_env) == ()  # free-form chat unforced

    # 2) Drive a clean-stop turn with an all-complete durable ledger and NO
    #    per-turn goal_loop_policy (goal_loop_policy is None -> "full" wiring).
    snapshot = _todos(("t1", "completed"), ("t2", "completed"))
    runner = MockRunner([text_event("All done.", partial=False, turn_complete=True)])
    out = run_capture(
        runner,
        driver_kwargs={
            "user_id": "cli",
            "evidence_first": evidence_first,
            "plan_ledger_reader": lambda _sid: snapshot,
            "required_evidence": (),
            # Present but MUST NOT be called: SEAM 2 is deterministic.
            "goal_loop_judge_factory": _exploding_judge_factory(),
        },
    )

    complete = _payloads_of_type(out["events"], "goal_loop_complete")
    assert len(complete) == 1
    assert complete[0].get("reason") == "ledger_all_complete"
    # ZERO judge calls: the exploding factory never raised (turn completed).
    assert not _payloads_of_type(out["events"], "goal_paused")
    assert out["terminal"]["terminal"] == "completed"


def test_seam2_full_profile_pause_with_loop_off(
    hermetic_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The recipe-scoped evidence pause: required_evidence reaches the engine via
    # Reader 2 (the engine-side terminus), gated ONLY on the evidence-first flag,
    # NEVER on the nudge gate (which stays UNSET here, proving 4.5 independence).
    full_env: dict[str, str] = {}
    apply_local_full_runtime_defaults(full_env)
    assert _GOAL_LOOP_FLAG not in full_env
    assert _NUDGE_FLAG not in full_env
    evidence_first = is_goal_completion_evidence_first_enabled(full_env)
    assert evidence_first is True

    import magi_agent.evidence.final_output_gate as gate_mod

    monkeypatch.setattr(gate_mod, "FinalOutputGate", _BlockedGate)

    snapshot = _todos(("t1", "completed"), ("t2", "in_progress"))
    runner = MockRunner(
        [text_event("Partial work.", partial=False, turn_complete=True)]
    )
    out = run_capture(
        runner,
        driver_kwargs={
            "user_id": "cli",
            "evidence_first": evidence_first,
            "plan_ledger_reader": lambda _sid: snapshot,
            # Supplied directly = the engine-side terminus of Reader 2; the nudge
            # gate is never set, so this proves Reader 2 independence (4.5).
            "required_evidence": ("source_ledger",),
            "goal_loop_judge_factory": _exploding_judge_factory(),
        },
    )

    paused = _payloads_of_type(out["events"], "goal_paused")
    assert len(paused) == 1
    assert paused[0].get("reason") == "evidence_unverifiable"
    # No synthetic success; partial output preserved.
    assert not _payloads_of_type(out["events"], "goal_loop_complete")
    assert out["terminal"]["terminal"] == "completed"


def run_capture(runner: MockRunner, *, driver_kwargs: dict[str, Any]) -> dict[str, Any]:
    import asyncio

    turn_input = {
        "prompt": "do the thing",
        "session_id": "s1",
        "turn_id": "t1",
    }
    return asyncio.run(
        capture_engine_turn(turn_input, runner, driver_kwargs=driver_kwargs)
    )
