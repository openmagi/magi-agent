from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.missions.receipts import (
    MissionLifecycleAuthorityFlags,
    MissionLifecycleState,
    MissionTransitionReceipt,
    MissionTransitionStatus,
    canonical_digest,
    sanitize_public_id,
    sanitize_public_ref,
    sanitize_public_text,
    sanitize_reason_code,
    sha256_ref,
    string_tuple,
)


MISSION_LIFECYCLE_STATES: tuple[MissionLifecycleState, ...] = (
    "draft",
    "pending_approval",
    "scheduled",
    "running",
    "paused",
    "blocked",
    "completed",
    "failed",
    "cancelled",
)
ALLOWED_MISSION_TRANSITIONS: Mapping[MissionLifecycleState, frozenset[MissionLifecycleState]] = {
    "draft": frozenset({"pending_approval", "cancelled"}),
    "pending_approval": frozenset({"scheduled", "running", "blocked", "cancelled"}),
    "scheduled": frozenset({"running", "paused", "blocked", "cancelled"}),
    "running": frozenset({"paused", "blocked", "completed", "failed", "cancelled"}),
    "paused": frozenset({"running", "blocked", "cancelled"}),
    "blocked": frozenset({"running", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PRIVATE_REASON_CODES = frozenset({"private_mission_payload_denied"})


class MissionLifecycleConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_transition_enabled: bool = Field(
        default=False,
        alias="localFakeTransitionEnabled",
    )
    production_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionMutationEnabled",
    )
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    scheduler_attached: Literal[False] = Field(default=False, alias="schedulerAttached")
    cron_mutation_enabled: Literal[False] = Field(default=False, alias="cronMutationEnabled")
    background_execution_enabled: Literal[False] = Field(
        default=False,
        alias="backgroundExecutionEnabled",
    )
    tool_host_dispatch_enabled: Literal[False] = Field(
        default=False,
        alias="toolHostDispatchEnabled",
    )
    channel_delivery_enabled: Literal[False] = Field(
        default=False,
        alias="channelDeliveryEnabled",
    )
    workspace_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="workspaceMutationEnabled",
    )
    memory_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="memoryMutationEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionMutationEnabled"] = False
        payload["trafficAttached"] = False
        payload["schedulerAttached"] = False
        payload["cronMutationEnabled"] = False
        payload["backgroundExecutionEnabled"] = False
        payload["toolHostDispatchEnabled"] = False
        payload["channelDeliveryEnabled"] = False
        payload["workspaceMutationEnabled"] = False
        payload["memoryMutationEnabled"] = False
        for field_name in (
            "production_mutation_enabled",
            "traffic_attached",
            "scheduler_attached",
            "cron_mutation_enabled",
            "background_execution_enabled",
            "tool_host_dispatch_enabled",
            "channel_delivery_enabled",
            "workspace_mutation_enabled",
            "memory_mutation_enabled",
        ):
            payload.pop(field_name, None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_serializer(
        "production_mutation_enabled",
        "traffic_attached",
        "scheduler_attached",
        "cron_mutation_enabled",
        "background_execution_enabled",
        "tool_host_dispatch_enabled",
        "channel_delivery_enabled",
        "workspace_mutation_enabled",
        "memory_mutation_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    def authority_flags(self) -> MissionLifecycleAuthorityFlags:
        return MissionLifecycleAuthorityFlags()


class MissionLifecyclePolicy(BaseModel):
    model_config = _MODEL_CONFIG

    policy_ref: str = Field(alias="policyRef")
    policy_snapshot_ref: str = Field(alias="policySnapshotRef")
    local_fake_transition_allowed: bool = Field(
        default=False,
        alias="localFakeTransitionAllowed",
    )
    approval_required: bool = Field(default=False, alias="approvalRequired")
    evidence_required: bool = Field(default=True, alias="evidenceRequired")
    allowed_transitions: tuple[str, ...] = Field(default=(), alias="allowedTransitions")
    production_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="productionMutationEnabled",
    )
    scheduler_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="schedulerMutationAllowed",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_default_off_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["productionMutationEnabled"] = False
        payload["schedulerMutationAllowed"] = False
        payload.pop("production_mutation_enabled", None)
        payload.pop("scheduler_mutation_allowed", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("policy_ref", "policy_snapshot_ref", mode="before")
    @classmethod
    def _sanitize_refs(cls, value: object) -> str:
        return sanitize_public_ref(str(value or "policy:mission-lifecycle"))

    @field_validator("allowed_transitions", mode="before")
    @classmethod
    def _sanitize_allowed_transitions(cls, value: object) -> tuple[str, ...]:
        return tuple(_sanitize_transition_name(item) for item in string_tuple(value))

    @field_serializer("production_mutation_enabled", "scheduler_mutation_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False

    def permits_transition(
        self,
        from_state: MissionLifecycleState,
        to_state: MissionLifecycleState,
    ) -> bool:
        if not self.allowed_transitions:
            return True
        return f"{from_state}->{to_state}" in self.allowed_transitions


class MissionTransitionRequest(BaseModel):
    model_config = _MODEL_CONFIG

    mission_id: str = Field(alias="missionId")
    run_id: str = Field(alias="runId")
    turn_id: str = Field(alias="turnId")
    from_state: MissionLifecycleState = Field(alias="fromState")
    to_state: MissionLifecycleState = Field(alias="toState")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    approval_ref: str | None = Field(default=None, alias="approvalRef")
    reason: str | None = None
    now: int = Field(default=0, ge=0)
    private_mission_payload: bool = Field(default=False, alias="privateMissionPayload")
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True)
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True)
    child_prompt: str | None = Field(default=None, alias="childPrompt", exclude=True)

    @field_validator("mission_id", "run_id", "turn_id", mode="before")
    @classmethod
    def _sanitize_ids(cls, value: object) -> str:
        return sanitize_public_id(str(value or "mission-unspecified"), prefix="mission-id")

    @field_validator("evidence_refs", mode="before")
    @classmethod
    def _sanitize_evidence_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(sanitize_public_ref(item) for item in string_tuple(value))

    @field_validator("approval_ref", mode="before")
    @classmethod
    def _sanitize_approval_ref(cls, value: object) -> str | None:
        if value is None:
            return None
        return sanitize_public_ref(str(value))

    @field_validator("reason", mode="before")
    @classmethod
    def _sanitize_reason(cls, value: object) -> str | None:
        if value is None:
            return None
        clean = sanitize_public_text(str(value))
        return clean[:240] if clean else None

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)


