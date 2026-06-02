from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from openmagi_core_agent.meta_orchestration.commit_adapter import MetaBeforeCommitVerdict
from openmagi_core_agent.meta_orchestration.final_assembly import MetaFinalAssemblyPlan
from openmagi_core_agent.meta_orchestration.inspection_loop import (
    MetaInspectedChildVerdict,
    MetaInspectionLoopResult,
)
from openmagi_core_agent.meta_orchestration.task_plan import (
    MetaChildTaskSpec,
    MetaTaskPlan,
    _validate_public_ref,
)
from openmagi_core_agent.runtime.public_events import (
    PublicEvent,
    RuleVerdict,
    authorize_rule_check_event,
    rule_check_event,
    runtime_trace_event,
    task_board_event,
    turn_phase_event,
)


_MAX_EVENTS = 25
_MAX_CHILD_EVENTS = 20
_PUBLIC_RECEIPT_OR_EVIDENCE_REF_RE = re.compile(
    r"^(?:(?:receipt:)?sha256:[a-fA-F0-9]{64}|"
    r"(?:evidence|source|file|result|tool-result):"
    r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,160})$"
)


def project_meta_plan_created_events(
    plan: MetaTaskPlan,
    *,
    max_events: int = _MAX_EVENTS,
) -> tuple[PublicEvent, ...]:
    """Project a factory-validated meta plan into public Work Console progress."""

    parsed = _parse_plan(plan)
    plan_digest = _canonical_digest(parsed.model_dump(by_alias=True, mode="python"))
    events = (
        turn_phase_event(turn_id=_digest_id("meta-plan", plan_digest), phase="planning"),
        task_board_event(tasks=_plan_tasks(parsed, plan_digest=plan_digest)),
    )
    return _bounded(events, max_events=max_events)


def project_meta_child_task_scheduled_event(
    plan: MetaTaskPlan,
    *,
    task_index: int,
    receipt_ref: str,
) -> PublicEvent:
    """Project parent scheduling metadata without claiming child execution."""

    parsed = _parse_plan(plan)
    child = _child_at(parsed, task_index)
    safe_receipt_ref = _validate_receipt_or_evidence_ref(receipt_ref, "receiptRef")
    return {
        "type": "spawn_started",
        "taskId": _task_public_id(parsed, child, task_index),
        "persona": "meta_child",
        "deliver": child.delivery_mode,
        "detail": f"scheduled receipt={safe_receipt_ref}",
    }


def project_meta_parent_inspection_events(
    inspection: MetaInspectionLoopResult,
    *,
    max_events: int = _MAX_EVENTS,
) -> tuple[PublicEvent, ...]:
    """Project parent inspection and acceptance decisions into public events."""

    parsed = _parse_inspection(inspection)
    events: list[PublicEvent] = [
        _rule_check_with_evidence(
            rule_id=_rule_id("meta-inspection", parsed.loop_id),
            verdict=_aggregate_rule_verdict(parsed),
            detail=(
                "meta_inspection "
                f"status={parsed.aggregate_status} "
                f"children={len(parsed.child_verdicts)} "
                f"retrySchedules={len(parsed.retry_schedule_refs)} "
                f"acceptedEvidenceRefCount={len(parsed.accepted_child_evidence_refs_for_assembly)}"
            ),
            evidence_kind="inspection",
            evidence_seed={
                "loopId": parsed.loop_id,
                "aggregateStatus": parsed.aggregate_status,
                "children": len(parsed.child_verdicts),
                "retrySchedules": len(parsed.retry_schedule_refs),
                "acceptedEvidenceRefCount": len(
                    parsed.accepted_child_evidence_refs_for_assembly
                ),
            },
        )
    ]
    for index, child in enumerate(parsed.child_verdicts[:_MAX_CHILD_EVENTS]):
        events.append(_child_verdict_rule_event(parsed, child, index))
        trace = _child_verdict_trace_event(parsed, child, index)
        if trace is not None:
            events.append(trace)
    return _bounded(tuple(events), max_events=max_events)


def project_meta_final_assembly_events(
    assembly: MetaFinalAssemblyPlan,
    *,
    max_events: int = _MAX_EVENTS,
) -> tuple[PublicEvent, ...]:
    """Project final assembly progress without exposing child evidence refs."""

    parsed = _parse_assembly(assembly)
    phase = "verifying" if parsed.projection_mode == "ready_for_projection" else "aborted"
    turn_id = _digest_id("meta-assembly", parsed.final_output_digest)
    events: list[PublicEvent] = [
        turn_phase_event(
            turn_id=turn_id,
            phase=phase,
        )
    ]
    if parsed.projection_mode != "ready_for_projection":
        events.append(
            runtime_trace_event(
                turn_id=turn_id,
                phase="verifier_blocked",
                severity="warning",
                title="Meta final assembly blocked",
                detail=(
                    "final_assembly "
                    f"status={parsed.projection_mode} "
                    f"acceptedEvidenceRefCount={len(parsed.accepted_child_evidence_refs)} "
                    f"excludedChildCount={len(parsed.excluded_child_refs)} "
                    f"requiredVerifierCount={len(parsed.required_verifier_refs)}"
                ),
            )
        )
    return _bounded(tuple(events), max_events=max_events)


