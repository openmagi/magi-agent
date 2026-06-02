from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Literal

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    project_child_runtime_envelope,
)
from magi_agent.runtime.child_runner_boundary import (
    ChildRunnerResult,
    ChildTaskRequest,
)
from magi_agent.transport.tool_preview import sanitize_tool_preview


PublicChildEvent = dict[str, object]

_MAX_EVENTS = 12
_TEXT_LIMIT = 240
_DETAIL_LIMIT = 400
_ID_LIMIT = 120
_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_PUBLIC_RECEIPT_REF_RE = re.compile(r"^(?:receipt:)?sha256:[a-fA-F0-9]{64}$")
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users|/home|/workspace|/data/bots|/var/lib/kubelet|/private|/root)"
    r"(?:/[^\s\"',}]+)*|"
    r"[A-Za-z]:[\\/][^\s\"',}]+|"
    r"\\\\[^\s\"',}]+",
    re.IGNORECASE,
)
_SOURCE_LOCATOR_RE = re.compile(
    r"(?:"
    r"\b(?:https?|s3|gs|file|ssh|git)://[^\s\"',}]+|"
    r"\bgit@[A-Za-z0-9_.-]+:[^\s\"',}]+|"
    r"(?<![A-Za-z0-9])(?:search|source|ref):[^\s\"',}]+"
    r")",
    re.IGNORECASE,
)
_RAW_PRIVATE_RE = re.compile(
    r"raw[_ -]?(?:child|tool|prompt|transcript|payload|output|result|log|args)|"
    r"child[_ -]?(?:prompt|transcript|output|args?|logs?)|"
    r"tool[_ -]?(?:prompt|transcript|output|args?|result|logs?)|"
    r"hidden[_ -]?reasoning|chain[_ -]?of[_ -]?thought|private[_ -]?reasoning|"
    r"authorization|cookie|set-cookie",
    re.IGNORECASE,
)


def project_child_runtime_envelope_events(
    envelope: ChildRuntimeEnvelope,
    *,
    receipt_ref: str | None = None,
    max_events: int = _MAX_EVENTS,
) -> tuple[PublicChildEvent, ...]:
    """Project a runtime-issued child envelope into public Work Console events."""

    parsed = _revalidate_runtime_envelope(envelope)
    projection = project_child_runtime_envelope(parsed)
    task_id = _safe_id(projection.task_id)
    receipt = _required_receipt(receipt_ref or parsed.ledger_ref.ledger_id)
    persona = _safe_text(parsed.task.persona)
    deliver = _safe_text(parsed.task.deliver)
    role_detail = (
        f"role={_safe_text(projection.role)} "
        f"spawnDepth={projection.spawn_depth} receipt={receipt}"
    )
    progress_detail = (
        f"envelopeStatus={_safe_text(projection.status)} mode={_safe_text(projection.mode)} "
        f"auditRefs={len(projection.audit_event_refs)} receipt={receipt}"
    )

    events: list[PublicChildEvent] = [
        _spawn_started_event(
            task_id=task_id,
            persona=persona,
            deliver=deliver,
            detail=f"scheduled {role_detail}",
        )
    ]
    events.append(
        _child_progress_event(
            task_id=task_id,
            detail=f"scheduled {progress_detail}",
            child_receipt_ref=receipt,
        )
    )
    return _bounded_events(events, max_events=max_events)


