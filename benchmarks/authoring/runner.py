"""Tier-agnostic scenario runner.

Drives one scenario through the adapter + user turns, evaluating the per-turn
invariants on every turn and the final oracle (draft/params/plan subset matches
+ persisted-state helpers) after the save leg. On the FIRST divergence it stops
and records ``first_divergence`` (turn index, the invariant/oracle code, and a
one-line expected-vs-got). See design section 6.3.

For T1 the compiler LLM is a ``ScriptedLlm`` (canned envelopes, one per turn)
and the user side is the scenario's literal ``turns``. The same runner backs
the live tiers by swapping who produces the two nondeterministic streams.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from benchmarks.authoring.adapter import (
    MagiPolicyFlowAdapter,
    MagiRuleFlowAdapter,
    SaveResult,
    TurnResult,
)
from benchmarks.authoring.fakes import ScriptedLlm
from benchmarks.authoring.injection import use_scripted_llm
from benchmarks.authoring.invariants import check_invariants
from benchmarks.authoring.oracles import persisted as P
from benchmarks.authoring.scenario import Scenario


@dataclass
class RunResult:
    scenario_id: str
    passed: bool
    turns: int
    reached_ready_at: int | None = None
    first_divergence: dict[str, Any] | None = None
    transcript: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


class _MonkeyShim:
    """A minimal ``setattr``-only monkeypatch stand-in for use outside pytest.

    The runner needs to patch the two model factories for the duration of one
    scenario. In CI it is called from a pytest test that already owns a real
    ``monkeypatch``; but ``run_scenario`` must also work from the standalone CLI
    (U6), so it manages its own patch lifecycle here and undoes it in a finally.
    """

    def __init__(self) -> None:
        self._undo: list[tuple[Any, str, Any, bool]] = []

    def setattr(self, target: Any, name: str, value: Any) -> None:
        had = hasattr(target, name)
        old = getattr(target, name, None)
        self._undo.append((target, name, old, had))
        setattr(target, name, value)

    def undo(self) -> None:
        for target, name, old, had in reversed(self._undo):
            if had:
                setattr(target, name, old)
            else:  # pragma: no cover - defensive
                try:
                    delattr(target, name)
                except AttributeError:
                    pass
        self._undo.clear()


def _get_path(obj: Any, dotted: str) -> Any:
    cur = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover
        return "<missing>"


_MISSING = _Missing()


def run_scenario(
    scenario: Scenario,
    runtime: Any,
    *,
    token: str,
    tier: str = "t1",
) -> RunResult:
    """Execute one scenario and return a RunResult with first_divergence."""
    shim = _MonkeyShim()
    try:
        # T1: inject the scripted compiler. Live tiers leave the factory alone.
        scripted = None
        if tier == "t1":
            scripted = ScriptedLlm(scenario.llm_script)
            use_scripted_llm(shim, scripted)

        adapter = _make_adapter(scenario, runtime, token)
        state = adapter.start(scenario)
        # HEAD-compat seed (flow A dead-LLM shim; see scenario.py docstring).
        if scenario.flow == "single_rule" and scenario.seed_draft:
            state.working = dict(scenario.seed_draft)
        elif scenario.flow == "linked_policy" and scenario.seed_params:
            state.working = dict(scenario.seed_params)

        transcript: list[dict[str, Any]] = []
        reached_ready_at: int | None = None
        # M4 question-loop tracking: (question_id -> [working-state hashes]).
        question_history: list[tuple[set[str], str]] = []

        turn_index = 0
        for turn in scenario.turns:
            if turn_index >= scenario.turn_budget:
                break
            answers = _resolve_answers(scenario, turn)
            result = adapter.step(state, say=turn.say, answers=answers)
            transcript.append(
                {"turn": turn_index, "say": turn.say, "answers": answers,
                 "response": result.raw, "http_status": result.http_status}
            )

            # Per-turn invariants.
            violations = check_invariants(
                result, flow=scenario.flow, answers=answers, turn_index=turn_index
            )
            if violations:
                v = violations[0]
                return _fail(
                    scenario, turn_index, transcript,
                    code=v.invariant_id, expected="invariant holds", got=v.evidence,
                    kind="invariant",
                )

            # Question-loop metric (M4): the SAME question id is asked again
            # after the user ANSWERED (attempted progress) yet the working state
            # did not change. A repeat when the user provided no answers is
            # honest non-engagement (e.g. out_of_scope), NOT a flow loop.
            q_ids = {q.get("id") for q in result.questions if q.get("id")}
            state_hash = _hash_working(result.working)
            attempted_progress = bool(answers)
            for prev_ids, prev_hash in question_history:
                if (
                    q_ids
                    and q_ids & prev_ids
                    and prev_hash == state_hash
                    and attempted_progress
                    and scenario.oracle.no_question_loop
                ):
                    return _fail(
                        scenario, turn_index, transcript,
                        code="no_question_loop",
                        expected="no repeated question after an answer with no progress",
                        got=f"repeated {sorted(q_ids & prev_ids)} despite an answer",
                        kind="oracle",
                    )
            question_history.append((q_ids, state_hash))

            if result.ready_to_save and reached_ready_at is None:
                reached_ready_at = turn_index + 1

            turn_index += 1

        # Final per-scenario oracle.
        final = _last_turn_result(adapter, state, scenario, transcript)
        divergence = _check_final_oracle(
            scenario, final, reached_ready_at, turn_index
        )
        if divergence is not None:
            return _fail_final(scenario, turn_index, transcript, divergence)

        # Save leg + persisted oracles.
        save_result = _do_save(adapter, state, scenario)
        divergence = _check_persisted(adapter, scenario, save_result)
        if divergence is not None:
            return _fail_final(scenario, turn_index, transcript, divergence)

        return RunResult(
            scenario_id=scenario.id,
            passed=True,
            turns=turn_index,
            reached_ready_at=reached_ready_at,
            transcript=transcript,
            metrics={"turns_to_ready": reached_ready_at},
        )
    finally:
        shim.undo()


def _make_adapter(scenario: Scenario, runtime: Any, token: str):
    if scenario.flow == "single_rule":
        return MagiRuleFlowAdapter(runtime, token)
    return MagiPolicyFlowAdapter(runtime, token)


def _resolve_answers(scenario: Scenario, turn) -> dict[str, str]:
    if turn.answers_from_slots:
        # T1 uses explicit answers; answers_from_slots is a T2 deterministic
        # user-sim affordance (U6). In T1 replay we treat it as "no answers"
        # unless explicit answers are also present.
        return dict(turn.answers)
    return dict(turn.answers)


def _hash_working(working: dict[str, Any]) -> str:
    import hashlib
    import json

    try:
        blob = json.dumps(working, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = repr(working)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _last_turn_result(adapter, state, scenario: Scenario, transcript) -> TurnResult:
    """Reconstruct the last TurnResult from the transcript (already normalized)."""
    if not transcript:
        # No turns (e.g. grouped_hybrid): synthesize an empty result.
        return TurnResult(
            assistant_message="", working=state.working, plan=state.plan,
            missing=[], questions=[], needs_more=False, ready_to_save=False,
            schema_issues=[], raw={}, http_status=200,
        )
    raw = transcript[-1]["response"]
    if scenario.flow == "single_rule":
        working = raw.get("draft") or state.working or {}
        return TurnResult(
            assistant_message=raw.get("assistant_message", ""),
            working=working, plan=None,
            missing=list(raw.get("missing_fields") or []),
            questions=list(raw.get("questions") or []),
            needs_more=bool(raw.get("needs_more")),
            ready_to_save=bool(raw.get("ready_to_save")),
            schema_issues=list(raw.get("schema_issues") or []),
            raw=raw, http_status=transcript[-1]["http_status"],
        )
    working = raw.get("params") or state.working or {}
    return TurnResult(
        assistant_message=raw.get("assistant_message", ""),
        working=working, plan=raw.get("plan"),
        missing=list(raw.get("missing_params") or []),
        questions=list(raw.get("questions") or []),
        needs_more=bool(raw.get("needs_more")),
        ready_to_save=bool(raw.get("ready_to_save")),
        schema_issues=list(raw.get("schema_issues") or []),
        raw=raw, http_status=transcript[-1]["http_status"],
    )


def _check_final_oracle(
    scenario: Scenario, final: TurnResult, reached_ready_at: int | None, turns: int
):
    oracle = scenario.oracle
    # expect_ready within max_turns_to_ready.
    if oracle.expect_ready:
        if not final.ready_to_save:
            return _div("expect_ready", "ready_to_save=true", "never reached ready")
        if oracle.max_turns_to_ready is not None and reached_ready_at is not None:
            if reached_ready_at > oracle.max_turns_to_ready:
                return _div(
                    "max_turns_to_ready",
                    f"<= {oracle.max_turns_to_ready} turns",
                    f"reached ready at turn {reached_ready_at}",
                )
    else:
        if final.ready_to_save:
            return _div("expect_ready", "ready_to_save=false", "reached ready unexpectedly")

    # dotted-path subset matches.
    for dotted, expected in (oracle.draft or {}).items():
        got = _get_path(final.working, dotted)
        if got != expected:
            return _div(f"draft.{dotted}", expected, got)
    for dotted, expected in (oracle.params or {}).items():
        got = _get_path(final.working, dotted)
        if got != expected:
            return _div(f"params.{dotted}", expected, got)
    for dotted, expected in (oracle.plan or {}).items():
        got = _get_path(final.plan or {}, dotted)
        if got != expected:
            return _div(f"plan.{dotted}", expected, got)

    for key in oracle.draft_absent_keys:
        if _get_path(final.working, key) is not _MISSING:
            return _div(f"draft_absent[{key}]", "absent", "present")
    return None


def _do_save(adapter, state, scenario: Scenario) -> SaveResult | None:
    if scenario.save == "none":
        return None
    if scenario.save == "grouped":
        return _do_grouped_save(adapter, scenario)
    return adapter.save(state, scenario)


def _do_grouped_save(adapter, scenario: Scenario) -> SaveResult:
    spec = scenario.grouped_save
    gid = spec["group_id"]
    rule_ids: list[str] = []
    for i, rule in enumerate(spec.get("rules") or []):
        body = dict(rule)
        body["groupId"] = gid
        body.setdefault("id", f"{gid}_r{i}")
        adapter._client.put("/v1/app/customize/custom-rules", json=body)
        rule_ids.append(body["id"])
    resp = adapter._client.put(
        f"/v1/app/policies/{gid}",
        json={
            "displayName": spec.get("display_name", gid),
            "intent": spec.get("intent", ""),
            "ruleIds": rule_ids,
        },
    )
    return SaveResult(
        ok=resp.status_code == 200, http_status=resp.status_code,
        raw=resp.json() if resp.status_code == 200 else {},
        policy_id=gid,
    )


def _check_persisted(adapter, scenario: Scenario, save_result):
    oracle = scenario.oracle
    snap = adapter.snapshot_persisted()
    pers = oracle.persisted or {}
    try:
        if oracle.never_persists:
            # Store must be identical before/after — but the snapshot GET ran
            # the backfill; for a never_persists scenario nothing was ever
            # written, so the store file is absent/empty. Assert no user rules.
            rules = snap.store.get("verification", {}).get("custom_rules", [])
            if rules:
                return _div("never_persists", "no persisted rules", f"{len(rules)} rules")
        if pers.get("rule_valid_and_clean") and save_result is not None:
            P.assert_rule_clean(snap, save_result.rule_id)
        if pers.get("policy_intent_is_first_utterance") and save_result is not None:
            intent = _first_utterance(scenario)
            # envelope save -> the promoted 1-rule policy references rule_id;
            # from-plan save -> the policy references the gate rule id.
            ref_id = save_result.rule_id or save_result.gate_id
            display = (
                scenario.save_spec.get("display_name")
                if scenario.save == "envelope" and scenario.save_spec
                else None
            )
            P.assert_policy_intent(snap, ref_id, intent, expected_display=display)
        if pers.get("no_orphan_rules"):
            P.assert_no_orphan_rules(snap)
        if pers.get("no_double_representation") and scenario.save == "grouped":
            P.assert_no_double_representation(snap, scenario.grouped_save["group_id"])
        if pers.get("group_single_policy") and scenario.save == "grouped":
            P.assert_no_double_representation(snap, scenario.grouped_save["group_id"])
        if pers.get("from_plan_triple") and save_result is not None:
            P.assert_from_plan_triple(snap, save_result)
        if pers.get("catalog_consistent"):
            P.assert_catalog_consistent(snap)
    except P.OracleFailure as exc:
        return _div(exc.code, "oracle passes", str(exc))
    return None


def _first_utterance(scenario: Scenario) -> str:
    for t in scenario.turns:
        if t.say and t.say.strip():
            return t.say.strip()
    return ""


def _div(code: str, expected: Any, got: Any) -> dict[str, Any]:
    return {"code": code, "expected": expected, "got": got}


def _fail(scenario, turn_index, transcript, *, code, expected, got, kind) -> RunResult:
    return RunResult(
        scenario_id=scenario.id, passed=False, turns=turn_index + 1,
        transcript=transcript,
        first_divergence={
            "turn": turn_index, kind: code, "expected": expected, "got": got,
        },
    )


def _fail_final(scenario, turns, transcript, divergence) -> RunResult:
    return RunResult(
        scenario_id=scenario.id, passed=False, turns=turns, transcript=transcript,
        first_divergence={
            "turn": turns, "oracle": divergence["code"],
            "expected": divergence["expected"], "got": divergence["got"],
        },
    )
