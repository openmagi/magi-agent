from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
import hashlib
import json
from types import MappingProxyType
from typing import Any, Literal, NamedTuple, Self, get_args

from pydantic import BaseModel, Field, field_validator

from magi_agent.ops.authority import FalseOnlyAuthorityModel
from magi_agent.ops.safety import (
    require_digest,
    require_safe_ref,
    safe_metadata,
    serialize_safe_value,
)


GuardStage = Literal[
    "before_input_acceptance",
    "before_recipe_selection",
    "after_recipe_selection",
    "before_context_projection",
    "before_model_call",
    "after_model_call",
    "before_tool_call",
    "after_tool_call",
    "before_repair",
    "before_approval_request",
    "after_approval",
    "before_output_projection",
    "after_output_projection",
    "before_delivery",
    "after_delivery",
]
GuardVerdict = Literal["pass", "deny", "uncertain"]
GuardMode = Literal["enforce", "require_approval", "uncertain_fail_passthrough", "log_only", "disabled"]
AutoPermissionStatus = Literal[
    "disabled",
    "allowed",
    "denied",
    "approval_required",
    "uncertain_fail_passthrough",
    "blocked_invalid_policy",
]
SelfReviewRecommendation = Literal["allow", "deny", "require_approval"]
SelfReviewConfidence = Literal["low", "medium", "high"]
_GUARD_STAGES = set(get_args(GuardStage))
_GUARD_VERDICTS = set(get_args(GuardVerdict))
_GUARD_MODES = set(get_args(GuardMode))
_SELF_REVIEW_RECOMMENDATIONS = set(get_args(SelfReviewRecommendation))
_SELF_REVIEW_CONFIDENCES = set(get_args(SelfReviewConfidence))

_ZERO_DIGEST = "sha256:" + "0" * 64
_MUTATING_PERMISSION_MARKERS = (
    "write",
    "workspace",
    "mutate",
    "modify",
    "execute",
    "shell",
    "channel",
    "delivery",
    "deliver",
    "edit",
    "patch",
    "apply",
    "delete",
    "create",
    "update",
    "stop",
    "send",
    "commit",
    "push",
    "merge",
)