def project_child_runner_result_events(
    request: ChildTaskRequest,
    result: ChildRunnerResult,
    *,
    max_events: int = _MAX_EVENTS,
) -> tuple[PublicChildEvent, ...]:
    """Project local child-runner boundary receipts without trusting child text."""

    parsed_request = ChildTaskRequest.model_validate(
        request.model_dump(by_alias=True, mode="python", warnings=False)
    )
    parsed_result = ChildRunnerResult.model_validate(
        result.model_dump(by_alias=True, mode="python", warnings=False)
    )
    if parsed_result.task_id != parsed_request.task_id:
        raise ValueError("child runner result taskId must match request taskId")
    if parsed_result.status != "ok":
        if parsed_result.envelope is not None:
            raise ValueError("child runner result status must be ok when envelope is present")
        return ()

    projection = parsed_result.public_projection()
    envelope = projection.get("childEnvelope")
    if not isinstance(envelope, Mapping):
        return ()
    _validate_runner_envelope_matches_request(envelope, parsed_request)

    status = _safe_text(str(envelope.get("status") or "failed"))
    task_id = _safe_id(str(envelope["taskId"]))
    receipt = _required_receipt(str(envelope.get("childRef") or ""))
    parent_turn_id = _safe_optional_id(parsed_request.turn_id)
    evidence_count = _safe_len(envelope.get("evidenceRefs"))
    artifact_count = _safe_len(envelope.get("artifactRefs"))
    audit_count = _safe_len(envelope.get("auditEventRefs"))
    detail = (
        f"status={status} evidenceRefs={evidence_count} artifactRefs={artifact_count} "
        f"auditRefs={audit_count} receipt={receipt}"
    )

    events: list[PublicChildEvent] = [
        _spawn_started_event(
            task_id=task_id,
            persona=_safe_text(parsed_request.role),
            deliver=_safe_text(parsed_request.delivery),
            detail=f"scheduled receipt={receipt}",
        )
    ]
    if parsed_request.delivery == "background":
        events.append(
            _background_task_event(
                task_id=task_id,
                persona=_safe_text(parsed_request.role),
                status=_runner_background_status(status),
                detail=detail,
            )
        )
    if status in {"completed", "failed"}:
        events.append(
            _child_started_event(
                task_id=task_id,
                parent_turn_id=parent_turn_id,
                detail=f"receipt={receipt}",
                child_receipt_ref=receipt,
            )
        )
    events.append(_child_progress_event(task_id=task_id, detail=detail, child_receipt_ref=receipt))
    events.append(
        _spawn_result_event(
            task_id=task_id,
            status=_runner_spawn_result_status(status),
        )
    )
    if status == "completed":
        events.append(_child_completed_event(task_id=task_id, child_receipt_ref=receipt))
    elif status == "blocked":
        events.append(
            _child_cancelled_event(
                task_id=task_id,
                reason=f"child_blocked receipt={receipt}",
                child_receipt_ref=receipt,
            )
        )
    else:
        events.append(
            _child_failed_event(
                task_id=task_id,
                error_message=f"child_result failed receipt={receipt}",
                child_receipt_ref=receipt,
            )
        )
    return _bounded_events(events, max_events=max_events)


def project_child_acceptance_verdict_event(
    verdict: object,
    *,
    task_id: str,
    receipt_ref: str | None = None,
) -> PublicChildEvent:
    """Represent accept/retry/reject verdicts without raw child output."""

    projection = _public_acceptance_projection(verdict)
    safe_task_id = _safe_id(task_id)
    status = _safe_text(str(projection["status"]))
    accepted_refs = _safe_refs(projection.get("acceptedEvidenceRefs"))
    missing_refs = _safe_refs(projection.get("missingEvidenceRefs"))
    retry_remaining = int(projection.get("retryBudgetRemaining") or 0)
    receipt = _first_receipt(receipt_ref, (*accepted_refs, *missing_refs))
    reason = _reason_code(projection.get("reasonCodes"))

    if receipt is None:
        raise ValueError("child acceptance event requires a public receipt or evidence ref")
    if status == "accepted":
        accepted_receipt = _first_accepted_child_receipt_ref(accepted_refs)
        if accepted_receipt is None:
            raise ValueError("accepted child result event requires a public child receipt ref")
        return _child_progress_event(
            task_id=safe_task_id,
            detail=(
                "child_result status=accepted "
                f"acceptedRefs={len(accepted_refs)} missingRefs=0 "
                f"retryBudgetRemaining={retry_remaining} receipt={accepted_receipt}"
            ),
            child_receipt_ref=accepted_receipt,
        )
    if status == "retry":
        detail = (
            "child_result status=retry "
            f"acceptedRefs={len(accepted_refs)} missingRefs={len(missing_refs)} "
            f"retryBudgetRemaining={retry_remaining}"
        )
        detail = f"{detail} receipt={receipt}"
        return _child_progress_event(
            task_id=safe_task_id,
            detail=detail,
            child_receipt_ref=receipt,
        )
    if status == "blocked":
        return _child_cancelled_event(
            task_id=safe_task_id,
            reason=f"child_result blocked reason={reason}",
            child_receipt_ref=receipt,
        )
    return _child_failed_event(
        task_id=safe_task_id,
        error_message=f"child_result rejected reason={reason}",
        child_receipt_ref=receipt,
    )


