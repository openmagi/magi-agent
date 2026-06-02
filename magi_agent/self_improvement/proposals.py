from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self, TypeAlias

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)

from magi_agent.runtime.receipt_utils import (
    canonical_digest,
    has_unsafe_marker,
    sanitize_public_text,
    sha256_ref,
)


ProposalType: TypeAlias = Literal[
    "recipe_change",
    "harness_config_change",
    "plugin_config_change",
    "test_fixture_addition",
    "docs_note",
    "blocked",
]
ProposalStatus: TypeAlias = Literal["disabled", "blocked", "proposed_local_fake"]
ProposalRecordStatus: TypeAlias = Literal["proposal_only", "blocked"]
DirectChangeDecision: TypeAlias = Literal["denied"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SHA256_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.:@=-]{1,191}$")
_SAFE_REASON_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,127}$")
_SAFE_DIRECT_CHANGE_RE = re.compile(r"^[a-z][a-z0-9_.:-]{0,95}$")
_SAFE_CLUSTER_REF_RE = re.compile(r"^failure-cluster:[a-f0-9]{16,64}$")
_ALLOWED_CHANGE_PREFIXES = (
    "recipe:",
    "harness:",
    "plugin:",
    "test-fixture:",
    "docs:",
    "ref:",
    "sha256:",
)
_ALLOWED_PROPOSAL_TYPES = frozenset(
    {
        "recipe_change",
        "harness_config_change",
        "plugin_config_change",
        "test_fixture_addition",
        "docs_note",
        "blocked",
    }
)
_KNOWN_DIRECT_CHANGES = frozenset(
    {
        "production_code_patch",
        "deploy_change",
        "secret_change",
        "db_migration",
        "sealed_file_hotpatch",
        "config_update",
    }
)
_UNSAFE_CHANGE_PREFIXES = (
    "deploy:",
    "secret:",
    "db:",
    "database:",
    "k8s:",
    "kubernetes:",
    "env:",
    "sealed-file:",
)


class ProposalAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    model_call_enabled: Literal[False] = Field(default=False, alias="modelCallEnabled")
    live_adk_runner_enabled: Literal[False] = Field(
        default=False,
        alias="liveAdkRunnerEnabled",
    )
    tool_execution_enabled: Literal[False] = Field(default=False, alias="toolExecutionEnabled")
    code_mutation_enabled: Literal[False] = Field(default=False, alias="codeMutationEnabled")
    config_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="configMutationEnabled",
    )
    plugin_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="pluginMutationEnabled",
    )
    deploy_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="deployMutationEnabled",
    )
    secret_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="secretMutationEnabled",
    )
    db_mutation_enabled: Literal[False] = Field(default=False, alias="dbMutationEnabled")
    sealed_file_hotpatch_enabled: Literal[False] = Field(
        default=False,
        alias="sealedFileHotpatchEnabled",
    )
    production_write_enabled: Literal[False] = Field(
        default=False,
        alias="productionWriteEnabled",
    )
    user_visible_output_enabled: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        for field_name, field in cls.model_fields.items():
            payload[field.alias or field_name] = False
            payload.pop(field_name, None)
        return payload

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        _ = _fields_set, values
        return cls()

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = update, deep
        return type(self)()

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return type(self)()

    @field_serializer(
        "model_call_enabled",
        "live_adk_runner_enabled",
        "tool_execution_enabled",
        "code_mutation_enabled",
        "config_mutation_enabled",
        "plugin_mutation_enabled",
        "deploy_mutation_enabled",
        "secret_mutation_enabled",
        "db_mutation_enabled",
        "sealed_file_hotpatch_enabled",
        "production_write_enabled",
        "user_visible_output_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class SelfImprovementProposalConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    local_fake_proposal_enabled: bool = Field(default=False, alias="localFakeProposalEnabled")
    live_adk_runner_enabled: Literal[False] = Field(
        default=False,
        alias="liveAdkRunnerEnabled",
    )
    automatic_mutation_enabled: Literal[False] = Field(
        default=False,
        alias="automaticMutationEnabled",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_live_flags_false(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["liveAdkRunnerEnabled"] = False
        payload.pop("live_adk_runner_enabled", None)
        payload["automaticMutationEnabled"] = False
        payload.pop("automatic_mutation_enabled", None)
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
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return self.model_copy(update=update)

    @field_serializer("live_adk_runner_enabled", "automatic_mutation_enabled")
    def _serialize_false(self, _value: object) -> bool:
        return False


class SelfImprovementProposalRequest(BaseModel):
    model_config = _MODEL_CONFIG

    request_id: str = Field(alias="requestId")
    proposal_type: ProposalType = Field(alias="proposalType")
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    eval_observation_digest_refs: tuple[str, ...] = Field(
        alias="evalObservationDigestRefs",
    )
    failure_cluster_refs: tuple[str, ...] = Field(default=(), alias="failureClusterRefs")
    title: str
    summary: str
    change_refs: tuple[str, ...] = Field(default=(), alias="changeRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    requested_direct_changes: tuple[str, ...] = Field(
        default=(),
        alias="requestedDirectChanges",
    )
    raw_prompt: str | None = Field(default=None, alias="rawPrompt", exclude=True, repr=False)
    raw_output: str | None = Field(default=None, alias="rawOutput", exclude=True, repr=False)
    raw_private_path: str | None = Field(
        default=None,
        alias="rawPrivatePath",
        exclude=True,
        repr=False,
    )
    tool_logs: str | None = Field(default=None, alias="toolLogs", exclude=True, repr=False)
    hidden_reasoning: str | None = Field(
        default=None,
        alias="hiddenReasoning",
        exclude=True,
        repr=False,
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_field_names(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        aliases = {
            "request_id": "requestId",
            "proposal_type": "proposalType",
            "policy_snapshot_digest": "policySnapshotDigest",
            "eval_observation_digest_refs": "evalObservationDigestRefs",
            "failure_cluster_refs": "failureClusterRefs",
            "change_refs": "changeRefs",
            "reason_codes": "reasonCodes",
            "requested_direct_changes": "requestedDirectChanges",
        }
        for field_name, alias in aliases.items():
            if field_name in payload:
                payload[alias] = payload.pop(field_name)
        return payload

    @field_validator("request_id")
    @classmethod
    def _validate_request_id(cls, value: str) -> str:
        return _safe_ref(value, "requestId", prefixes=("self-improvement-proposal:", "ref:"))

    @field_validator("proposal_type", mode="before")
    @classmethod
    def _validate_proposal_type(cls, value: object) -> str:
        raw = str(value or "").strip()
        if raw not in _ALLOWED_PROPOSAL_TYPES:
            raise ValueError("proposalType must be an allowed structured proposal type")
        return raw

    @field_validator("policy_snapshot_digest")
    @classmethod
    def _validate_policy_snapshot_digest(cls, value: str) -> str:
        return _safe_digest(value, "policySnapshotDigest")

    @field_validator("eval_observation_digest_refs", mode="before")
    @classmethod
    def _validate_observation_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        if not refs:
            raise ValueError("evalObservationDigestRefs must not be empty")
        return tuple(_safe_digest(ref, "evalObservationDigestRefs") for ref in refs)

    @field_validator("failure_cluster_refs", mode="before")
    @classmethod
    def _validate_failure_cluster_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        for ref in refs:
            if not _SAFE_CLUSTER_REF_RE.fullmatch(ref):
                raise ValueError("failureClusterRefs must be safe failure cluster refs")
        return refs

    @field_validator("title", "summary")
    @classmethod
    def _validate_public_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "publicText")
        return _safe_public_text(value, field_name)

    @field_validator("change_refs", mode="before")
    @classmethod
    def _validate_change_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_change_ref(ref, "changeRefs") for ref in _string_tuple(value))

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "reasonCodes") for item in _string_tuple(value))

    @field_validator("requested_direct_changes", mode="before")
    @classmethod
    def _validate_direct_changes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_direct_change(item) for item in _string_tuple(value))

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
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return self.model_copy(update=update)