class _FrozenSafeMetadata(Mapping[str, object]):
    __slots__ = ("__value",)

    def __init__(self, value: Mapping[str, object]) -> None:
        object.__setattr__(self, "_FrozenSafeMetadata__value", MappingProxyType(dict(value)))

    def __setattr__(self, name: str, value: object) -> None:
        _ = name, value
        raise TypeError("safe metadata is immutable")

    def __delattr__(self, name: str) -> None:
        _ = name
        raise TypeError("safe metadata is immutable")

    def __getitem__(self, key: str) -> object:
        return self.__value[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.__value)

    def __len__(self) -> int:
        return len(self.__value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return False

    def __repr__(self) -> str:
        return repr(dict(self.__value))


class _SealedPermissionRecord:
    __slots__ = ("_sealed",)

    def __setattr__(self, name: str, value: object) -> None:
        if getattr(self, "_sealed", False):
            raise TypeError(f"{type(self).__name__} is immutable")
        object.__setattr__(self, name, value)

    def _seal(self) -> None:
        object.__setattr__(self, "_sealed", True)

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        raise ValueError(f"model_construct is disabled for {cls.__name__}")

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        if update:
            raise ValueError(f"model_copy update is disabled for {type(self).__name__}")
        _ = deep
        return self


def _read_alias(data: Mapping[str, object], alias: str, field_name: str | None = None) -> object:
    name = field_name or alias
    if alias in data:
        return data[alias]
    if name in data:
        return data[name]
    raise ValueError(f"{alias} is required")


def _read_alias_default(
    data: Mapping[str, object],
    alias: str,
    default: object,
    field_name: str | None = None,
) -> object:
    name = field_name or alias
    if alias in data:
        return data[alias]
    if name in data:
        return data[name]
    return default


class AutoPermissionConfig(FalseOnlyAuthorityModel):
    enabled: bool = False
    auto_allow_permission_refs: tuple[str, ...] = Field(default=(), alias="autoAllowPermissionRefs")
    forbidden_permission_refs: tuple[str, ...] = Field(default=(), alias="forbiddenPermissionRefs")
    frontend_admin_attached: Literal[False] = Field(
        default=False,
        alias="frontendAdminAttached",
    )
    production_policy_writes_enabled: Literal[False] = Field(
        default=False,
        alias="productionPolicyWritesEnabled",
    )
    route_attached: Literal[False] = Field(default=False, alias="routeAttached")

    @field_validator("auto_allow_permission_refs", "forbidden_permission_refs", mode="before")
    @classmethod
    def _coerce_refs(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, tuple | list):
            return tuple(str(item) for item in value)
        raise ValueError("permission refs must be arrays of strings")

    @field_validator("auto_allow_permission_refs", "forbidden_permission_refs")
    @classmethod
    def _validate_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _safe_ref_tuple(value, field_name="permission ref")


class AutoPermissionAuthorityFlags:
    __slots__ = ()

    def __setattr__(self, name: str, value: object) -> None:
        _ = name, value
        raise TypeError("AutoPermissionAuthorityFlags is immutable")

    def __delattr__(self, name: str) -> None:
        _ = name
        raise TypeError("AutoPermissionAuthorityFlags is immutable")

    @property
    def adk_callback_attached(self) -> Literal[False]:
        return False

    @property
    def tool_host_bypass_allowed(self) -> Literal[False]:
        return False

    @property
    def production_policy_write(self) -> Literal[False]:
        return False

    @property
    def frontend_admin_attached(self) -> Literal[False]:
        return False

    @property
    def user_visible_authority(self) -> Literal[False]:
        return False

    @property
    def route_attached(self) -> Literal[False]:
        return False

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(self, *, update: Mapping[str, Any] | None = None, deep: bool = False) -> Self:
        _ = update, deep
        return type(self)()

    def model_dump(self, *, by_alias: bool = False, **_: object) -> dict[str, bool]:
        if by_alias:
            return {
                "adkCallbackAttached": False,
                "toolHostBypassAllowed": False,
                "productionPolicyWrite": False,
                "frontendAdminAttached": False,
                "userVisibleAuthority": False,
                "routeAttached": False,
            }
        return {
            "adk_callback_attached": False,
            "tool_host_bypass_allowed": False,
            "production_policy_write": False,
            "frontend_admin_attached": False,
            "user_visible_authority": False,
            "route_attached": False,
        }


class AutoPermissionGuardDecision(_SealedPermissionRecord):
    __slots__ = (
        "_sealed",
        "guard_id",
        "stage",
        "hard_invariant",
        "deterministic_verdict",
        "configured_mode",
        "reason_codes",
        "evidence_refs",
    )

    def __init__(
        self,
        *,
        guardId: str | None = None,
        guard_id: str | None = None,
        stage: str,
        hardInvariant: bool | None = None,
        hard_invariant: bool | None = None,
        deterministicVerdict: str | None = None,
        deterministic_verdict: str | None = None,
        configuredMode: str | None = None,
        configured_mode: str | None = None,
        reasonCodes: object = (),
        reason_codes: object = (),
        evidenceRefs: object = (),
        evidence_refs: object = (),
    ) -> None:
        object.__setattr__(self, "_sealed", False)
        guard_ref = guardId if guardId is not None else guard_id
        hard_value = hardInvariant if hardInvariant is not None else hard_invariant
        verdict = deterministicVerdict if deterministicVerdict is not None else deterministic_verdict
        mode = configuredMode if configuredMode is not None else configured_mode
        if guard_ref is None or hard_value is None or verdict is None or mode is None:
            raise ValueError("guard decision requires guardId, hardInvariant, deterministicVerdict, and configuredMode")
        if stage not in _GUARD_STAGES:
            raise ValueError("stage must be a supported guard stage")
        if verdict not in _GUARD_VERDICTS:
            raise ValueError("deterministicVerdict must be a supported verdict")
        if mode not in _GUARD_MODES:
            raise ValueError("configuredMode must be a supported guard mode")
        refs = reasonCodes if reasonCodes != () else reason_codes
        evidence = evidenceRefs if evidenceRefs != () else evidence_refs
        self.guard_id = require_safe_ref(str(guard_ref), field_name="guardId")
        self.stage = stage
        self.hard_invariant = bool(hard_value)
        self.deterministic_verdict = verdict
        self.configured_mode = mode
        self.reason_codes = _safe_ref_tuple(_coerce_ref_tuple(refs), field_name="guard refs")
        self.evidence_refs = _safe_ref_tuple(_coerce_ref_tuple(evidence), field_name="guard refs")
        self._seal()

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        _ = args, kwargs
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, Mapping):
            raise ValueError("guard decision must be a mapping")
        return cls(
            guardId=str(_read_alias(obj, "guardId", "guard_id")),
            stage=str(_read_alias(obj, "stage")),
            hardInvariant=bool(_read_alias(obj, "hardInvariant", "hard_invariant")),
            deterministicVerdict=str(_read_alias(obj, "deterministicVerdict", "deterministic_verdict")),
            configuredMode=str(_read_alias(obj, "configuredMode", "configured_mode")),
            reasonCodes=_read_alias_default(obj, "reasonCodes", (), "reason_codes"),
            evidenceRefs=_read_alias_default(obj, "evidenceRefs", (), "evidence_refs"),
        )

    def model_dump(self, *, by_alias: bool = False, mode: str = "python", **_: object) -> dict[str, object]:
        _ = mode
        if by_alias:
            return {
                "guardId": self.guard_id,
                "stage": self.stage,
                "hardInvariant": self.hard_invariant,
                "deterministicVerdict": self.deterministic_verdict,
                "configuredMode": self.configured_mode,
                "reasonCodes": list(self.reason_codes),
                "evidenceRefs": list(self.evidence_refs),
            }
        return {
            "guard_id": self.guard_id,
            "stage": self.stage,
            "hard_invariant": self.hard_invariant,
            "deterministic_verdict": self.deterministic_verdict,
            "configured_mode": self.configured_mode,
            "reason_codes": self.reason_codes,
            "evidence_refs": self.evidence_refs,
        }