def project_meta_before_commit_event(
    verdict: MetaBeforeCommitVerdict,
) -> PublicEvent:
    """Project the beforeCommit verdict as a public rule check only."""

    parsed = _parse_before_commit(verdict)
    public = parsed.public_projection()
    rule_verdict = "ok" if parsed.verifier_chain_result == "passed" else "violation"
    return _rule_check_with_evidence(
        rule_id=_rule_id("before-commit", parsed.verdict_id),
        verdict=rule_verdict,
        detail=(
            "before_commit "
            f"verifierStatus={parsed.verifier_chain_result} "
            f"verifierResultCount={public['verifierResultRefCount']} "
            f"blockedReasonCount={len(parsed.blocked_reasons)} "
            f"retryableReasonCount={len(parsed.retryable_reasons)} "
            f"finalProjectionEligible={str(parsed.final_projection_eligible).lower()} "
            "commitExecuted=false transcriptWritten=false sseWritten=false "
            "controlWritten=false toolExecutionAttached=false"
        ),
        evidence_kind="before-commit",
        evidence_seed={
            "verdictId": parsed.verdict_id,
            "verifierStatus": parsed.verifier_chain_result,
            "verifierResultCount": public["verifierResultRefCount"],
            "blockedReasonCount": len(parsed.blocked_reasons),
            "retryableReasonCount": len(parsed.retryable_reasons),
            "finalProjectionEligible": parsed.final_projection_eligible,
            "commitExecuted": False,
            "transcriptWritten": False,
            "sseWritten": False,
            "controlWritten": False,
            "toolExecutionAttached": False,
        },
    )


def _parse_plan(plan: MetaTaskPlan) -> MetaTaskPlan:
    if not isinstance(plan, MetaTaskPlan):
        raise ValueError("meta plan event projection requires a MetaTaskPlan")
    return MetaTaskPlan.model_validate(plan.model_dump(by_alias=True, mode="python"))


def _parse_inspection(inspection: MetaInspectionLoopResult) -> MetaInspectionLoopResult:
    if not isinstance(inspection, MetaInspectionLoopResult):
        raise ValueError("meta inspection event projection requires a MetaInspectionLoopResult")
    inspection.public_projection()
    return MetaInspectionLoopResult.model_validate(
        {
            "loopId": inspection.loop_id,
            "childVerdicts": inspection.child_verdicts,
            "aggregateStatus": inspection.aggregate_status,
            "retryScheduleRefs": inspection.retry_schedule_refs,
            "exhaustedRetryReasons": inspection.exhausted_retry_reasons,
            "acceptedChildEvidenceRefsForAssembly": (
                inspection.accepted_child_evidence_refs_for_assembly
            ),
            "parentExecutedChildTools": inspection.parent_executed_child_tools,
            "defaultOff": inspection.default_off,
        }
    )


def _parse_assembly(assembly: MetaFinalAssemblyPlan) -> MetaFinalAssemblyPlan:
    if not isinstance(assembly, MetaFinalAssemblyPlan):
        raise ValueError("meta final assembly event projection requires a MetaFinalAssemblyPlan")
    assembly.public_projection()
    return assembly


def _parse_before_commit(verdict: MetaBeforeCommitVerdict) -> MetaBeforeCommitVerdict:
    if not isinstance(verdict, MetaBeforeCommitVerdict):
        raise ValueError("meta beforeCommit event projection requires a MetaBeforeCommitVerdict")
    verdict.public_projection()
    return verdict


def _plan_tasks(plan: MetaTaskPlan, *, plan_digest: str) -> tuple[Mapping[str, object], ...]:
    tasks: list[Mapping[str, object]] = []
    for index, child in enumerate(plan.child_task_specs):
        tasks.append(
            {
                "id": _task_public_id(plan, child, index),
                "title": f"Child task {index + 1}",
                "description": (
                    f"planDigest={plan_digest} delivery={child.delivery_mode} "
                    "evidenceEnvelopeRequired="
                    f"{str(child.requires_evidence_envelope).lower()}"
                ),
                "status": "pending",
                "parallelGroup": _digest_id("meta-plan", plan_digest),
            }
        )
    return tuple(tasks)


def _child_at(plan: MetaTaskPlan, task_index: int) -> MetaChildTaskSpec:
    if (
        isinstance(task_index, bool)
        or not isinstance(task_index, int)
        or task_index < 0
        or task_index >= len(plan.child_task_specs)
    ):
        raise ValueError("task_index must identify a planned child task")
    return plan.child_task_specs[task_index]


