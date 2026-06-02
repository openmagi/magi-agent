from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import Any, Literal, Self
from weakref import finalize

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from magi_agent.evidence.child_runtime_envelope import (
    ChildRuntimeEnvelope,
    project_child_runtime_envelope,
)
from magi_agent.research.acceptance_criteria import (
    ResearchAcceptanceCriteriaSet,
    project_research_acceptance_criteria_set,
)
from magi_agent.research.action_claims import (
    ResearchActionProofVerdict,
    project_research_action_proof_verdicts,
)
from magi_agent.research.claim_graph import ResearchClaimSupportRef
from magi_agent.research.source_proof import (
    ResearchSourceProofVerdict,
    project_research_source_proof_verdicts,
)


ResearchChildRoleName = Literal[
    "research_searcher",
    "source_inspector",
    "claim_mapper",
    "research_verifier",
    "synthesis_reviewer",
]
ResearchChildProofKind = Literal[
    "source_proof",
    "claim_proof",
    "action_proof",
    "task_proof",
]
ResearchChildAdmissionDecisionKind = Literal["accept", "retry", "reject"]
ResearchChildAdmissionReasonCode = Literal[
    "child_evidence_accepted",
    "missing_required_child_proof",
    "child_raw_text_not_evidence",
    "child_raw_private_payload_rejected",
    "child_envelope_not_accepted",
    "child_proof_ref_invalid",
    "child_role_ref_missing",
    "non_research_child_envelope",
    "undeclared_child_tool_grant",
    "child_summary_not_evidence",
    "live_authority_attached",
]


RESEARCH_CHILD_ROLE_NAMES: tuple[ResearchChildRoleName, ...] = (
    "research_searcher",
    "source_inspector",
    "claim_mapper",
    "research_verifier",
    "synthesis_reviewer",
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="never",
    hide_input_in_errors=True,
)
_PUBLIC_REF_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{1,180}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_EVIDENCE_TYPE_RE = re.compile(
    r"^(?:[A-Z][A-Za-z0-9_]*|custom:[A-Z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*)$"
)
_SECRET_TEXT_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|gh[opusr]_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|AKIA[0-9A-Z]{12,}|ASIA[0-9A-Z]{12,}|"
    r"AIza[0-9A-Za-z_-]{12,}|xox[baprs]-[A-Za-z0-9-]{8,}|"
    r"sk-(?:live|test)?[-_A-Za-z0-9]{8,}|\b\d{5,}:[A-Za-z0-9_-]{8,}\b|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY|PASSWORD|COOKIE)[A-Z0-9_]*\s*[:=]\s*"
    r"[^,\s}{\n]{4,})",
    re.IGNORECASE,
)
_PRIVATE_PATH_RE = re.compile(
    r"(?:/Users/[^,\s\"']+|/home/[^,\s\"']+|/root/[^,\s\"']+|"
    r"/workspace/[^,\s\"']+|/data/bots/[^,\s\"']+|"
    r"/var/lib/kubelet/[^,\s\"']+|pvc-[A-Za-z0-9-]+)",
    re.IGNORECASE,
)
_UNSAFE_TEXT_RE = re.compile(
    r"https?://|file://|raw[_ -]?(?:source|transcript|tool|prompt|output|result|log|logs)|"
    r"source[_ -]?(?:body|content|html|text)|hidden[_ -]?reasoning|"
    r"chain[_ -]?of[_ -]?thought|authorization|cookie|set-cookie|"
    r"api[_ -]?key|secret|token|model[_ -]?summary|model[_ -]?generated[_ -]?summary",
    re.IGNORECASE,
)
_FORBIDDEN_TOOL_PARTS = frozenset(
    {
        "api",
        "auth",
        "browser",
        "channel",
        "cookie",
        "delete",
        "execute",
        "key",
        "log",
        "memory",
        "model",
        "mutate",
        "output",
        "path",
        "private",
        "prompt",
        "provider",
        "raw",
        "result",
        "secret",
        "token",
        "transcript",
        "web",
        "write",
    }
)
_ADK_USAGE_NOTES = (
    "Research child role metadata only; no ADK Runner, FunctionTool, provider call, "
    "browser use, model call, ToolHost execution, memory write, or channel delivery is attached."
)
_ROLE_TOOL_NAMES: dict[ResearchChildRoleName, tuple[str, ...]] = {
    "research_searcher": ("FixtureSearchIndexRead", "FixtureSearchMetadataRead"),
    "source_inspector": ("FixtureSourceMetadataRead", "FixtureSourceSnapshotRead"),
    "claim_mapper": ("FixtureClaimMapRead", "FixtureSourceSpanRead"),
    "research_verifier": (
        "FixtureActionProofRead",
        "FixtureClaimProofRead",
        "FixtureSourceProofRead",
    ),
    "synthesis_reviewer": (
        "FixtureAcceptanceCriteriaRead",
        "FixtureEvidenceGraphRead",
        "FixtureClaimGraphRead",
    ),
}
_ROLE_REQUIRED_PROOFS: dict[ResearchChildRoleName, tuple[ResearchChildProofKind, ...]] = {
    "research_searcher": ("action_proof",),
    "source_inspector": ("source_proof",),
    "claim_mapper": ("claim_proof",),
    "research_verifier": ("action_proof", "claim_proof", "source_proof"),
    "synthesis_reviewer": ("action_proof", "claim_proof", "source_proof", "task_proof"),
}
_EVIDENCE_TYPE_TO_PROOF_KIND: dict[str, tuple[ResearchChildProofKind, ...]] = {
    "SourceInspection": ("source_proof",),
    "DeterministicEvidenceVerifier": ("claim_proof", "action_proof"),
    "PlanVerifier": ("task_proof",),
    "custom:ResearchSourceProof": ("source_proof",),
    "custom:ResearchClaimProof": ("claim_proof",),
    "custom:ResearchClaimSupport": ("claim_proof",),
    "custom:ResearchActionProof": ("action_proof",),
    "custom:ResearchTaskProof": ("task_proof",),
}
_PROOF_KIND_ORDER: tuple[ResearchChildProofKind, ...] = (
    "action_proof",
    "claim_proof",
    "source_proof",
    "task_proof",
)
_REF_OBJECT_IDS: set[int] = set()
_REF_OBJECT_FINGERPRINTS: dict[int, str] = {}
_REF_FINALIZERS: dict[int, object] = {}
_PROOF_REF_OBJECT_IDS: set[int] = set()
_PROOF_REF_OBJECT_FINGERPRINTS: dict[int, str] = {}
_PROOF_REF_FINALIZERS: dict[int, object] = {}
_DECISION_OBJECT_IDS: set[int] = set()
_DECISION_OBJECT_FINGERPRINTS: dict[int, str] = {}
_DECISION_FINALIZERS: dict[int, object] = {}