class AutoPermissionDecisionRequest(_SealedPermissionRecord):
    __slots__ = (
        "_sealed",
        "request_id",
        "action_ref",
        "action_digest",
        "requested_permission_refs",
        "policy_snapshot_digest",
        "guard_decisions",
        "admin_policy_ref",
        "admin_policy_digest",
        "metadata",
    )

    def __init__(
        self,
        *,
        requestId: str | None = None,
        request_id: str | None = None,
        actionRef: str | None = None,
        action_ref: str | None = None,
        actionDigest: str | None = None,
        action_digest: str | None = None,
        requestedPermissionRefs: object | None = None,
        requested_permission_refs: object | None = None,
        policySnapshotDigest: str | None = None,
        policy_snapshot_digest: str | None = None,
        guardDecisions: object | None = None,
        guard_decisions: object | None = None,
        adminPolicyRef: str | None = None,
        admin_policy_ref: str | None = None,
        adminPolicyDigest: str | None = None,
        admin_policy_digest: str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        object.__setattr__(self, "_sealed", False)
        request_ref = requestId if requestId is not None else request_id
        action = actionRef if actionRef is not None else action_ref
        action_hash = actionDigest if actionDigest is not None else action_digest
        permission_refs = requestedPermissionRefs if requestedPermissionRefs is not None else requested_permission_refs
        policy_hash = policySnapshotDigest if policySnapshotDigest is not None else policy_snapshot_digest
        guards = guardDecisions if guardDecisions is not None else guard_decisions
        admin_ref = adminPolicyRef if adminPolicyRef is not None else admin_policy_ref
        admin_hash = adminPolicyDigest if adminPolicyDigest is not None else admin_policy_digest
        if None in (request_ref, action, action_hash, permission_refs, policy_hash, guards, admin_ref, admin_hash):
            raise ValueError("auto permission request is missing required fields")
        parsed_permission_refs = _safe_ref_tuple(
            _coerce_ref_tuple(permission_refs),
            field_name="permission ref",
        )
        if not parsed_permission_refs:
            raise ValueError("requestedPermissionRefs must be non-empty")
        parsed_guards = tuple(AutoPermissionGuardDecision.model_validate(item) for item in _coerce_object_tuple(guards))
        if not parsed_guards:
            raise ValueError("guardDecisions must be non-empty")
        self.request_id = require_safe_ref(str(request_ref), field_name="auto permission ref")
        self.action_ref = require_safe_ref(str(action), field_name="auto permission ref")
        self.action_digest = require_digest(str(action_hash))
        self.requested_permission_refs = parsed_permission_refs
        self.policy_snapshot_digest = require_digest(str(policy_hash))
        self.guard_decisions = parsed_guards
        self.admin_policy_ref = require_safe_ref(str(admin_ref), field_name="auto permission ref")
        self.admin_policy_digest = require_digest(str(admin_hash))
        self.metadata = _FrozenSafeMetadata(safe_metadata(metadata or {}))
        self._seal()

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        _ = args, kwargs
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, Mapping):
            raise ValueError("auto permission request must be a mapping")
        return cls(
            requestId=str(_read_alias(obj, "requestId", "request_id")),
            actionRef=str(_read_alias(obj, "actionRef", "action_ref")),
            actionDigest=str(_read_alias(obj, "actionDigest", "action_digest")),
            requestedPermissionRefs=_read_alias(obj, "requestedPermissionRefs", "requested_permission_refs"),
            policySnapshotDigest=str(_read_alias(obj, "policySnapshotDigest", "policy_snapshot_digest")),
            guardDecisions=_read_alias(obj, "guardDecisions", "guard_decisions"),
            adminPolicyRef=str(_read_alias(obj, "adminPolicyRef", "admin_policy_ref")),
            adminPolicyDigest=str(_read_alias(obj, "adminPolicyDigest", "admin_policy_digest")),
            metadata=_read_alias_default(obj, "metadata", {}),
        )

    def model_dump(self, *, by_alias: bool = False, mode: str = "python", **_: object) -> dict[str, object]:
        metadata = _safe_json_metadata(self.metadata)
        if mode != "json":
            metadata = dict(self.metadata)
        if by_alias:
            return {
                "requestId": self.request_id,
                "actionRef": self.action_ref,
                "actionDigest": self.action_digest,
                "requestedPermissionRefs": list(self.requested_permission_refs),
                "policySnapshotDigest": self.policy_snapshot_digest,
                "guardDecisions": [
                    guard.model_dump(by_alias=True, mode=mode) for guard in self.guard_decisions
                ],
                "adminPolicyRef": self.admin_policy_ref,
                "adminPolicyDigest": self.admin_policy_digest,
                "metadata": metadata,
            }
        return {
            "request_id": self.request_id,
            "action_ref": self.action_ref,
            "action_digest": self.action_digest,
            "requested_permission_refs": self.requested_permission_refs,
            "policy_snapshot_digest": self.policy_snapshot_digest,
            "guard_decisions": self.guard_decisions,
            "admin_policy_ref": self.admin_policy_ref,
            "admin_policy_digest": self.admin_policy_digest,
            "metadata": metadata,
        }