class SelfImprovementProposal(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementProposal.v1"] = Field(
        default="selfImprovementProposal.v1",
        alias="schemaVersion",
    )
    proposal_id: str = Field(alias="proposalId")
    proposal_digest: str = Field(alias="proposalDigest")
    proposal_type: ProposalType = Field(alias="proposalType")
    status: ProposalRecordStatus
    policy_snapshot_digest: str = Field(alias="policySnapshotDigest")
    eval_observation_digest_refs: tuple[str, ...] = Field(
        alias="evalObservationDigestRefs",
    )
    failure_cluster_refs: tuple[str, ...] = Field(default=(), alias="failureClusterRefs")
    title: str
    summary: str
    change_refs: tuple[str, ...] = Field(default=(), alias="changeRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    denied_direct_change_refs: tuple[str, ...] = Field(
        default=(),
        alias="deniedDirectChangeRefs",
    )
    execution_default: Literal["denied"] = Field(default="denied", alias="executionDefault")
    authority_flags: ProposalAuthorityFlags = Field(
        default_factory=ProposalAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("proposal_id")
    @classmethod
    def _validate_proposal_id(cls, value: str) -> str:
        return _safe_ref(value, "proposalId", prefixes=("self-improvement-proposal:",))

    @field_validator("proposal_digest", "policy_snapshot_digest")
    @classmethod
    def _validate_digest_refs(cls, value: str) -> str:
        return _safe_digest(value, "proposalDigest")

    @field_validator("eval_observation_digest_refs", mode="before")
    @classmethod
    def _validate_eval_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        if not refs:
            raise ValueError("evalObservationDigestRefs must not be empty")
        return tuple(_safe_digest(ref, "evalObservationDigestRefs") for ref in refs)

    @field_validator("failure_cluster_refs", mode="before")
    @classmethod
    def _validate_cluster_refs(cls, value: object) -> tuple[str, ...]:
        refs = _string_tuple(value)
        for ref in refs:
            if not _SAFE_CLUSTER_REF_RE.fullmatch(ref):
                raise ValueError("failureClusterRefs must be safe failure cluster refs")
        return refs

    @field_validator("title", "summary")
    @classmethod
    def _validate_text(cls, value: str, info: object) -> str:
        return _safe_public_text(value, getattr(info, "field_name", "publicText"))

    @field_validator("change_refs", mode="before")
    @classmethod
    def _validate_change_refs(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_change_ref(ref, "changeRefs") for ref in _string_tuple(value))

    @field_validator("reason_codes", mode="before")
    @classmethod
    def _validate_reason_codes(cls, value: object) -> tuple[str, ...]:
        return tuple(_safe_reason_code(item, "reasonCodes") for item in _string_tuple(value))

    @field_validator("denied_direct_change_refs", mode="before")
    @classmethod
    def _validate_denied_change_refs(cls, value: object) -> tuple[str, ...]:
        refs = []
        for item in _string_tuple(value):
            raw = item.removeprefix("direct-change:")
            refs.append(f"direct-change:{_safe_direct_change(raw)}")
        return tuple(refs)

    @model_validator(mode="after")
    def _validate_digest(self) -> Self:
        expected = canonical_digest(_proposal_digest_payload(self))
        if self.proposal_digest != expected:
            raise ValueError("proposalDigest mismatch")
        return self

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
        _ = update, deep
        raise ValueError("model_copy is disabled for SelfImprovementProposal")

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        raise ValueError("copy is disabled for SelfImprovementProposal")

    @field_serializer("execution_default")
    def _serialize_execution_default(self, _value: object) -> str:
        return "denied"


class SelfImprovementProposalResult(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["selfImprovementProposalResult.v1"] = Field(
        default="selfImprovementProposalResult.v1",
        alias="schemaVersion",
    )
    status: ProposalStatus
    proposal: SelfImprovementProposal | None = None
    blocked_reason: str | None = Field(default=None, alias="blockedReason")
    direct_change_decision: DirectChangeDecision = Field(
        default="denied",
        alias="directChangeDecision",
    )
    authority_flags: ProposalAuthorityFlags = Field(
        default_factory=ProposalAuthorityFlags,
        alias="authorityFlags",
    )
    adk_primitive: Literal["ADK Runner boundary"] = Field(
        default="ADK Runner boundary",
        alias="adkPrimitive",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_denied_authority(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        if "blocked_reason" in payload:
            payload["blockedReason"] = payload.pop("blocked_reason")
        payload["directChangeDecision"] = "denied"
        payload.pop("direct_change_decision", None)
        payload["authorityFlags"] = ProposalAuthorityFlags().model_dump(by_alias=True)
        payload.pop("authority_flags", None)
        return payload

    @field_validator("blocked_reason")
    @classmethod
    def _validate_blocked_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _safe_reason_code(value, "blockedReason")

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
        _ = deep
        payload = self.model_dump(by_alias=True)
        if update:
            payload.update(update)
        return type(self).model_validate(payload)

    def copy(
        self,
        *,
        include: Any = None,
        exclude: Any = None,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        _ = include, exclude, update, deep
        return self.model_copy(update=update)

    @field_serializer("direct_change_decision")
    def _serialize_direct_change_decision(self, _value: object) -> str:
        return "denied"


class SelfImprovementProposalService:
    def __init__(
        self,
        config: SelfImprovementProposalConfig | Mapping[str, object] | None = None,
    ) -> None:
        self.config = (
            config
            if isinstance(config, SelfImprovementProposalConfig)
            else SelfImprovementProposalConfig.model_validate(config or {})
        )

    def generate(
        self,
        request: SelfImprovementProposalRequest | Mapping[str, object],
    ) -> SelfImprovementProposalResult:
        try:
            proposal_request = (
                SelfImprovementProposalRequest.model_validate(request.model_dump(by_alias=True))
                if isinstance(request, SelfImprovementProposalRequest)
                else SelfImprovementProposalRequest.model_validate(request)
            )
        except ValidationError:
            if isinstance(request, Mapping):
                return _blocked_repair_result(request)
            raise

        if not self.config.enabled:
            return SelfImprovementProposalResult(
                status="disabled",
                blockedReason="self_improvement_proposal_disabled",
            )
        if not self.config.local_fake_proposal_enabled:
            return SelfImprovementProposalResult(
                status="blocked",
                blockedReason="self_improvement_local_fake_proposal_disabled",
            )

        proposal = _build_proposal(proposal_request)
        status: ProposalStatus = (
            "blocked" if proposal.proposal_type == "blocked" else "proposed_local_fake"
        )
        blocked_reason = "proposal_type_blocked" if proposal.proposal_type == "blocked" else None
        return SelfImprovementProposalResult(
            status=status,
            proposal=proposal,
            blockedReason=blocked_reason,
        )


def _blocked_repair_result(payload: Mapping[str, object]) -> SelfImprovementProposalResult:
    policy_digest = str(payload.get("policySnapshotDigest") or sha256_ref("blocked-policy"))
    if not _SHA256_RE.fullmatch(policy_digest):
        policy_digest = sha256_ref(policy_digest)
    observation_refs = _string_tuple(payload.get("evalObservationDigestRefs")) or (
        sha256_ref("blocked-observation"),
    )
    safe_observation_refs = tuple(
        ref if _SHA256_RE.fullmatch(ref) else sha256_ref(ref) for ref in observation_refs
    )
    proposal = _build_proposal_from_parts(
        proposal_type="blocked",
        status="blocked",
        policy_snapshot_digest=policy_digest,
        eval_observation_digest_refs=safe_observation_refs,
        failure_cluster_refs=(),
        title="Blocked self-improvement proposal",
        summary="Unsupported proposal was blocked before any runtime action.",
        change_refs=(),
        reason_codes=("unsupported_proposal_type",),
        denied_direct_change_refs=(),
    )
    return SelfImprovementProposalResult(
        status="blocked",
        proposal=proposal,
        blockedReason="unsupported_proposal_type",
    )


def _build_proposal(request: SelfImprovementProposalRequest) -> SelfImprovementProposal:
    denied_direct_change_refs = tuple(
        f"direct-change:{change}" for change in request.requested_direct_changes
    )
    status: ProposalRecordStatus = (
        "blocked" if request.proposal_type == "blocked" else "proposal_only"
    )
    return _build_proposal_from_parts(
        proposal_type=request.proposal_type,
        status=status,
        policy_snapshot_digest=request.policy_snapshot_digest,
        eval_observation_digest_refs=request.eval_observation_digest_refs,
        failure_cluster_refs=request.failure_cluster_refs,
        title=request.title,
        summary=request.summary,
        change_refs=request.change_refs,
        reason_codes=request.reason_codes,
        denied_direct_change_refs=denied_direct_change_refs,
    )


def _build_proposal_from_parts(
    *,
    proposal_type: ProposalType,
    status: ProposalRecordStatus,
    policy_snapshot_digest: str,
    eval_observation_digest_refs: tuple[str, ...],
    failure_cluster_refs: tuple[str, ...],
    title: str,
    summary: str,
    change_refs: tuple[str, ...],
    reason_codes: tuple[str, ...],
    denied_direct_change_refs: tuple[str, ...],
) -> SelfImprovementProposal:
    proposal_id = "self-improvement-proposal:" + sha256_ref(
        "|".join(
            (
                proposal_type,
                policy_snapshot_digest,
                ",".join(eval_observation_digest_refs),
                ",".join(change_refs),
                ",".join(reason_codes),
                ",".join(denied_direct_change_refs),
            )
        )
    ).removeprefix("sha256:")[:32]
    payload = {
        "schemaVersion": "selfImprovementProposal.v1",
        "proposalId": proposal_id,
        "proposalType": proposal_type,
        "status": status,
        "policySnapshotDigest": policy_snapshot_digest,
        "evalObservationDigestRefs": eval_observation_digest_refs,
        "failureClusterRefs": failure_cluster_refs,
        "title": title,
        "summary": summary,
        "changeRefs": change_refs,
        "reasonCodes": reason_codes,
        "deniedDirectChangeRefs": denied_direct_change_refs,
        "executionDefault": "denied",
        "authorityFlags": ProposalAuthorityFlags().model_dump(by_alias=True),
    }
    return SelfImprovementProposal.model_validate(
        payload | {"proposalDigest": canonical_digest(payload)}
    )


def _proposal_digest_payload(proposal: SelfImprovementProposal) -> dict[str, object]:
    return proposal.model_dump(by_alias=True, exclude={"proposal_digest"})


def _safe_digest(value: str, field_name: str) -> str:
    raw = str(value).strip()
    if not _SHA256_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be sha256:<64 lowercase hex>")
    return raw


def _safe_ref(value: str, field_name: str, *, prefixes: tuple[str, ...]) -> str:
    raw = str(value).strip()
    if not raw or not raw.startswith(prefixes) or not _SAFE_ID_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be a safe public ref")
    if has_unsafe_marker(raw) or sanitize_public_text(raw) != raw:
        raise ValueError(f"{field_name} contains private or unsafe material")
    return raw


def _safe_change_ref(value: str, field_name: str) -> str:
    raw = str(value).strip()
    if raw.startswith(_UNSAFE_CHANGE_PREFIXES):
        raise ValueError(f"{field_name} contains forbidden live-change ref")
    if not raw.startswith(_ALLOWED_CHANGE_PREFIXES):
        raise ValueError(f"{field_name} must use an allowed proposal ref prefix")
    if not _SAFE_ID_RE.fullmatch(raw) and not _SHA256_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be a safe public ref")
    if has_unsafe_marker(raw) or sanitize_public_text(raw) != raw:
        raise ValueError(f"{field_name} contains private or unsafe material")
    return raw


def _safe_public_text(value: str, field_name: str) -> str:
    raw = str(value)
    safe = sanitize_public_text(raw)
    if not safe:
        raise ValueError(f"{field_name} must not be empty")
    if safe != raw or has_unsafe_marker(safe):
        raise ValueError(f"{field_name} contains private or unsafe material")
    return safe[:400]


def _safe_reason_code(value: str, field_name: str) -> str:
    token = str(value).strip().lower().replace(" ", "_")
    if not token or not _SAFE_REASON_RE.fullmatch(token):
        raise ValueError(f"{field_name} must be a safe reason code")
    if has_unsafe_marker(token) or sanitize_public_text(token) != token:
        raise ValueError(f"{field_name} contains private or unsafe material")
    return token


def _safe_direct_change(value: str) -> str:
    token = str(value).strip().lower().replace(" ", "_")
    if token in _KNOWN_DIRECT_CHANGES:
        return token
    if (
        not token
        or not _SAFE_DIRECT_CHANGE_RE.fullmatch(token)
        or has_unsafe_marker(token)
        or sanitize_public_text(token) != token
    ):
        raise ValueError("requestedDirectChanges must be safe direct-change tokens")
    return token


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, bytes):
        return tuple(str(item) for item in value)
    return (str(value),)


__all__ = [
    "DirectChangeDecision",
    "ProposalAuthorityFlags",
    "ProposalRecordStatus",
    "ProposalStatus",
    "ProposalType",
    "SelfImprovementProposal",
    "SelfImprovementProposalConfig",
    "SelfImprovementProposalRequest",
    "SelfImprovementProposalResult",
    "SelfImprovementProposalService",
]