class _ResearchChildModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(cls, *args: object, **kwargs: object) -> Self:
        raise TypeError("model_construct is disabled for research child role contracts")

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = deep
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)


class ResearchChildToolGrant(_ResearchChildModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchChildToolGrant subclasses are not accepted")

    tool_name: str = Field(alias="toolName")
    access: Literal["read_only"] = "read_only"
    fixture_only: Literal[True] = Field(default=True, alias="fixtureOnly")
    live_execution_allowed: Literal[False] = Field(
        default=False,
        alias="liveExecutionAllowed",
    )
    tool_host_execution_allowed: Literal[False] = Field(
        default=False,
        alias="toolHostExecutionAllowed",
    )

    @field_validator("tool_name")
    @classmethod
    def _validate_tool_name(cls, value: str) -> str:
        clean = _public_ref(value, "toolName")
        if not clean.startswith("Fixture"):
            raise ValueError("research child tool grants must be fixture-only read grants")
        parts = frozenset(re.split(r"[_.:-]+", clean.casefold()))
        normalized = re.sub(r"[^a-z0-9]", "", clean.casefold())
        if parts & _FORBIDDEN_TOOL_PARTS or any(
            forbidden in normalized for forbidden in _FORBIDDEN_TOOL_PARTS
        ):
            raise ValueError("research child tool grants must not expose live/raw/write access")
        return clean


class ResearchChildRolePolicy(_ResearchChildModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchChildRolePolicy subclasses are not accepted")

    role_name: ResearchChildRoleName = Field(alias="roleName")
    role_ref: str = Field(alias="roleRef")
    tool_grants: tuple[ResearchChildToolGrant, ...] = Field(alias="toolGrants")
    required_proof_kinds: tuple[ResearchChildProofKind, ...] = Field(
        alias="requiredProofKinds",
    )
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    fake_provider_only: Literal[True] = Field(default=True, alias="fakeProviderOnly")
    live_execution_allowed: Literal[False] = Field(default=False, alias="liveExecutionAllowed")
    provider_calls_allowed: Literal[False] = Field(default=False, alias="providerCallsAllowed")
    browser_execution_allowed: Literal[False] = Field(
        default=False,
        alias="browserExecutionAllowed",
    )
    model_calls_allowed: Literal[False] = Field(default=False, alias="modelCallsAllowed")
    tool_execution_allowed: Literal[False] = Field(default=False, alias="toolExecutionAllowed")
    memory_writes_allowed: Literal[False] = Field(default=False, alias="memoryWritesAllowed")
    channel_delivery_allowed: Literal[False] = Field(default=False, alias="channelDeliveryAllowed")
    user_visible_python_activation_allowed: Literal[False] = Field(
        default=False,
        alias="userVisiblePythonActivationAllowed",
    )
    adk_runner_attached: Literal[False] = Field(default=False, alias="adkRunnerAttached")
    function_tool_attached: Literal[False] = Field(default=False, alias="functionToolAttached")
    adk_usage_notes: str = Field(default=_ADK_USAGE_NOTES, alias="adkUsageNotes")

    @field_validator("role_ref")
    @classmethod
    def _validate_role_ref(cls, value: str) -> str:
        return _public_ref(value, "roleRef")

    @field_validator("tool_grants")
    @classmethod
    def _validate_tool_grants(
        cls,
        value: tuple[ResearchChildToolGrant, ...],
    ) -> tuple[ResearchChildToolGrant, ...]:
        if not value:
            raise ValueError("toolGrants must be non-empty")
        names = tuple(grant.tool_name for grant in value)
        if len(set(names)) != len(names):
            raise ValueError("toolGrants must not contain duplicates")
        return value

    @field_validator("required_proof_kinds")
    @classmethod
    def _validate_required_proof_kinds(
        cls,
        value: tuple[ResearchChildProofKind, ...],
    ) -> tuple[ResearchChildProofKind, ...]:
        if not value:
            raise ValueError("requiredProofKinds must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("requiredProofKinds must not contain duplicates")
        return tuple(
            proof for proof in _PROOF_KIND_ORDER if proof in set(value)
        )

    @field_validator("adk_usage_notes")
    @classmethod
    def _validate_adk_usage_notes(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("adkUsageNotes must be non-empty")
        if len(clean) > 320:
            raise ValueError("adkUsageNotes must be at most 320 characters")
        _reject_unsafe_public_text(clean, "adkUsageNotes")
        return clean

    @model_validator(mode="after")
    def _validate_policy_shape(self) -> Self:
        expected_ref = f"research_child_role:{self.role_name}"
        if self.role_ref != expected_ref:
            raise ValueError("roleRef must match the deterministic research child role ref")
        tool_names = tuple(grant.tool_name for grant in self.tool_grants)
        if tool_names != _ROLE_TOOL_NAMES[self.role_name]:
            raise ValueError("toolGrants must match the first-party research child role")
        if self.required_proof_kinds != _ROLE_REQUIRED_PROOFS[self.role_name]:
            raise ValueError("requiredProofKinds must match the first-party research child role")
        return self

    def public_projection(self) -> dict[str, object]:
        return {
            "roleName": self.role_name,
            "roleRef": self.role_ref,
            "toolGrants": tuple(
                grant.model_dump(by_alias=True, mode="python", warnings=False)
                for grant in self.tool_grants
            ),
            "requiredProofKinds": self.required_proof_kinds,
            "defaultOff": self.default_off,
            "localOnly": self.local_only,
            "fakeProviderOnly": self.fake_provider_only,
            "liveExecutionAllowed": self.live_execution_allowed,
            "providerCallsAllowed": self.provider_calls_allowed,
            "browserExecutionAllowed": self.browser_execution_allowed,
            "modelCallsAllowed": self.model_calls_allowed,
            "toolExecutionAllowed": self.tool_execution_allowed,
            "memoryWritesAllowed": self.memory_writes_allowed,
            "channelDeliveryAllowed": self.channel_delivery_allowed,
            "userVisiblePythonActivationAllowed": self.user_visible_python_activation_allowed,
            "adkRunnerAttached": self.adk_runner_attached,
            "functionToolAttached": self.function_tool_attached,
            "adkUsageNotes": self.adk_usage_notes,
        }


class ResearchChildEnvelopeEvidenceRef(_ResearchChildModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchChildEnvelopeEvidenceRef subclasses are not accepted")

    _issued_by_research_child_role_admission: bool = PrivateAttr(default=False)

    child_evidence_ref_id: str = Field(alias="childEvidenceRefId")
    issuer: Literal["openmagi_runtime_boundary"]
    expected_role: ResearchChildRoleName = Field(alias="expectedRole")
    parent_execution_id: str = Field(alias="parentExecutionId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    ledger_digest: str = Field(alias="ledgerDigest")
    authority_digest: str = Field(alias="authorityDigest")
    completion_summary_is_evidence: Literal[False] = Field(
        alias="completionSummaryIsEvidence",
    )
    accepted_evidence_metadata_only: Literal[True] = Field(
        alias="acceptedEvidenceMetadataOnly",
    )
    audit_event_refs: tuple[str, ...] = Field(alias="auditEventRefs")
    digest: str

    @field_validator(
        "child_evidence_ref_id",
        "parent_execution_id",
        "child_execution_id",
        "task_id",
    )
    @classmethod
    def _validate_public_refs(cls, value: str) -> str:
        return _public_ref(value, "child evidence ref")

    @field_validator("ledger_digest", "authority_digest", "digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest fields must be sha256 hex digests")
        return value

    @field_validator("audit_event_refs")
    @classmethod
    def _validate_audit_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("auditEventRefs must not contain duplicates")
        return tuple(_public_ref(item, "auditEventRefs") for item in value)

    @model_validator(mode="after")
    def _validate_digest_binding(self) -> Self:
        expected = _digest_for(_child_ref_digest_payload(self))
        if self.digest != expected:
            raise ValueError("digest must be bound to research child evidence metadata")
        return self

    def public_projection(self) -> dict[str, object]:
        _validate_ref_object(self)
        return _child_ref_digest_payload(self) | {"digest": self.digest}


class ResearchChildProofRef(_ResearchChildModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError("ResearchChildProofRef subclasses are not accepted")

    _issued_by_research_child_role_admission: bool = PrivateAttr(default=False)

    proof_ref_id: str = Field(alias="proofRefId")
    issuer: Literal["openmagi_runtime_boundary"]
    expected_role: ResearchChildRoleName = Field(alias="expectedRole")
    proof_kind: ResearchChildProofKind = Field(alias="proofKind")
    delegated_evidence_type: str = Field(alias="delegatedEvidenceType")
    parent_execution_id: str = Field(alias="parentExecutionId")
    child_execution_id: str = Field(alias="childExecutionId")
    task_id: str = Field(alias="taskId")
    ledger_digest: str = Field(alias="ledgerDigest")
    envelope_digest: str = Field(alias="envelopeDigest")
    evidence_digest: str = Field(alias="evidenceDigest")
    digest: str

    @field_validator(
        "proof_ref_id",
        "parent_execution_id",
        "child_execution_id",
        "task_id",
    )
    @classmethod
    def _validate_public_refs(cls, value: str) -> str:
        return _public_ref(value, "child proof ref")

    @field_validator("delegated_evidence_type")
    @classmethod
    def _validate_evidence_type(cls, value: str) -> str:
        return _safe_evidence_type(value, "delegatedEvidenceType")

    @field_validator("ledger_digest", "envelope_digest", "evidence_digest", "digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest fields must be sha256 hex digests")
        return value

    @model_validator(mode="after")
    def _validate_digest_binding(self) -> Self:
        if self.proof_kind not in _EVIDENCE_TYPE_TO_PROOF_KIND[self.delegated_evidence_type]:
            raise ValueError("proofKind must match delegatedEvidenceType")
        expected = _digest_for(_proof_ref_digest_payload(self))
        if self.digest != expected:
            raise ValueError("digest must be bound to research child proof metadata")
        return self

    def public_projection(self) -> dict[str, object]:
        _validate_proof_ref_object(self)
        return _proof_ref_digest_payload(self) | {"digest": self.digest}


class ResearchChildEvidenceAdmissionDecision(_ResearchChildModel):
    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        raise TypeError(
            "ResearchChildEvidenceAdmissionDecision subclasses are not accepted"
        )

    _issued_by_research_child_role_admission: bool = PrivateAttr(default=False)

    decision: ResearchChildAdmissionDecisionKind
    expected_role: ResearchChildRoleName = Field(alias="expectedRole")
    satisfied_proof_kinds: tuple[ResearchChildProofKind, ...] = Field(
        default=(),
        alias="satisfiedProofKinds",
    )
    missing_proof_kinds: tuple[ResearchChildProofKind, ...] = Field(
        default=(),
        alias="missingProofKinds",
    )
    reason_codes: tuple[ResearchChildAdmissionReasonCode, ...] = Field(alias="reasonCodes")
    child_evidence_ref: ResearchChildEnvelopeEvidenceRef | None = Field(
        default=None,
        alias="childEvidenceRef",
    )
    digest: str

    @field_validator("satisfied_proof_kinds", "missing_proof_kinds")
    @classmethod
    def _validate_proof_kind_tuple(
        cls,
        value: tuple[ResearchChildProofKind, ...],
    ) -> tuple[ResearchChildProofKind, ...]:
        if len(set(value)) != len(value):
            raise ValueError("proof kind tuples must not contain duplicates")
        return tuple(kind for kind in _PROOF_KIND_ORDER if kind in set(value))

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(
        cls,
        value: tuple[ResearchChildAdmissionReasonCode, ...],
    ) -> tuple[ResearchChildAdmissionReasonCode, ...]:
        if not value:
            raise ValueError("reasonCodes must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("reasonCodes must not contain duplicates")
        return value

    @field_validator("child_evidence_ref")
    @classmethod
    def _validate_nested_ref(
        cls,
        value: ResearchChildEnvelopeEvidenceRef | None,
    ) -> ResearchChildEnvelopeEvidenceRef | None:
        if value is None:
            return None
        return _validate_ref_object(value)

    @field_validator("digest")
    @classmethod
    def _validate_digest(cls, value: str) -> str:
        if not _DIGEST_RE.fullmatch(value):
            raise ValueError("digest must be a sha256 hex digest")
        return value

    @model_validator(mode="after")
    def _validate_decision_shape(self) -> Self:
        if self.decision == "accept":
            if self.reason_codes != ("child_evidence_accepted",):
                raise ValueError("accept decisions require child_evidence_accepted")
            if self.child_evidence_ref is None or not self.satisfied_proof_kinds:
                raise ValueError("accept decisions require child evidence and proof coverage")
            if self.missing_proof_kinds:
                raise ValueError("accept decisions cannot include missing proof kinds")
        if self.decision == "retry":
            if self.reason_codes != ("missing_required_child_proof",):
                raise ValueError("retry decisions require missing_required_child_proof")
            if not self.missing_proof_kinds:
                raise ValueError("retry decisions require missing proof kinds")
        if self.decision == "reject":
            if self.reason_codes == ("child_evidence_accepted",):
                raise ValueError("reject decisions cannot use accepted reason")
            if self.child_evidence_ref is not None:
                raise ValueError("reject decisions cannot include child evidence refs")
        expected = _digest_for(_decision_digest_payload(self))
        if self.digest != expected:
            raise ValueError("digest must be bound to research child admission decision")
        return self

    def public_projection(self) -> dict[str, object]:
        _validate_decision_object(self)
        payload = _decision_digest_payload(self)
        return payload | {"digest": self.digest}


def build_default_research_child_role_policies() -> tuple[ResearchChildRolePolicy, ...]:
    return tuple(_build_role_policy(role) for role in RESEARCH_CHILD_ROLE_NAMES)


def research_child_role_policy(role_name: str) -> ResearchChildRolePolicy:
    for policy in build_default_research_child_role_policies():
        if policy.role_name == role_name:
            return policy
    raise ValueError(f"unknown research child role: {role_name}")


def project_research_child_role_policies(
    policies: Iterable[ResearchChildRolePolicy] | None = None,
) -> tuple[dict[str, object], ...]:
    parsed = tuple(build_default_research_child_role_policies() if policies is None else policies)
    if tuple(policy.role_name for policy in parsed) != RESEARCH_CHILD_ROLE_NAMES:
        raise ValueError("research child role policies must use the deterministic role order")
    return tuple(policy.public_projection() for policy in parsed)


def issue_runtime_research_child_proof_ref(
    *,
    envelope: ChildRuntimeEnvelope,
    expected_role: ResearchChildRoleName,
    proof_kind: ResearchChildProofKind,
    delegated_evidence_type: str,
    proof_evidence: object,
    proof_ref_id: str | None = None,
) -> ResearchChildProofRef:
    role_policy = research_child_role_policy(expected_role)
    normalized_kind = _normalize_required_proof_kinds((proof_kind,))[0]
    if normalized_kind not in role_policy.required_proof_kinds:
        raise ValueError("proof kind must be allowed by the research child role")
    evidence_type = _safe_evidence_type(delegated_evidence_type, "delegatedEvidenceType")
    if normalized_kind not in _EVIDENCE_TYPE_TO_PROOF_KIND[evidence_type]:
        raise ValueError("proof kind must match delegated evidence type")
    evidence_digest = _verified_proof_evidence_digest(normalized_kind, proof_evidence)
    if _child_envelope_contains_raw_private_payload(envelope):
        raise ValueError("child proof refs cannot be issued for raw private child payloads")

    projection = project_child_runtime_envelope(envelope).model_dump(
        by_alias=True,
        mode="python",
        warnings=False,
    )
    if _value_contains_unsafe_text(projection):
        raise ValueError("child proof refs cannot be issued for unsafe child payloads")
    rejection = _envelope_rejection_reason(projection, role_policy)
    if rejection is not None:
        raise ValueError("child proof refs require an admissible child envelope")
    if not _envelope_has_delegated_required_evidence_type(envelope, evidence_type):
        raise ValueError("child proof refs require delegated_required evidence metadata")

    ledger_ref = dict(projection["ledgerRef"])  # type: ignore[index]
    payload = {
        "proofRefId": proof_ref_id
        or f"child-proof:{_digest_for((projection, normalized_kind, evidence_type))[7:23]}",
        "issuer": projection["issuer"],
        "expectedRole": expected_role,
        "proofKind": normalized_kind,
        "delegatedEvidenceType": evidence_type,
        "parentExecutionId": projection["parentExecutionId"],
        "childExecutionId": projection["childExecutionId"],
        "taskId": projection["taskId"],
        "ledgerDigest": _digest_for(ledger_ref),
        "envelopeDigest": _digest_for(projection),
        "evidenceDigest": evidence_digest,
    }
    return _mark_proof_ref_issued(
        ResearchChildProofRef(**payload, digest=_digest_for(payload))
    )


def admit_research_child_evidence(
    child_output: object,
    *,
    expected_role: ResearchChildRoleName,
    required_proof_kinds: Iterable[ResearchChildProofKind] | None = None,
    child_proof_refs: Iterable[ResearchChildProofRef] = (),
) -> ResearchChildEvidenceAdmissionDecision:
    role_policy = research_child_role_policy(expected_role)
    required = _normalize_required_proof_kinds(
        tuple(required_proof_kinds or role_policy.required_proof_kinds)
    )
    if not set(required).issubset(set(role_policy.required_proof_kinds)):
        raise ValueError("required proof kinds must be allowed by the research child role")

    if not isinstance(child_output, ChildRuntimeEnvelope):
        reason: ResearchChildAdmissionReasonCode = (
            "child_raw_private_payload_rejected"
            if _value_contains_unsafe_text(child_output)
            else "child_raw_text_not_evidence"
        )
        return _issue_decision(
            decision="reject",
            expected_role=expected_role,
            satisfied_proof_kinds=(),
            missing_proof_kinds=(),
            reason_codes=(reason,),
            child_evidence_ref=None,
        )

    if _child_envelope_contains_raw_private_payload(child_output):
        return _issue_decision(
            decision="reject",
            expected_role=expected_role,
            satisfied_proof_kinds=(),
            missing_proof_kinds=(),
            reason_codes=("child_raw_private_payload_rejected",),
            child_evidence_ref=None,
        )

    projection = project_child_runtime_envelope(child_output).model_dump(
        by_alias=True,
        mode="python",
        warnings=False,
    )
    if _value_contains_unsafe_text(projection):
        return _issue_decision(
            decision="reject",
            expected_role=expected_role,
            satisfied_proof_kinds=(),
            missing_proof_kinds=(),
            reason_codes=("child_raw_private_payload_rejected",),
            child_evidence_ref=None,
        )
    rejection = _envelope_rejection_reason(projection, role_policy)
    if rejection is not None:
        return _issue_decision(
            decision="reject",
            expected_role=expected_role,
            satisfied_proof_kinds=(),
            missing_proof_kinds=(),
            reason_codes=(rejection,),
            child_evidence_ref=None,
        )

    try:
        satisfied = _proof_kinds_from_child_proof_refs(
            child_output,
            expected_role=expected_role,
            role_policy=role_policy,
            projection=projection,
            child_proof_refs=tuple(child_proof_refs),
        )
    except (TypeError, ValueError):
        return _issue_decision(
            decision="reject",
            expected_role=expected_role,
            satisfied_proof_kinds=(),
            missing_proof_kinds=(),
            reason_codes=("child_proof_ref_invalid",),
            child_evidence_ref=None,
        )
    missing = tuple(kind for kind in required if kind not in set(satisfied))
    if missing:
        return _issue_decision(
            decision="retry",
            expected_role=expected_role,
            satisfied_proof_kinds=satisfied,
            missing_proof_kinds=missing,
            reason_codes=("missing_required_child_proof",),
            child_evidence_ref=None,
        )

    return _issue_decision(
        decision="accept",
        expected_role=expected_role,
        satisfied_proof_kinds=satisfied,
        missing_proof_kinds=(),
        reason_codes=("child_evidence_accepted",),
        child_evidence_ref=_child_ref_from_projection(projection, expected_role),
    )


def _build_role_policy(role: ResearchChildRoleName) -> ResearchChildRolePolicy:
    return ResearchChildRolePolicy(
        roleName=role,
        roleRef=f"research_child_role:{role}",
        toolGrants=tuple(
            ResearchChildToolGrant(toolName=tool_name)
            for tool_name in _ROLE_TOOL_NAMES[role]
        ),
        requiredProofKinds=_ROLE_REQUIRED_PROOFS[role],
    )


def _verified_proof_evidence_digest(
    proof_kind: ResearchChildProofKind,
    proof_evidence: object,
) -> str:
    if proof_kind == "source_proof":
        if not isinstance(proof_evidence, ResearchSourceProofVerdict):
            raise TypeError("source proof refs require source verifier verdict evidence")
        projection = project_research_source_proof_verdicts((proof_evidence,))[0]
        if projection["verdict"] != "allowed":
            raise ValueError("source proof refs require allowed source proof verdicts")
        return _digest_for(projection)
    if proof_kind == "action_proof":
        if not isinstance(proof_evidence, ResearchActionProofVerdict):
            raise TypeError("action proof refs require action verifier verdict evidence")
        projection = project_research_action_proof_verdicts((proof_evidence,))[0]
        if projection["verdict"] != "allowed":
            raise ValueError("action proof refs require allowed action proof verdicts")
        return _digest_for(projection)
    if proof_kind == "claim_proof":
        if type(proof_evidence) is not ResearchClaimSupportRef:
            raise TypeError("claim proof refs require claim support verifier evidence")
        projection = proof_evidence.public_projection()
        if projection["supportVerdict"] not in {"supported", "weak"}:
            raise ValueError("claim proof refs require supporting claim proof evidence")
        return _digest_for(projection)
    if proof_kind == "task_proof":
        if not isinstance(proof_evidence, ResearchAcceptanceCriteriaSet):
            raise TypeError("task proof refs require acceptance criteria evidence")
        projection = project_research_acceptance_criteria_set(proof_evidence)
        criteria = tuple(dict(item) for item in projection["criteria"])  # type: ignore[index]
        if any(
            criterion.get("completionMode") == "required"
            and criterion.get("status") != "satisfied"
            for criterion in criteria
        ):
            raise ValueError("task proof refs require satisfied required criteria")
        return _digest_for(projection)
    raise ValueError("proof kind must be a known research proof kind")


def _normalize_required_proof_kinds(
    values: tuple[ResearchChildProofKind, ...],
) -> tuple[ResearchChildProofKind, ...]:
    if not values:
        raise ValueError("required proof kinds must be non-empty")
    if len(set(values)) != len(values):
        raise ValueError("required proof kinds must not contain duplicates")
    invalid = tuple(value for value in values if value not in _PROOF_KIND_ORDER)
    if invalid:
        raise ValueError("required proof kinds must be known research proof kinds")
    return tuple(kind for kind in _PROOF_KIND_ORDER if kind in set(values))


def _proof_kinds_from_child_proof_refs(
    envelope: ChildRuntimeEnvelope,
    *,
    expected_role: ResearchChildRoleName,
    role_policy: ResearchChildRolePolicy,
    projection: Mapping[str, object],
    child_proof_refs: tuple[ResearchChildProofRef, ...],
) -> tuple[ResearchChildProofKind, ...]:
    kinds: set[ResearchChildProofKind] = set()
    ledger_ref = dict(projection["ledgerRef"])  # type: ignore[index]
    expected_ledger_digest = _digest_for(ledger_ref)
    expected_envelope_digest = _digest_for(projection)
    for ref in child_proof_refs:
        validated = _validate_proof_ref_object(ref)
        if validated.expected_role != expected_role:
            raise ValueError("child proof ref expectedRole must match admission role")
        if validated.proof_kind not in role_policy.required_proof_kinds:
            raise ValueError("child proof ref proofKind is not allowed for the role")
        if validated.parent_execution_id != str(projection["parentExecutionId"]):
            raise ValueError("child proof ref parentExecutionId must match envelope")
        if validated.child_execution_id != str(projection["childExecutionId"]):
            raise ValueError("child proof ref childExecutionId must match envelope")
        if validated.task_id != str(projection["taskId"]):
            raise ValueError("child proof ref taskId must match envelope")
        if validated.ledger_digest != expected_ledger_digest:
            raise ValueError("child proof ref ledgerDigest must match envelope")
        if validated.envelope_digest != expected_envelope_digest:
            raise ValueError("child proof ref envelopeDigest must match envelope")
        if not _envelope_has_delegated_required_evidence_type(
            envelope,
            validated.delegated_evidence_type,
        ):
            raise ValueError("child proof ref requires delegated_required evidence metadata")
        kinds.add(validated.proof_kind)
    return tuple(kind for kind in _PROOF_KIND_ORDER if kind in kinds)


def _envelope_has_delegated_required_evidence_type(
    envelope: ChildRuntimeEnvelope,
    evidence_type: str,
) -> bool:
    return any(
        requirement.type == evidence_type and requirement.delegation == "delegated_required"
        for requirement in envelope.delegated_evidence_requirements
    )


def _child_envelope_contains_raw_private_payload(envelope: ChildRuntimeEnvelope) -> bool:
    if envelope.raw_transcript_ref is not None:
        return True
    return _value_contains_unsafe_text(envelope.private_metadata)


def _envelope_rejection_reason(
    projection: Mapping[str, object],
    role_policy: ResearchChildRolePolicy,
) -> ResearchChildAdmissionReasonCode | None:
    if projection.get("status") != "accepted":
        return "child_envelope_not_accepted"
    if projection.get("role") != "research":
        return "non_research_child_envelope"

    policy_snapshot = dict(projection.get("policySnapshot", {}))
    permission_refs = tuple(str(item) for item in policy_snapshot.get("permissionRefs", ()))
    if permission_refs != (role_policy.role_ref,):
        return "child_role_ref_missing"

    adk_ownership = dict(projection.get("adkPrimitiveOwnership", {}))
    allowed_tools = tuple(str(item) for item in policy_snapshot.get("allowedToolNames", ()))
    adk_tools = tuple(str(item) for item in adk_ownership.get("allowedToolNames", ()))
    declared = tuple(grant.tool_name for grant in role_policy.tool_grants)
    if allowed_tools != declared or adk_tools != declared:
        return "undeclared_child_tool_grant"

    completion_contract = dict(projection.get("completionContract", {}))
    if completion_contract.get("requiredEvidence") in {"text", "none"}:
        return "child_summary_not_evidence"
    if completion_contract.get("summaryIsEvidence") is not False:
        return "child_summary_not_evidence"
    if completion_contract.get("acceptedEvidenceMetadataOnly") is not True:
        return "child_summary_not_evidence"

    authority_flags = dict(projection.get("authorityFlags", {}))
    if any(bool(value) for value in authority_flags.values()):
        return "live_authority_attached"
    if adk_ownership.get("runnerAttached") is not False:
        return "live_authority_attached"
    if adk_ownership.get("childExecutionAttached") is not False:
        return "live_authority_attached"
    return None


def _child_ref_from_projection(
    projection: Mapping[str, object],
    expected_role: ResearchChildRoleName,
) -> ResearchChildEnvelopeEvidenceRef:
    ledger_ref = dict(projection["ledgerRef"])  # type: ignore[index]
    authority_flags = dict(projection["authorityFlags"])  # type: ignore[index]
    payload = {
        "childEvidenceRefId": f"child:{_digest_for(projection)[7:23]}",
        "issuer": projection["issuer"],
        "expectedRole": expected_role,
        "parentExecutionId": projection["parentExecutionId"],
        "childExecutionId": projection["childExecutionId"],
        "taskId": projection["taskId"],
        "ledgerDigest": _digest_for(ledger_ref),
        "authorityDigest": _digest_for(authority_flags),
        "completionSummaryIsEvidence": False,
        "acceptedEvidenceMetadataOnly": True,
        "auditEventRefs": tuple(projection["auditEventRefs"]),  # type: ignore[arg-type]
    }
    return _mark_ref_issued(
        ResearchChildEnvelopeEvidenceRef(**payload, digest=_digest_for(payload))
    )


def _issue_decision(
    *,
    decision: ResearchChildAdmissionDecisionKind,
    expected_role: ResearchChildRoleName,
    satisfied_proof_kinds: tuple[ResearchChildProofKind, ...],
    missing_proof_kinds: tuple[ResearchChildProofKind, ...],
    reason_codes: tuple[ResearchChildAdmissionReasonCode, ...],
    child_evidence_ref: ResearchChildEnvelopeEvidenceRef | None,
) -> ResearchChildEvidenceAdmissionDecision:
    payload = {
        "decision": decision,
        "expectedRole": expected_role,
        "satisfiedProofKinds": satisfied_proof_kinds,
        "missingProofKinds": missing_proof_kinds,
        "reasonCodes": reason_codes,
        "childEvidenceRef": (
            None if child_evidence_ref is None else child_evidence_ref.public_projection()
        ),
    }
    return _mark_decision_issued(
        ResearchChildEvidenceAdmissionDecision(
            decision=decision,
            expectedRole=expected_role,
            satisfiedProofKinds=satisfied_proof_kinds,
            missingProofKinds=missing_proof_kinds,
            reasonCodes=reason_codes,
            childEvidenceRef=child_evidence_ref,
            digest=_digest_for(payload),
        )
    )


def _mark_ref_issued(
    ref: ResearchChildEnvelopeEvidenceRef,
) -> ResearchChildEnvelopeEvidenceRef:
    object_id = id(ref)
    ref.__pydantic_private__["_issued_by_research_child_role_admission"] = True
    _REF_OBJECT_IDS.add(object_id)
    _REF_OBJECT_FINGERPRINTS[object_id] = _model_fingerprint(ref)
    _REF_FINALIZERS[object_id] = finalize(ref, _discard_ref_object_id, object_id)
    return ref


def _discard_ref_object_id(object_id: int) -> None:
    _REF_OBJECT_IDS.discard(object_id)
    _REF_OBJECT_FINGERPRINTS.pop(object_id, None)
    _REF_FINALIZERS.pop(object_id, None)


def _validate_ref_object(value: object) -> ResearchChildEnvelopeEvidenceRef:
    if type(value) is not ResearchChildEnvelopeEvidenceRef:
        raise TypeError("child evidence refs must be issued research child evidence refs")
    if not value.__pydantic_private__.get("_issued_by_research_child_role_admission"):
        raise ValueError("child evidence ref must be issued by research child role admission")
    if id(value) not in _REF_OBJECT_IDS:
        raise ValueError("child evidence ref must be issued by research child role admission")
    if _REF_OBJECT_FINGERPRINTS.get(id(value)) != _model_fingerprint(value):
        raise ValueError("child evidence ref was modified after admission issuance")
    ResearchChildEnvelopeEvidenceRef.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _mark_proof_ref_issued(
    ref: ResearchChildProofRef,
) -> ResearchChildProofRef:
    object_id = id(ref)
    ref.__pydantic_private__["_issued_by_research_child_role_admission"] = True
    _PROOF_REF_OBJECT_IDS.add(object_id)
    _PROOF_REF_OBJECT_FINGERPRINTS[object_id] = _model_fingerprint(ref)
    _PROOF_REF_FINALIZERS[object_id] = finalize(
        ref,
        _discard_proof_ref_object_id,
        object_id,
    )
    return ref


def _discard_proof_ref_object_id(object_id: int) -> None:
    _PROOF_REF_OBJECT_IDS.discard(object_id)
    _PROOF_REF_OBJECT_FINGERPRINTS.pop(object_id, None)
    _PROOF_REF_FINALIZERS.pop(object_id, None)


def _validate_proof_ref_object(value: object) -> ResearchChildProofRef:
    if type(value) is not ResearchChildProofRef:
        raise TypeError("child proof refs must be issued research child proof refs")
    if not value.__pydantic_private__.get("_issued_by_research_child_role_admission"):
        raise ValueError("child proof ref must be issued by research child role admission")
    if id(value) not in _PROOF_REF_OBJECT_IDS:
        raise ValueError("child proof ref must be issued by research child role admission")
    if _PROOF_REF_OBJECT_FINGERPRINTS.get(id(value)) != _model_fingerprint(value):
        raise ValueError("child proof ref was modified after admission issuance")
    ResearchChildProofRef.model_validate(
        value.model_dump(by_alias=True, mode="python", warnings=False)
    )
    return value


def _mark_decision_issued(
    decision: ResearchChildEvidenceAdmissionDecision,
) -> ResearchChildEvidenceAdmissionDecision:
    object_id = id(decision)
    decision.__pydantic_private__["_issued_by_research_child_role_admission"] = True
    _DECISION_OBJECT_IDS.add(object_id)
    _DECISION_OBJECT_FINGERPRINTS[object_id] = _model_fingerprint(decision)
    _DECISION_FINALIZERS[object_id] = finalize(decision, _discard_decision_object_id, object_id)
    return decision


def _discard_decision_object_id(object_id: int) -> None:
    _DECISION_OBJECT_IDS.discard(object_id)
    _DECISION_OBJECT_FINGERPRINTS.pop(object_id, None)
    _DECISION_FINALIZERS.pop(object_id, None)


def _validate_decision_object(value: object) -> ResearchChildEvidenceAdmissionDecision:
    if type(value) is not ResearchChildEvidenceAdmissionDecision:
        raise TypeError("research child admission projections require decision objects")
    if not value.__pydantic_private__.get("_issued_by_research_child_role_admission"):
        raise ValueError("research child decision must be issued by child role admission")
    if id(value) not in _DECISION_OBJECT_IDS:
        raise ValueError("research child decision must be issued by child role admission")
    if _DECISION_OBJECT_FINGERPRINTS.get(id(value)) != _model_fingerprint(value):
        raise ValueError("research child decision was modified after admission issuance")
    return value


def _child_ref_digest_payload(ref: ResearchChildEnvelopeEvidenceRef) -> dict[str, object]:
    return {
        "childEvidenceRefId": ref.child_evidence_ref_id,
        "issuer": ref.issuer,
        "expectedRole": ref.expected_role,
        "parentExecutionId": ref.parent_execution_id,
        "childExecutionId": ref.child_execution_id,
        "taskId": ref.task_id,
        "ledgerDigest": ref.ledger_digest,
        "authorityDigest": ref.authority_digest,
        "completionSummaryIsEvidence": ref.completion_summary_is_evidence,
        "acceptedEvidenceMetadataOnly": ref.accepted_evidence_metadata_only,
        "auditEventRefs": ref.audit_event_refs,
    }


def _proof_ref_digest_payload(ref: ResearchChildProofRef) -> dict[str, object]:
    return {
        "proofRefId": ref.proof_ref_id,
        "issuer": ref.issuer,
        "expectedRole": ref.expected_role,
        "proofKind": ref.proof_kind,
        "delegatedEvidenceType": ref.delegated_evidence_type,
        "parentExecutionId": ref.parent_execution_id,
        "childExecutionId": ref.child_execution_id,
        "taskId": ref.task_id,
        "ledgerDigest": ref.ledger_digest,
        "envelopeDigest": ref.envelope_digest,
        "evidenceDigest": ref.evidence_digest,
    }


def _decision_digest_payload(
    decision: ResearchChildEvidenceAdmissionDecision,
) -> dict[str, object]:
    return {
        "decision": decision.decision,
        "expectedRole": decision.expected_role,
        "satisfiedProofKinds": decision.satisfied_proof_kinds,
        "missingProofKinds": decision.missing_proof_kinds,
        "reasonCodes": decision.reason_codes,
        "childEvidenceRef": (
            None
            if decision.child_evidence_ref is None
            else decision.child_evidence_ref.public_projection()
        ),
    }


def _public_ref(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _PUBLIC_REF_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe public ref")
    return clean


def _safe_evidence_type(value: str, field_name: str) -> str:
    clean = value.strip()
    if not clean:
        raise ValueError(f"{field_name} must be non-empty")
    _reject_unsafe_public_text(clean, field_name)
    if not _EVIDENCE_TYPE_RE.fullmatch(clean):
        raise ValueError(f"{field_name} must be a digest-safe evidence type")
    if clean not in _EVIDENCE_TYPE_TO_PROOF_KIND:
        raise ValueError(f"{field_name} must be declared by the research child role contract")
    return clean


def _reject_unsafe_public_text(value: str, field_name: str) -> None:
    if _PRIVATE_PATH_RE.search(value):
        raise ValueError(f"{field_name} must not contain private paths")
    if _SECRET_TEXT_RE.search(value) or _UNSAFE_TEXT_RE.search(value):
        raise ValueError(
            f"{field_name} must not contain raw, private, auth, token, or secret data"
        )


def _value_contains_unsafe_text(value: object) -> bool:
    if isinstance(value, str):
        return bool(
            _PRIVATE_PATH_RE.search(value)
            or _SECRET_TEXT_RE.search(value)
            or _UNSAFE_TEXT_RE.search(value)
        )
    if isinstance(value, Mapping):
        return any(
            _value_contains_unsafe_text(str(key)) or _value_contains_unsafe_text(nested)
            for key, nested in value.items()
        )
    if isinstance(value, tuple | list):
        return any(_value_contains_unsafe_text(item) for item in value)
    return False


def _digest_for(payload: object) -> str:
    material = json.dumps(
        _jsonable(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return "sha256:" + sha256(material.encode("utf-8")).hexdigest()


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, mode="python", warnings=False)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


def _model_fingerprint(model: BaseModel) -> str:
    return _digest_for(model.model_dump(by_alias=True, mode="python", warnings=False))


__all__ = [
    "RESEARCH_CHILD_ROLE_NAMES",
    "ResearchChildAdmissionDecisionKind",
    "ResearchChildAdmissionReasonCode",
    "ResearchChildEnvelopeEvidenceRef",
    "ResearchChildEvidenceAdmissionDecision",
    "ResearchChildProofKind",
    "ResearchChildProofRef",
    "ResearchChildRoleName",
    "ResearchChildRolePolicy",
    "ResearchChildToolGrant",
    "admit_research_child_evidence",
    "build_default_research_child_role_policies",
    "issue_runtime_research_child_proof_ref",
    "project_research_child_role_policies",
    "research_child_role_policy",
]