class AutoPermissionSelfReviewRecord(_SealedPermissionRecord):
    __slots__ = (
        "_sealed",
        "review_id",
        "action_digest",
        "recommendation",
        "confidence",
        "reason_codes",
        "evidence_refs",
        "policy_snapshot_digest",
        "created_at",
        "metadata",
    )

    def __init__(
        self,
        *,
        reviewId: str | None = None,
        review_id: str | None = None,
        actionDigest: str | None = None,
        action_digest: str | None = None,
        recommendation: str,
        confidence: str,
        reasonCodes: object = (),
        reason_codes: object = (),
        evidenceRefs: object = (),
        evidence_refs: object = (),
        policySnapshotDigest: str | None = None,
        policy_snapshot_digest: str | None = None,
        createdAt: datetime | str | None = None,
        created_at: datetime | str | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        object.__setattr__(self, "_sealed", False)
        review_ref = reviewId if reviewId is not None else review_id
        action_hash = actionDigest if actionDigest is not None else action_digest
        policy_hash = policySnapshotDigest if policySnapshotDigest is not None else policy_snapshot_digest
        created = createdAt if createdAt is not None else created_at
        if review_ref is None or action_hash is None or policy_hash is None or created is None:
            raise ValueError("self review is missing required fields")
        if recommendation not in _SELF_REVIEW_RECOMMENDATIONS:
            raise ValueError("recommendation must be supported")
        if confidence not in _SELF_REVIEW_CONFIDENCES:
            raise ValueError("confidence must be supported")
        refs = reasonCodes if reasonCodes != () else reason_codes
        evidence = evidenceRefs if evidenceRefs != () else evidence_refs
        self.review_id = require_safe_ref(str(review_ref), field_name="reviewId")
        self.action_digest = require_digest(str(action_hash))
        self.recommendation = recommendation
        self.confidence = confidence
        self.reason_codes = _safe_ref_tuple(_coerce_ref_tuple(refs), field_name="self-review refs")
        self.evidence_refs = _safe_ref_tuple(_coerce_ref_tuple(evidence), field_name="self-review refs")
        self.policy_snapshot_digest = require_digest(str(policy_hash))
        self.created_at = _parse_datetime(created)
        self.metadata = _FrozenSafeMetadata(safe_metadata(metadata or {}))
        self._seal()

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        _ = args, kwargs
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, Mapping):
            raise ValueError("self review must be a mapping")
        return cls(
            reviewId=str(_read_alias(obj, "reviewId", "review_id")),
            actionDigest=str(_read_alias(obj, "actionDigest", "action_digest")),
            recommendation=str(_read_alias(obj, "recommendation")),
            confidence=str(_read_alias(obj, "confidence")),
            reasonCodes=_read_alias_default(obj, "reasonCodes", (), "reason_codes"),
            evidenceRefs=_read_alias_default(obj, "evidenceRefs", (), "evidence_refs"),
            policySnapshotDigest=str(_read_alias(obj, "policySnapshotDigest", "policy_snapshot_digest")),
            createdAt=_read_alias(obj, "createdAt", "created_at"),
            metadata=_read_alias_default(obj, "metadata", {}),
        )

    def model_dump(self, *, by_alias: bool = False, mode: str = "python", **_: object) -> dict[str, object]:
        metadata = _safe_json_metadata(self.metadata)
        if mode != "json":
            metadata = dict(self.metadata)
        if by_alias:
            return {
                "reviewId": self.review_id,
                "actionDigest": self.action_digest,
                "recommendation": self.recommendation,
                "confidence": self.confidence,
                "reasonCodes": list(self.reason_codes),
                "evidenceRefs": list(self.evidence_refs),
                "policySnapshotDigest": self.policy_snapshot_digest,
                "createdAt": _iso_z(self.created_at),
                "metadata": metadata,
            }
        return {
            "review_id": self.review_id,
            "action_digest": self.action_digest,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "reason_codes": self.reason_codes,
            "evidence_refs": self.evidence_refs,
            "policy_snapshot_digest": self.policy_snapshot_digest,
            "created_at": self.created_at,
            "metadata": metadata,
        }

    @property
    def review_digest(self) -> str:
        return _digest_json(
            {
                "reviewId": self.review_id,
                "actionDigest": self.action_digest,
                "recommendation": self.recommendation,
                "confidence": self.confidence,
                "reasonCodes": list(self.reason_codes),
                "evidenceRefs": list(self.evidence_refs),
                "policySnapshotDigest": self.policy_snapshot_digest,
                "createdAt": _iso_z(self.created_at),
                "metadata": _safe_json_metadata(self.metadata),
            }
        )