def _revalidate_runtime_envelope(envelope: ChildRuntimeEnvelope) -> ChildRuntimeEnvelope:
    if not isinstance(envelope, ChildRuntimeEnvelope) or not envelope.is_runtime_boundary_issued:
        raise ValueError("child runtime envelope must be runtime-issued")
    return envelope


def _validate_runner_envelope_matches_request(
    envelope: Mapping[str, object],
    request: ChildTaskRequest,
) -> None:
    if envelope.get("parentExecutionId") != request.parent_execution_id:
        raise ValueError("child runner envelope parentExecutionId must match request")
    if envelope.get("taskId") != request.task_id:
        raise ValueError("child runner envelope taskId must match request")
    if not isinstance(envelope.get("childExecutionId"), str):
        raise ValueError("child runner envelope requires childExecutionId receipt")


def _public_acceptance_projection(verdict: object) -> Mapping[str, object]:
    verdict_type = type(verdict)
    if (
        verdict_type.__name__ != "ChildAcceptanceVerdict"
        or not verdict_type.__module__.endswith(".child_acceptance")
    ):
        raise ValueError("child acceptance event requires evaluated child acceptance verdict")
    public_projection = getattr(verdict, "public_projection", None)
    if not callable(public_projection):
        raise ValueError("child acceptance verdict must expose public_projection")
    projection = public_projection()
    if not isinstance(projection, Mapping):
        raise ValueError("child acceptance public_projection must return a mapping")
    status = projection.get("status")
    if status not in {"accepted", "retry", "rejected", "blocked"}:
        raise ValueError("child acceptance projection has invalid status")
    return projection


def _spawn_started_event(
    *,
    task_id: str,
    persona: str,
    deliver: str,
    detail: str,
) -> PublicChildEvent:
    return {
        "type": "spawn_started",
        "taskId": task_id,
        "persona": _safe_text(persona),
        "deliver": _safe_text(deliver),
        "detail": _safe_text(detail, limit=_DETAIL_LIMIT),
    }


def _spawn_result_event(*, task_id: str, status: str) -> PublicChildEvent:
    return {
        "type": "spawn_result",
        "taskId": task_id,
        "status": _safe_text(status),
        "toolCallCount": 0,
    }


def _background_task_event(
    *,
    task_id: str,
    persona: str,
    status: str,
    detail: str,
) -> PublicChildEvent:
    return {
        "type": "background_task",
        "taskId": task_id,
        "persona": _safe_text(persona),
        "status": _safe_text(status),
        "detail": _safe_text(detail, limit=_DETAIL_LIMIT),
    }


def _child_started_event(
    *,
    task_id: str,
    parent_turn_id: str | None,
    detail: str,
    child_receipt_ref: str | None = None,
) -> PublicChildEvent:
    event: PublicChildEvent = {
        "type": "child_started",
        "taskId": task_id,
        "detail": _safe_text(detail, limit=_DETAIL_LIMIT),
    }
    if child_receipt_ref is not None:
        event["childReceiptRef"] = _public_child_receipt_ref(child_receipt_ref)
    if parent_turn_id is not None:
        event["parentTurnId"] = parent_turn_id
    return event


def _child_progress_event(
    *,
    task_id: str,
    detail: str,
    child_receipt_ref: str,
) -> PublicChildEvent:
    return {
        "type": "child_progress",
        "taskId": task_id,
        "detail": _safe_text(detail, limit=_DETAIL_LIMIT),
        "childReceiptRef": _public_child_receipt_ref(child_receipt_ref),
    }


def _child_completed_event(
    *,
    task_id: str,
    child_receipt_ref: str | None = None,
) -> PublicChildEvent:
    event: PublicChildEvent = {"type": "child_completed", "taskId": task_id}
    if child_receipt_ref is not None:
        event["childReceiptRef"] = _public_child_receipt_ref(child_receipt_ref)
    return event


def _child_cancelled_event(
    *,
    task_id: str,
    reason: str,
    child_receipt_ref: str,
) -> PublicChildEvent:
    return {
        "type": "child_cancelled",
        "taskId": task_id,
        "reason": _safe_text(reason, limit=_DETAIL_LIMIT),
        "childReceiptRef": _public_child_receipt_ref(child_receipt_ref),
    }