def _child_verdict_rule_event(
    inspection: MetaInspectionLoopResult,
    child: MetaInspectedChildVerdict,
    index: int,
) -> PublicEvent:
    status = child.verdict.status
    return _rule_check_with_evidence(
        rule_id=_rule_id("child-verdict", inspection.loop_id, str(index)),
        verdict=_child_rule_verdict(status),
        detail=(
            "child_acceptance "
            f"status={status} "
            f"reason={_reason_summary(child.verdict.reason_codes)} "
            f"attempt={child.attempt} "
            f"acceptedRefCount={len(child.verdict.accepted_evidence_refs)} "
            f"missingRefCount={len(child.verdict.missing_evidence_refs)} "
            f"retryBudgetRemaining={child.verdict.retry_budget_remaining}"
        ),
        evidence_kind="child-verdict",
        evidence_seed={
            "loopId": inspection.loop_id,
            "index": index,
            "status": status,
            "reason": tuple(child.verdict.reason_codes),
            "attempt": child.attempt,
            "acceptedRefCount": len(child.verdict.accepted_evidence_refs),
            "missingRefCount": len(child.verdict.missing_evidence_refs),
            "retryBudgetRemaining": child.verdict.retry_budget_remaining,
        },
    )


def _child_verdict_trace_event(
    inspection: MetaInspectionLoopResult,
    child: MetaInspectedChildVerdict,
    index: int,
) -> PublicEvent | None:
    status = child.verdict.status
    if status == "retry":
        return runtime_trace_event(
            turn_id=_turn_id("inspection", inspection.loop_id),
            phase="retry_scheduled",
            severity="warning",
            title="Meta child retry scheduled",
            detail=(
                "child_acceptance "
                f"status=retry reason={_reason_summary(child.verdict.reason_codes)} "
                f"attempt={child.attempt + 1} "
                f"acceptedRefCount={len(child.verdict.accepted_evidence_refs)} "
                f"missingRefCount={len(child.verdict.missing_evidence_refs)} "
                f"retryBudgetRemaining={child.verdict.retry_budget_remaining}"
            ),
            attempt=child.attempt + 1,
            max_attempts=child.attempt + child.verdict.retry_budget_remaining + 1,
            retryable=True,
        )
    if status in {"rejected", "blocked"}:
        return runtime_trace_event(
            turn_id=_turn_id("inspection", inspection.loop_id),
            phase="terminal_abort",
            severity="error" if status == "rejected" else "warning",
            title="Meta child result blocked",
            detail=(
                "child_acceptance "
                f"status={status} reason={_reason_summary(child.verdict.reason_codes)} "
                f"attempt={child.attempt} "
                f"acceptedRefCount={len(child.verdict.accepted_evidence_refs)} "
                f"missingRefCount={len(child.verdict.missing_evidence_refs)}"
            ),
            retryable=False,
        )
    return None


def _aggregate_rule_verdict(inspection: MetaInspectionLoopResult) -> RuleVerdict:
    if inspection.aggregate_status == "complete":
        return "ok"
    if inspection.aggregate_status == "needs_retry":
        return "pending"
    return "violation"


def _child_rule_verdict(status: str) -> RuleVerdict:
    if status == "accepted":
        return "ok"
    if status == "retry":
        return "pending"
    return "violation"


def _reason_summary(reason_codes: Sequence[str]) -> str:
    return "+".join(reason_codes[:4]) or "none"


def _rule_check_with_evidence(
    *,
    rule_id: str,
    verdict: RuleVerdict,
    detail: str,
    evidence_kind: str,
    evidence_seed: object,
) -> PublicEvent:
    event = rule_check_event(rule_id=rule_id, verdict=verdict, detail=detail)
    event["evidenceRef"] = _projection_evidence_ref(evidence_kind, evidence_seed)
    if verdict != "pending":
        authorize_rule_check_event(event)
    return event


def _projection_evidence_ref(kind: str, value: object) -> str:
    return _canonical_digest({"kind": kind, "value": value})


def _validate_receipt_or_evidence_ref(value: str, field_name: str) -> str:
    safe_ref = _validate_public_ref(value, field_name)
    if _PUBLIC_RECEIPT_OR_EVIDENCE_REF_RE.fullmatch(safe_ref) is not None:
        return safe_ref
    raise ValueError(f"{field_name} must be a runtime receipt or evidence ref")


def _task_public_id(plan: MetaTaskPlan, child: MetaChildTaskSpec, index: int) -> str:
    return _digest_id("meta-child", plan.plan_id, child.task_id, str(index))


def _rule_id(*parts: str) -> str:
    return _digest_id("meta-rule", *parts)


def _turn_id(kind: str, value: str) -> str:
    return _digest_id(f"meta-{kind}", value)


def _digest_id(prefix: str, *values: str) -> str:
    digest = _canonical_digest({"prefix": prefix, "values": values})[len("sha256:") : 39]
    return f"{prefix}:{digest}"


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bounded(events: Sequence[PublicEvent], *, max_events: int) -> tuple[PublicEvent, ...]:
    if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
        raise ValueError("max_events must be a positive integer")
    return tuple(events[: min(max_events, _MAX_EVENTS)])


__all__ = [
    "project_meta_before_commit_event",
    "project_meta_child_task_scheduled_event",
    "project_meta_final_assembly_events",
    "project_meta_parent_inspection_events",
    "project_meta_plan_created_events",
]