class _DecisionState(NamedTuple):
    status: AutoPermissionStatus
    allowed: bool
    requires_approval: bool
    request_id: str
    action_ref: str
    action_digest: str
    requested_permission_refs: tuple[str, ...]
    policy_snapshot_digest: str
    admin_policy_digest: str
    reason_codes: tuple[str, ...]
    guard_decision_digests: tuple[str, ...]
    self_review_digest: str | None
    decision_digest: str
    decided_at: datetime


class AutoPermissionDecision(_SealedPermissionRecord):
    __slots__ = ("_sealed", "__state")

    def __init__(self, **data: object) -> None:
        _ = data
        raise ValueError("AutoPermissionDecision must be issued by evaluate_auto_permission")

    @classmethod
    def model_validate(cls, obj: object, *args: object, **kwargs: object) -> Self:
        _ = args, kwargs
        if isinstance(obj, cls):
            obj._state()
            return obj
        raise ValueError("AutoPermissionDecision must be issued by evaluate_auto_permission")

    def _state(self) -> _DecisionState:
        try:
            return self.__state
        except AttributeError as exc:
            raise ValueError("AutoPermissionDecision must be issued by evaluate_auto_permission") from exc

    def model_dump(self, *, by_alias: bool = False, mode: str = "python", **_: object) -> dict[str, object]:
        _ = mode
        state = self._state()
        authority_error = _decision_state_authority_error(state)
        if authority_error is not None:
            raise ValueError(authority_error)
        if by_alias:
            return {
                "status": state.status,
                "allowed": state.allowed,
                "requiresApproval": state.requires_approval,
                "requestId": state.request_id,
                "actionRef": state.action_ref,
                "actionDigest": state.action_digest,
                "requestedPermissionRefs": list(state.requested_permission_refs),
                "policySnapshotDigest": state.policy_snapshot_digest,
                "adminPolicyDigest": state.admin_policy_digest,
                "reasonCodes": list(state.reason_codes),
                "guardDecisionDigests": list(state.guard_decision_digests),
                "selfReviewDigest": state.self_review_digest,
                "decisionDigest": state.decision_digest,
                "authorityFlags": AutoPermissionAuthorityFlags().model_dump(by_alias=True),
                "decidedAt": _iso_z(state.decided_at),
            }
        return {
            "status": state.status,
            "allowed": state.allowed,
            "requires_approval": state.requires_approval,
            "request_id": state.request_id,
            "action_ref": state.action_ref,
            "action_digest": state.action_digest,
            "requested_permission_refs": state.requested_permission_refs,
            "policy_snapshot_digest": state.policy_snapshot_digest,
            "admin_policy_digest": state.admin_policy_digest,
            "reason_codes": state.reason_codes,
            "guard_decision_digests": state.guard_decision_digests,
            "self_review_digest": state.self_review_digest,
            "decision_digest": state.decision_digest,
            "authority_flags": AutoPermissionAuthorityFlags(),
            "decided_at": state.decided_at,
        }

    @property
    def status(self) -> AutoPermissionStatus:
        return self._state().status

    @property
    def allowed(self) -> bool:
        return self._state().allowed

    @property
    def requires_approval(self) -> bool:
        return self._state().requires_approval

    @property
    def request_id(self) -> str:
        return self._state().request_id

    @property
    def action_ref(self) -> str:
        return self._state().action_ref

    @property
    def action_digest(self) -> str:
        return self._state().action_digest

    @property
    def requested_permission_refs(self) -> tuple[str, ...]:
        return self._state().requested_permission_refs

    @property
    def policy_snapshot_digest(self) -> str:
        return self._state().policy_snapshot_digest

    @property
    def admin_policy_digest(self) -> str:
        return self._state().admin_policy_digest

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return self._state().reason_codes

    @property
    def guard_decision_digests(self) -> tuple[str, ...]:
        return self._state().guard_decision_digests

    @property
    def self_review_digest(self) -> str | None:
        return self._state().self_review_digest

    @property
    def decision_digest(self) -> str:
        return self._state().decision_digest

    @property
    def decided_at(self) -> datetime:
        return self._state().decided_at

    @property
    def authority_flags(self) -> AutoPermissionAuthorityFlags:
        return AutoPermissionAuthorityFlags()

    def public_projection(self) -> dict[str, object]:
        authority_error = _decision_state_authority_error(self._state())
        if authority_error is not None:
            raise ValueError(authority_error)
        return self.model_dump(by_alias=True, mode="json")