def _child_failed_event(
    *,
    task_id: str,
    error_message: str,
    child_receipt_ref: str,
) -> PublicChildEvent:
    return {
        "type": "child_failed",
        "taskId": task_id,
        "errorMessage": _safe_text(error_message, limit=_DETAIL_LIMIT),
        "childReceiptRef": _public_child_receipt_ref(child_receipt_ref),
    }


def _runner_spawn_result_status(status: str) -> Literal["ok", "error", "aborted"]:
    if status == "completed":
        return "ok"
    if status == "blocked":
        return "aborted"
    return "error"


def _runner_background_status(status: str) -> str:
    if status == "completed":
        return "completed"
    if status == "blocked":
        return "aborted"
    return "failed"


def _bounded_events(
    events: Sequence[PublicChildEvent],
    *,
    max_events: int,
) -> tuple[PublicChildEvent, ...]:
    if isinstance(max_events, bool) or not isinstance(max_events, int) or max_events < 1:
        raise ValueError("max_events must be a positive integer")
    return tuple(events[: min(max_events, _MAX_EVENTS)])


def _safe_refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    refs: list[str] = []
    for item in value[:25]:
        if not isinstance(item, str):
            continue
        safe = _safe_optional_ref(item)
        if safe is not None:
            refs.append(safe)
    return tuple(dict.fromkeys(refs))


def _first_receipt(explicit_ref: str | None, refs: Sequence[str]) -> str | None:
    explicit = _safe_optional_ref(explicit_ref)
    if explicit is not None:
        return _public_child_receipt_ref(explicit)
    for ref in refs:
        safe = _safe_optional_ref(ref)
        if safe is not None:
            return _public_child_receipt_ref(safe)
    return None


def _first_accepted_child_receipt_ref(refs: Sequence[str]) -> str | None:
    for ref in refs:
        safe = _safe_optional_ref(ref)
        if safe is not None and safe.startswith("receipt:"):
            return _public_child_receipt_ref(safe)
    return _first_receipt(None, refs)


def _required_receipt(value: str) -> str:
    receipt = _safe_optional_ref(value)
    if receipt is None:
        raise ValueError("child event projection requires a public runtime receipt ref")
    return _public_child_receipt_ref(receipt)


def _public_child_receipt_ref(value: str) -> str:
    if _PUBLIC_RECEIPT_REF_RE.fullmatch(value) is not None:
        return value if value.startswith("receipt:") else f"receipt:{value}"
    return "receipt:sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_optional_ref(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    safe = _safe_text(value, limit=_ID_LIMIT)
    if _is_unsafe_text(safe):
        return None
    if _PUBLIC_ID_RE.fullmatch(safe) is None:
        return None
    return safe


def _safe_optional_id(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return _safe_id(value)


def _safe_id(value: str) -> str:
    safe = _safe_text(value, limit=_ID_LIMIT)
    return f"child:{_digest(value)}"


def _safe_len(value: object) -> int:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return min(len(value), 25)
    return 0


def _safe_text(value: object, *, limit: int = _TEXT_LIMIT) -> str:
    text = sanitize_tool_preview(str(value))
    clean_lines = [line for line in text.splitlines() if not _RAW_PRIVATE_RE.search(line)]
    clean = "\n".join(clean_lines)
    clean = _PRIVATE_PATH_RE.sub("[redacted-path]", clean)
    clean = _SOURCE_LOCATOR_RE.sub("[redacted-source]", clean)
    if _RAW_PRIVATE_RE.search(clean):
        clean = "[redacted-private]"
    if len(clean) > limit:
        return f"{clean[: limit - 3]}..."
    return clean


def _is_unsafe_text(value: str) -> bool:
    return (
        "[redacted-private]" in value
        or _RAW_PRIVATE_RE.search(value) is not None
        or _PRIVATE_PATH_RE.search(value) is not None
        or _SOURCE_LOCATOR_RE.search(value) is not None
    )


def _reason_code(value: object) -> str:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        safe_reasons = [_safe_id(str(item)) for item in value[:5] if isinstance(item, str)]
        if safe_reasons:
            return ",".join(safe_reasons)
    return "unknown"


def _digest(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "PublicChildEvent",
    "project_child_acceptance_verdict_event",
    "project_child_runner_result_events",
    "project_child_runtime_envelope_events",
]