class MissionTransitionResult(BaseModel):
    model_config = _MODEL_CONFIG

    status: MissionTransitionStatus
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    receipt: MissionTransitionReceipt
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    authority_flags: MissionLifecycleAuthorityFlags = Field(
        default_factory=MissionLifecycleAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_authority_flags(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["authorityFlags"] = MissionLifecycleAuthorityFlags()
        payload.pop("authority_flags", None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: object,
    ) -> Self:
        _ = _fields_set
        return cls.model_validate(values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        _ = deep
        return type(self).model_validate(payload)

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _sanitize_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(sanitize_reason_code(item) for item in string_tuple(value))

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reasonCodes": [sanitize_reason_code(code) for code in self.reason_codes],
            "receipt": self.receipt.public_projection(),
            "policySnapshotDigest": sanitize_public_ref(self.policy_snapshot_digest),
            "authorityFlags": MissionLifecycleAuthorityFlags().model_dump(by_alias=True),
        }


class MissionLifecycleStateMachine:
    """Default-off mission-owned lifecycle transition boundary."""

    def __init__(
        self,
        config: MissionLifecycleConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, MissionLifecycleConfig)
            else MissionLifecycleConfig.model_validate(config or {})
        )

    def transition(
        self,
        *,
        request: MissionTransitionRequest | Mapping[str, object],
        policy: MissionLifecyclePolicy | Mapping[str, object] | None,
    ) -> MissionTransitionResult:
        safe_request = MissionTransitionRequest.model_validate(request)
        safe_policy = (
            policy
            if isinstance(policy, MissionLifecyclePolicy)
            else MissionLifecyclePolicy.model_validate(policy)
            if policy is not None
            else None
        )
        policy_digest = _policy_snapshot_digest(safe_policy)

        if not self.config.enabled:
            return _result(
                status="disabled",
                reason_codes=("mission_lifecycle_disabled",),
                request=safe_request,
                policy=safe_policy,
                policy_snapshot_digest=policy_digest,
                transition_allowed=False,
                local_fake_recorded=False,
            )

        denial_status, denial_reasons = _transition_denial_reasons(safe_request, safe_policy)
        if denial_status is not None:
            return _result(
                status=denial_status,
                reason_codes=denial_reasons,
                request=safe_request,
                policy=safe_policy,
                policy_snapshot_digest=policy_digest,
                transition_allowed=False,
                local_fake_recorded=False,
            )

        if not self.config.local_fake_transition_enabled:
            return _result(
                status="blocked",
                reason_codes=("local_fake_mission_transition_disabled",),
                request=safe_request,
                policy=safe_policy,
                policy_snapshot_digest=policy_digest,
                transition_allowed=True,
                local_fake_recorded=False,
            )

        assert safe_policy is not None
        if not safe_policy.local_fake_transition_allowed:
            return _result(
                status="blocked",
                reason_codes=("mission_policy_disallows_local_fake_transition",),
                request=safe_request,
                policy=safe_policy,
                policy_snapshot_digest=policy_digest,
                transition_allowed=True,
                local_fake_recorded=False,
            )

        return _result(
            status="applied_local_fake",
            reason_codes=("local_fake_mission_transition_receipt",),
            request=safe_request,
            policy=safe_policy,
            policy_snapshot_digest=policy_digest,
            transition_allowed=True,
            local_fake_recorded=True,
        )


def _transition_denial_reasons(
    request: MissionTransitionRequest,
    policy: MissionLifecyclePolicy | None,
) -> tuple[MissionTransitionStatus | None, tuple[str, ...]]:
    if policy is None:
        return "blocked", ("missing_mission_lifecycle_policy",)
    if request.private_mission_payload:
        return "blocked", ("private_mission_payload_denied",)
    if request.to_state not in ALLOWED_MISSION_TRANSITIONS[request.from_state]:
        return "blocked", ("mission_transition_denied",)
    if not policy.permits_transition(request.from_state, request.to_state):
        return "blocked", ("mission_transition_not_allowed_by_policy",)
    if policy.evidence_required and not request.evidence_refs:
        return "blocked", ("missing_mission_transition_evidence",)
    if policy.approval_required and request.approval_ref is None:
        return "approval_required", ("missing_mission_transition_approval",)
    return None, ()


def _result(
    *,
    status: MissionTransitionStatus,
    reason_codes: Sequence[str],
    request: MissionTransitionRequest,
    policy: MissionLifecyclePolicy | None,
    policy_snapshot_digest: str,
    transition_allowed: bool,
    local_fake_recorded: bool,
) -> MissionTransitionResult:
    safe_reason_codes = tuple(sanitize_reason_code(item) for item in reason_codes)
    receipt = _receipt_for_transition(
        status=status,
        reason_codes=safe_reason_codes,
        request=request,
        policy=policy,
        policy_snapshot_digest=policy_snapshot_digest,
        transition_allowed=transition_allowed,
        local_fake_recorded=local_fake_recorded,
    )
    return MissionTransitionResult(
        status=status,
        reasonCodes=safe_reason_codes,
        receipt=receipt,
        policySnapshotDigest=policy_snapshot_digest,
        authorityFlags=MissionLifecycleAuthorityFlags(),
    )


def _receipt_for_transition(
    *,
    status: MissionTransitionStatus,
    reason_codes: tuple[str, ...],
    request: MissionTransitionRequest,
    policy: MissionLifecyclePolicy | None,
    policy_snapshot_digest: str,
    transition_allowed: bool,
    local_fake_recorded: bool,
) -> MissionTransitionReceipt:
    policy_snapshot_ref = (
        "policy-snapshot:absent"
        if policy is None
        else sanitize_public_ref(policy.policy_snapshot_ref)
    )
    reason_digest = sha256_ref(
        json.dumps(
            {
                "reason": request.reason,
                "reasonCodes": reason_codes,
                "privateMissionPayload": request.private_mission_payload,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    digest_payload = {
        "schemaVersion": "missionTransitionReceipt.v1",
        "missionId": request.mission_id,
        "runId": request.run_id,
        "turnId": request.turn_id,
        "fromState": request.from_state,
        "toState": request.to_state,
        "status": status,
        "transitionAllowed": transition_allowed,
        "localFakeTransitionRecorded": local_fake_recorded,
        "policySnapshotDigest": policy_snapshot_digest,
        "policySnapshotRef": policy_snapshot_ref,
        "evidenceRefs": request.evidence_refs,
        "approvalRef": request.approval_ref,
        "reasonDigest": reason_digest,
    }
    receipt_digest = canonical_digest(digest_payload)
    return MissionTransitionReceipt(
        receiptId=f"mission-transition:{receipt_digest[7:23]}",
        receiptDigest=receipt_digest,
        missionId=request.mission_id,
        runId=request.run_id,
        turnId=request.turn_id,
        fromState=request.from_state,
        toState=request.to_state,
        status=status,
        transitionAllowed=transition_allowed,
        localFakeTransitionRecorded=local_fake_recorded,
        localTestOnly=local_fake_recorded,
        policySnapshotDigest=policy_snapshot_digest,
        policySnapshotRef=policy_snapshot_ref,
        evidenceRefs=request.evidence_refs,
        approvalRef=request.approval_ref,
        reasonCodes=reason_codes,
        reasonDigest=reason_digest,
        authorityFlags=MissionLifecycleAuthorityFlags(),
    )


def _policy_snapshot_digest(policy: MissionLifecyclePolicy | None) -> str:
    payload = (
        {"policy": None}
        if policy is None
        else policy.model_dump(by_alias=True, mode="json", warnings=False)
    )
    return canonical_digest({"policy": payload})


def _sanitize_transition_name(value: str) -> str:
    raw = str(value).strip()
    if "->" not in raw:
        return sanitize_reason_code(raw)
    left, right = raw.split("->", 1)
    left_clean = sanitize_reason_code(left)
    right_clean = sanitize_reason_code(right)
    return f"{left_clean}->{right_clean}"


__all__ = [
    "ALLOWED_MISSION_TRANSITIONS",
    "MISSION_LIFECYCLE_STATES",
    "MissionLifecycleConfig",
    "MissionLifecyclePolicy",
    "MissionLifecycleStateMachine",
    "MissionTransitionRequest",
    "MissionTransitionResult",
]