def _decision(
    request: AutoPermissionDecisionRequest,
    *,
    status: AutoPermissionStatus,
    allowed: bool,
    requires_approval: bool,
    reason_codes: tuple[str, ...],
    self_review: AutoPermissionSelfReviewRecord | None,
    now: datetime | None,
) -> _DecisionState:
    guard_digests = tuple(_guard_digest(guard) for guard in request.guard_decisions)
    decided_at = now or datetime.now(UTC)
    self_review_digest = None if self_review is None else self_review.review_digest
    decision_digest = _digest_json(
        {
            "status": status,
            "allowed": allowed,
            "requiresApproval": requires_approval,
            "requestId": request.request_id,
            "actionRef": request.action_ref,
            "actionDigest": request.action_digest,
            "requestedPermissionRefs": list(request.requested_permission_refs),
            "policySnapshotDigest": request.policy_snapshot_digest,
            "adminPolicyDigest": request.admin_policy_digest,
            "reasonCodes": list(reason_codes),
            "guardDecisionDigests": list(guard_digests),
            "selfReviewDigest": self_review_digest,
            "decidedAt": _iso_z(decided_at),
        }
    )
    return _DecisionState(
        status=status,
        allowed=allowed,
        requires_approval=requires_approval,
        request_id=request.request_id,
        action_ref=request.action_ref,
        action_digest=request.action_digest,
        requested_permission_refs=request.requested_permission_refs,
        policy_snapshot_digest=request.policy_snapshot_digest,
        admin_policy_digest=request.admin_policy_digest,
        reason_codes=reason_codes,
        guard_decision_digests=guard_digests,
        self_review_digest=self_review_digest,
        decision_digest=decision_digest,
        decided_at=decided_at,
    )


def _hard_invariant_policy_error(
    guard_decisions: tuple[AutoPermissionGuardDecision, ...],
) -> str | None:
    for guard in guard_decisions:
        if guard.hard_invariant and guard.configured_mode in {"log_only", "disabled"}:
            return "hard_invariant_mode_downgrade"
    return None


def _is_mutating_permission(ref: str) -> bool:
    return _compact_permission_ref_has_mutating_marker(ref)


def _compact_permission_ref_has_mutating_marker(ref: str) -> bool:
    compact = "".join(character for character in ref.lower() if character.isalnum())
    return any(marker in compact for marker in _MUTATING_PERMISSION_MARKERS)


def evaluate_auto_permission(
    request: AutoPermissionDecisionRequest,
    *,
    config: AutoPermissionConfig | Mapping[str, object] | None = None,
    self_review: AutoPermissionSelfReviewRecord | None = None,
    now: datetime | None = None,
) -> AutoPermissionDecision:
    def finish(
        *,
        status: AutoPermissionStatus,
        allowed: bool,
        requires_approval: bool,
        reason_codes: tuple[str, ...],
    ) -> AutoPermissionDecision:
        guard_digests = tuple(_guard_digest(guard) for guard in request.guard_decisions)
        decided_at = now or datetime.now(UTC)
        self_review_digest = None if self_review is None else self_review.review_digest
        state = _DecisionState(
            status=status,
            allowed=allowed,
            requires_approval=requires_approval,
            request_id=request.request_id,
            action_ref=request.action_ref,
            action_digest=request.action_digest,
            requested_permission_refs=request.requested_permission_refs,
            policy_snapshot_digest=request.policy_snapshot_digest,
            admin_policy_digest=request.admin_policy_digest,
            reason_codes=reason_codes,
            guard_decision_digests=guard_digests,
            self_review_digest=self_review_digest,
            decision_digest=_ZERO_DIGEST,
            decided_at=decided_at,
        )
        state = state._replace(decision_digest=_decision_state_digest(state))
        decision = object.__new__(AutoPermissionDecision)
        object.__setattr__(decision, "_AutoPermissionDecision__state", state)
        object.__setattr__(decision, "_sealed", True)
        return decision

    parsed_config = AutoPermissionConfig.model_validate(config or {})
    if not parsed_config.enabled:
        return finish(
            status="disabled",
            allowed=False,
            requires_approval=False,
            reason_codes=("auto_permission_control_disabled",),
        )

    policy_error = None
    for guard in request.guard_decisions:
        if guard.hard_invariant and guard.configured_mode in {"log_only", "disabled"}:
            policy_error = "hard_invariant_mode_downgrade"
            break
    if policy_error is not None:
        return finish(
            status="blocked_invalid_policy",
            allowed=False,
            requires_approval=False,
            reason_codes=(policy_error,),
        )

    forbidden_refs = set(parsed_config.forbidden_permission_refs)
    if forbidden_refs.intersection(request.requested_permission_refs):
        return finish(
            status="denied",
            allowed=False,
            requires_approval=False,
            reason_codes=("forbidden_permission_ref",),
        )

    hard_denial = any(
        guard.deterministic_verdict == "deny"
        and (guard.hard_invariant or guard.configured_mode in {"enforce", "require_approval"})
        for guard in request.guard_decisions
    )
    if hard_denial:
        return finish(
            status="denied",
            allowed=False,
            requires_approval=False,
            reason_codes=(
                "hard_guard_denied"
                if any(guard.hard_invariant and guard.deterministic_verdict == "deny" for guard in request.guard_decisions)
                else "guard_denied",
            ),
        )

    uncertain_guards = [
        guard for guard in request.guard_decisions if guard.deterministic_verdict == "uncertain"
    ]
    if uncertain_guards:
        passthrough_only = all(
            not guard.hard_invariant and guard.configured_mode == "uncertain_fail_passthrough"
            for guard in uncertain_guards
        )
        if passthrough_only:
            return finish(
                status="uncertain_fail_passthrough",
                allowed=False,
                requires_approval=True,
                reason_codes=("non_hard_uncertain_fail_passthrough",),
            )
        return finish(
            status="approval_required",
            allowed=False,
            requires_approval=True,
            reason_codes=("uncertain_guard_requires_approval",),
        )

    if any(guard.configured_mode == "require_approval" for guard in request.guard_decisions):
        return finish(
            status="approval_required",
            allowed=False,
            requires_approval=True,
            reason_codes=("guard_requires_approval",),
        )

    if not set(request.requested_permission_refs) <= set(parsed_config.auto_allow_permission_refs):
        return finish(
            status="approval_required",
            allowed=False,
            requires_approval=True,
            reason_codes=("permission_requires_explicit_approval",),
        )

    authority_refs = (request.action_ref, *request.requested_permission_refs)
    if any(_compact_permission_ref_has_mutating_marker(ref) for ref in authority_refs):
        return finish(
            status="approval_required",
            allowed=False,
            requires_approval=True,
            reason_codes=("permission_requires_explicit_approval",),
        )

    return finish(
        status="allowed",
        allowed=True,
        requires_approval=False,
        reason_codes=("all_guards_passed",),
    )

def _guard_digest(guard: AutoPermissionGuardDecision) -> str:
    return _digest_json(
        {
            "guardId": guard.guard_id,
            "stage": guard.stage,
            "hardInvariant": guard.hard_invariant,
            "deterministicVerdict": guard.deterministic_verdict,
            "configuredMode": guard.configured_mode,
            "reasonCodes": list(guard.reason_codes),
            "evidenceRefs": list(guard.evidence_refs),
        }
    )


def _decision_state_digest(state: _DecisionState) -> str:
    return _digest_json(
        {
            "status": state.status,
            "allowed": state.allowed,
            "requiresApproval": state.requires_approval,
            "requestId": state.request_id,
            "actionRef": state.action_ref,
            "actionDigest": state.action_digest,
            "requestedPermissionRefs": list(state.requested_permission_refs),
            "policySnapshotDigest": state.policy_snapshot_digest,
            "adminPolicyDigest": state.admin_policy_digest,
            "reasonCodes": list(state.reason_codes),
            "guardDecisionDigests": list(state.guard_decision_digests),
            "selfReviewDigest": state.self_review_digest,
            "decidedAt": _iso_z(state.decided_at),
        }
    )


def _decision_state_authority_error(state: _DecisionState) -> str | None:
    if state.decision_digest == _ZERO_DIGEST or state.decision_digest != _decision_state_digest(state):
        return "decisionDigest does not match auto permission decision"
    if state.allowed:
        if state.status != "allowed":
            return "allowed decision must use allowed status"
        if state.requires_approval:
            return "allowed decision cannot require approval"
        if state.reason_codes != ("all_guards_passed",):
            return "allowed decision requires guard pass reason"
        authority_refs = (state.action_ref, *state.requested_permission_refs)
        if any(_compact_permission_ref_has_mutating_marker(ref) for ref in authority_refs):
            return "allowed decision cannot grant mutating permissions"
    if state.status == "allowed" and not state.allowed:
        return "allowed status requires allowed decision"
    if state.status in {"disabled", "denied", "blocked_invalid_policy"} and state.allowed:
        return "non-authorized status cannot allow permission"
    if state.status in {"approval_required", "uncertain_fail_passthrough"} and not state.requires_approval:
        return "approval status requires approval flag"
    return None


def _decision_digest(decision: AutoPermissionDecision) -> str:
    return _decision_state_digest(decision._state())


def _coerce_ref_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple | list):
        return tuple(str(item) for item in value)
    raise ValueError("refs must be arrays of strings")


def _coerce_object_tuple(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple | list):
        return tuple(value)
    raise ValueError("value must be an array")


def _parse_datetime(value: datetime | str | object) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    raise ValueError("datetime field must be a datetime or ISO timestamp")


def _safe_ref_tuple(value: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    refs = tuple(require_safe_ref(item, field_name=field_name) for item in value)
    if len(set(refs)) != len(refs):
        raise ValueError(f"{field_name} values must be unique")
    return refs


def _safe_json_metadata(value: Mapping[str, object]) -> dict[str, object]:
    return {
        key: serialize_safe_value(item)
        for key, item in safe_metadata(value).items()
    }


def _digest_json(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _iso_z(value: datetime) -> str:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")
