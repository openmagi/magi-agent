from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from magi_agent.runtime.control import ControlRequest
from magi_agent.transport.tool_preview import sanitize_tool_preview


MemorySourceAuthorityCategory = Literal[
    "normal_recall_metadata",
    "read_only_recall_metadata",
    "read_only_write_blocked",
    "incognito_recall_blocked",
    "incognito_write_blocked",
    "source_authority_long_term_disabled",
    "redaction_failure_blocks_projection",
    "provider_unavailable_fail_open_no_claim",
    "explicit_write_requires_receipt_before_claim",
    "memory_redact_authority_supersedes_provider",
    "stale_conflicting_memory_background_only",
    "child_agent_memory_scope_isolated",
    "selected_kb_current_source_background_only",
    "attachment_current_source_background_only",
    "image_current_source_background_only",
    "classifier_disabled_blocks_recall",
    "root_memory_background_without_continuation",
    "qmd_active_with_continuation_overlap",
    "stale_background_memory_retry_metadata",
    "passive_background_memory_reference_audit_ok",
    "hipocampus_root_precedes_legacy_memory_metadata",
]
MemoryMode = Literal["normal", "read_only", "incognito"]
MemorySourceAuthority = Literal[
    "long_term_allowed",
    "long_term_disabled",
    "background_only",
    "memory_redact_authority",
    "child_isolated",
]
MemoryProviderId = Literal["hipocampus", "qmd", "agentmemory", "none"]
MemoryProviderStatus = Literal["available", "unavailable", "not_applicable"]
MemoryGuardDecision = Literal[
    "allow_metadata_only",
    "block",
    "fail_open_no_claim",
    "approval_required",
]
MemoryRedactionStatus = Literal["verified", "failed", "not_required"]
MemoryRedactAuthority = Literal["none", "openmagi_memory_redact"]
MemoryPriority = Literal["normal", "background", "blocked"]
AgentRole = Literal["main", "coding", "research", "child"]
EffectiveLongTermMemoryPolicy = Literal["normal", "background_only", "disabled"]
MemoryRecallSource = Literal["root", "qmd"]
MemoryContinuity = Literal["active", "related", "background"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:/data/bots|/workspace|/var/lib/kubelet)(?:/[^\s\"',}]+)*",
    re.IGNORECASE,
)
_FORBIDDEN_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_memorysecret",
    "sk-memory-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "raw memory payload",
    "pythonResponseAuthority",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_memory_service_replaced",
        "adk_runner_invoked",
        "adk_runner_attached",
        "agent_memory_provider_called",
        "canary_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
        "hipocampus_qmd_live_called",
        "live_memory_provider_called",
        "live_tool",
        "live_tool_dispatched",
        "memory_provider_called",
        "memory_redacted_by_provider",
        "memory_written",
        "production_authority",
        "production_storage_written",
        "prompt_injected",
        "route_attached",
        "route_or_api_attached",
        "telegram_attached",
        "traffic_attached",
    }
)
_REQUIRED_CATEGORIES = set(MemorySourceAuthorityCategory.__args__)  # type: ignore[attr-defined]


class MemorySourceAuthorityAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_memory_provider_called: Literal[False] = Field(
        default=False,
        alias="liveMemoryProviderCalled",
    )
    adk_memory_service_replaced: Literal[False] = Field(
        default=False,
        alias="adkMemoryServiceReplaced",
    )
    agent_memory_provider_called: Literal[False] = Field(
        default=False,
        alias="agentMemoryProviderCalled",
    )
    hipocampus_qmd_live_called: Literal[False] = Field(
        default=False,
        alias="hipocampusQmdLiveCalled",
    )
    prompt_injected: Literal[False] = Field(default=False, alias="promptInjected")
    memory_written: Literal[False] = Field(default=False, alias="memoryWritten")
    memory_redacted_by_provider: Literal[False] = Field(
        default=False,
        alias="memoryRedactedByProvider",
    )
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{name: False for name in cls.model_fields})

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            data.update(dict(update))
        return type(self).model_validate(data)

    @field_serializer(
        "adk_runner_invoked",
        "live_memory_provider_called",
        "adk_memory_service_replaced",
        "agent_memory_provider_called",
        "hipocampus_qmd_live_called",
        "prompt_injected",
        "memory_written",
        "memory_redacted_by_provider",
        "live_tool_dispatched",
        "production_storage_written",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class MemoryProviderCandidate(BaseModel):
    model_config = _MODEL_CONFIG

    provider_id: MemoryProviderId = Field(alias="providerId")
    candidate_plugin_id: str | None = Field(default=None, alias="candidatePluginId")
    provider_status: MemoryProviderStatus = Field(alias="providerStatus")
    provider_call_made: Literal[False] = Field(default=False, alias="providerCallMade")
    provider_delete_or_redact_allowed: Literal[False] = Field(
        default=False,
        alias="providerDeleteOrRedactAllowed",
    )

    @model_validator(mode="after")
    def _validate_provider(self) -> Self:
        if self.provider_id == "none":
            if self.provider_status != "not_applicable" or self.candidate_plugin_id is not None:
                raise ValueError("none provider must be not_applicable without plugin metadata")
        elif self.provider_status == "not_applicable":
            raise ValueError("concrete memory provider cannot be not_applicable")
        if self.provider_id == "agentmemory" and self.candidate_plugin_id not in {
            "openmagi.agentmemory",
            "openmagi.memory-agentmemory",
        }:
            raise ValueError("AgentMemory provider metadata must stay under OpenMagi plugins")
        return self


class MemoryGuardScopeMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    tenant_id: str = Field(alias="tenantId")
    bot_id: str = Field(alias="botId")
    session_key: str = Field(alias="sessionKey")
    turn_id: str = Field(alias="turnId")
    agent_role: AgentRole = Field(alias="agentRole")
    spawn_depth: int = Field(alias="spawnDepth", ge=0)
    child_execution_id: str | None = Field(default=None, alias="childExecutionId")
    parent_execution_id: str | None = Field(default=None, alias="parentExecutionId")
    inherited_from_parent: bool = Field(alias="inheritedFromParent")

    @model_validator(mode="after")
    def _validate_scope(self) -> Self:
        if self.agent_role == "child" and self.spawn_depth <= 0:
            raise ValueError("child memory scope requires spawnDepth greater than zero")
        if self.agent_role == "child" and self.child_execution_id is None:
            raise ValueError("child memory scope requires childExecutionId")
        if self.agent_role != "child" and self.child_execution_id is not None:
            raise ValueError("main memory scope cannot include childExecutionId")
        return self


class MemorySourceAuthorityMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    current_source_kinds: tuple[str, ...] = Field(alias="currentSourceKinds")
    effective_long_term_memory_policy: EffectiveLongTermMemoryPolicy = Field(
        alias="effectiveLongTermMemoryPolicy",
    )
    classifier_reason: str = Field(alias="classifierReason")

    @model_validator(mode="after")
    def _validate_source_metadata(self) -> Self:
        if (
            self.effective_long_term_memory_policy == "normal"
            and len(self.current_source_kinds) != 0
        ):
            raise ValueError("normal long-term memory policy cannot include current sources")
        return self


class StaleMemoryPromotionRetryMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    retry: bool
    phrase: str | None = None
    path: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _validate_retry_metadata(self) -> Self:
        if self.retry:
            if not self.phrase or not self.path or not self.reason:
                raise ValueError("stale memory retry metadata requires phrase, path, and reason")
        else:
            if self.phrase is not None or self.path is not None or self.reason is not None:
                raise ValueError("non-retry stale memory metadata cannot include retry details")
        return self


class MemoryContinuityMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    memory_recall_source: MemoryRecallSource = Field(alias="memoryRecallSource")
    continuity: MemoryContinuity
    path: str
    continuation_cue: bool = Field(alias="continuationCue")
    token_overlap: bool = Field(alias="tokenOverlap")
    stale_promotion_retry: StaleMemoryPromotionRetryMetadata | None = Field(
        default=None,
        alias="stalePromotionRetry",
    )

    @model_validator(mode="after")
    def _validate_continuity_metadata(self) -> Self:
        if self.memory_recall_source == "root" and self.continuity == "active":
            if not (self.continuation_cue and self.token_overlap):
                raise ValueError("root memory can only be active with continuation and overlap")
        if self.memory_recall_source == "qmd" and self.continuity == "active":
            if not (self.continuation_cue and self.token_overlap):
                raise ValueError("qmd memory is active only with continuation and overlap")
        if self.stale_promotion_retry is not None and self.continuity != "background":
            raise ValueError("stale promotion retry metadata only applies to background memory")
        return self


class MemorySourceAuthorityCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: MemorySourceAuthorityCategory
    memory_mode: MemoryMode = Field(alias="memoryMode")
    source_authority: MemorySourceAuthority = Field(alias="sourceAuthority")
    provider: MemoryProviderCandidate
    recall_intent: bool = Field(alias="recallIntent")
    write_intent: bool = Field(alias="writeIntent")
    redact_intent: bool = Field(alias="redactIntent")
    decision: MemoryGuardDecision
    redaction_status: MemoryRedactionStatus = Field(alias="redactionStatus")
    prompt_projection_allowed: bool = Field(alias="promptProjectionAllowed")
    public_projection_allowed: bool = Field(alias="publicProjectionAllowed")
    public_preview: str = Field(alias="publicPreview")
    write_claim_allowed: bool = Field(alias="writeClaimAllowed")
    write_receipt_ref: str | None = Field(default=None, alias="writeReceiptRef")
    memory_redact_authority: MemoryRedactAuthority = Field(alias="memoryRedactAuthority")
    background_only: bool = Field(alias="backgroundOnly")
    priority: MemoryPriority
    current_source_priority: str = Field(alias="currentSourcePriority")
    no_user_facing_memory_claim: bool = Field(alias="noUserFacingMemoryClaim")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    control_request: ControlRequest | None = Field(default=None, alias="controlRequest")
    scope: MemoryGuardScopeMetadata
    source_metadata: MemorySourceAuthorityMetadata | None = Field(
        default=None,
        alias="sourceMetadata",
    )
    continuity_metadata: MemoryContinuityMetadata | None = Field(
        default=None,
        alias="continuityMetadata",
    )
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    audit_refs: tuple[str, ...] = Field(default=(), alias="auditRefs")
    attachment_flags: MemorySourceAuthorityAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
            control_request = value.get("controlRequest")
            if isinstance(control_request, Mapping):
                arguments = control_request.get("arguments")
                if isinstance(arguments, Mapping):
                    _reject_unsafe_raw_value(arguments)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not (self.recall_intent or self.write_intent or self.redact_intent):
            raise ValueError("memory/source authority case must declare an intent")
        if self.redaction_status == "failed":
            if self.prompt_projection_allowed or self.public_projection_allowed:
                raise ValueError("redaction failures must block prompt and public projection")
            if self.decision != "block":
                raise ValueError("redaction failures must block memory projection")
        if self.prompt_projection_allowed:
            raise ValueError("memory prompt projection is not represented in local fixtures")
        if self.write_claim_allowed and self.write_receipt_ref is None:
            raise ValueError("write claims require an explicit memory write receipt")
        if self.write_claim_allowed and self.no_user_facing_memory_claim:
            raise ValueError("write claims cannot also be marked as no-claim")
        _validate_memory_mode(self)
        _validate_source_authority(self)
        _validate_provider_status(self)
        _validate_decision(self)
        _validate_redact_authority(self)
        _validate_child_scope(self)
        _validate_source_metadata(self)
        _validate_continuity_metadata(self)
        _validate_expected_reason(self)
        return self


class MemorySourceAuthorityGuardFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["memorySourceAuthorityFixture.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: MemorySourceAuthorityAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[MemorySourceAuthorityCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("memory/source authority caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("memory/source authority fixture is missing required categories")
        return self


class MemorySourceAuthorityProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: MemorySourceAuthorityAttachmentFlags = Field(alias="attachmentFlags")
    no_live_memory_runtime: Literal[True] = Field(alias="noLiveMemoryRuntime")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_decision: dict[str, int] = Field(alias="byDecision")
    by_memory_mode: dict[str, int] = Field(alias="byMemoryMode")
    by_source_authority: dict[str, int] = Field(alias="bySourceAuthority")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    control_requests: dict[str, dict[str, object]] = Field(alias="controlRequests")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_memory_source_authority_guard_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> MemorySourceAuthorityGuardFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return MemorySourceAuthorityGuardFixture.model_validate(payload)


def project_memory_source_authority_guard_fixture(
    fixture: MemorySourceAuthorityGuardFixture | Mapping[str, Any],
) -> MemorySourceAuthorityProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews: dict[str, str] = {}
    control_requests: dict[str, dict[str, object]] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    for case in safe_fixture.cases:
        preview = _public_preview(case)
        public_previews[case.case_id] = preview
        if case.control_request is not None:
            control_requests[case.case_id] = _public_control_request(case.control_request)
        snapshot = _case_snapshot(case, preview=preview)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot
    return MemorySourceAuthorityProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveMemoryRuntime=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byDecision=dict(Counter(case.decision for case in safe_fixture.cases)),
        byMemoryMode=dict(Counter(case.memory_mode for case in safe_fixture.cases)),
        bySourceAuthority=dict(Counter(case.source_authority for case in safe_fixture.cases)),
        publicPreviews=public_previews,
        controlRequests=control_requests,
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: MemorySourceAuthorityGuardFixture | Mapping[str, Any],
) -> MemorySourceAuthorityGuardFixture:
    if isinstance(fixture, MemorySourceAuthorityGuardFixture):
        return MemorySourceAuthorityGuardFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return MemorySourceAuthorityGuardFixture.model_validate(fixture)


def _case_snapshot(case: MemorySourceAuthorityCase, *, preview: str) -> dict[str, object]:
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "category": case.category,
        "memoryMode": case.memory_mode,
        "sourceAuthority": case.source_authority,
        "provider": case.provider.model_dump(by_alias=True, mode="json", warnings=False),
        "recallIntent": case.recall_intent,
        "writeIntent": case.write_intent,
        "redactIntent": case.redact_intent,
        "decision": case.decision,
        "redactionStatus": case.redaction_status,
        "promptProjectionAllowed": case.prompt_projection_allowed,
        "publicProjectionAllowed": case.public_projection_allowed,
        "publicPreview": preview,
        "writeClaimAllowed": case.write_claim_allowed,
        "writeReceiptRef": case.write_receipt_ref,
        "memoryRedactAuthority": case.memory_redact_authority,
        "backgroundOnly": case.background_only,
        "priority": case.priority,
        "currentSourcePriority": case.current_source_priority,
        "noUserFacingMemoryClaim": case.no_user_facing_memory_claim,
        "reasonCodes": case.reason_codes,
        "scope": case.scope.model_dump(by_alias=True, mode="json", warnings=False),
        "evidenceRefs": case.evidence_refs,
        "auditRefs": case.audit_refs,
    }
    if case.source_metadata is not None:
        snapshot["sourceMetadata"] = case.source_metadata.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )
    if case.continuity_metadata is not None:
        snapshot["continuityMetadata"] = case.continuity_metadata.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )
    if case.control_request is not None:
        snapshot["controlRequest"] = _public_control_request(case.control_request)
    return snapshot


def _public_control_request(request: ControlRequest) -> dict[str, object]:
    return {
        "requestId": request.request_id,
        "turnId": request.turn_id,
        "toolName": request.tool_name,
        "reason": request.reason,
    }


def _public_preview(case: MemorySourceAuthorityCase) -> str:
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(case.public_preview))
    return redacted


def _validate_memory_mode(case: MemorySourceAuthorityCase) -> None:
    if case.memory_mode == "incognito":
        if case.decision != "block":
            raise ValueError("incognito memory mode must block recall and writes")
        if case.recall_intent and "incognito_blocks_recall" not in case.reason_codes:
            raise ValueError("incognito recall block requires reason code")
        if case.write_intent and "incognito_blocks_writes" not in case.reason_codes:
            raise ValueError("incognito write block requires reason code")
        if case.public_projection_allowed or case.write_claim_allowed:
            raise ValueError("incognito memory mode cannot project or claim memory")
    if case.memory_mode == "read_only" and case.write_intent:
        if case.decision != "block" or "read_only_blocks_writes" not in case.reason_codes:
            raise ValueError("read_only memory write intent must be blocked")
        if case.write_claim_allowed:
            raise ValueError("read_only memory mode cannot claim writes")


def _validate_source_authority(case: MemorySourceAuthorityCase) -> None:
    if case.source_authority == "long_term_disabled":
        if case.decision != "block":
            raise ValueError("disabled source authority must block long-term memory")
        if "source_authority_disables_long_term_memory" not in case.reason_codes and not (
            case.memory_mode == "incognito"
        ):
            raise ValueError("disabled source authority requires reason code")
    if case.source_authority == "background_only":
        if not case.background_only or case.priority != "background":
            raise ValueError("background-only memory must be lower-priority metadata")
        if case.current_source_priority != "current_workspace_user_instruction":
            raise ValueError("background-only memory must stay below current source authority")
    if case.source_authority == "child_isolated":
        if case.decision != "block":
            raise ValueError("child-isolated memory must block implicit inheritance")


def _validate_provider_status(case: MemorySourceAuthorityCase) -> None:
    if case.provider.provider_status == "unavailable":
        if case.decision != "fail_open_no_claim":
            raise ValueError("unavailable memory provider must fail open without claims")
        if not case.no_user_facing_memory_claim or case.write_claim_allowed:
            raise ValueError("unavailable memory provider cannot produce memory claims")
    if case.provider.provider_id == "none" and case.decision not in {"block", "approval_required"}:
        raise ValueError("none provider may only appear in blocked or approval metadata cases")


def _validate_decision(case: MemorySourceAuthorityCase) -> None:
    if case.decision == "allow_metadata_only":
        if case.write_intent or case.redact_intent:
            raise ValueError("allow_metadata_only may only represent recall metadata")
        if not case.no_user_facing_memory_claim:
            raise ValueError("metadata-only recall must not claim memory use to users")
    elif case.decision == "block":
        if case.write_claim_allowed or case.prompt_projection_allowed:
            raise ValueError("blocked memory case cannot project prompt or write claims")
    elif case.decision == "fail_open_no_claim":
        if not case.no_user_facing_memory_claim:
            raise ValueError("fail-open memory case must be no-claim")
        if case.prompt_projection_allowed or case.write_claim_allowed:
            raise ValueError("fail-open memory case cannot project or claim memory")
    else:
        if case.control_request is None:
            raise ValueError("approval-required memory case must include ControlRequest")
        if case.write_claim_allowed and case.write_receipt_ref is None:
            raise ValueError("approval-required memory write claim needs receipt")


def _validate_redact_authority(case: MemorySourceAuthorityCase) -> None:
    if case.redact_intent:
        if case.memory_redact_authority != "openmagi_memory_redact":
            raise ValueError("memory redact intent must route through OpenMagi MemoryRedact")
        if case.provider.provider_delete_or_redact_allowed:
            raise ValueError("provider delete/redact cannot supersede MemoryRedact authority")
        if case.decision != "approval_required":
            raise ValueError("memory redaction metadata must require approval")
    elif case.memory_redact_authority != "none":
        raise ValueError("memoryRedactAuthority requires redactIntent")


def _validate_child_scope(case: MemorySourceAuthorityCase) -> None:
    if case.category == "child_agent_memory_scope_isolated":
        if case.scope.agent_role != "child" or case.scope.child_execution_id is None:
            raise ValueError("child memory isolation case must use child scope")
        if case.scope.inherited_from_parent:
            raise ValueError("child memory scope must remain isolated unless explicitly inherited")
        if case.decision != "block":
            raise ValueError("child memory isolation must block implicit recall/write")


def _validate_source_metadata(case: MemorySourceAuthorityCase) -> None:
    if case.source_metadata is None:
        return
    policy = case.source_metadata.effective_long_term_memory_policy
    if case.source_authority == "background_only" and policy != "background_only":
        raise ValueError("background-only source authority metadata cannot claim another policy")
    if case.source_authority == "long_term_disabled" and policy != "disabled":
        raise ValueError("disabled source authority metadata cannot claim another policy")
    if policy == "background_only":
        if case.source_authority != "background_only" or not case.background_only:
            raise ValueError("background-only source metadata must match source authority")
        if case.priority != "background":
            raise ValueError("background-only source metadata must stay background priority")
    if policy == "disabled":
        if case.source_authority != "long_term_disabled" or case.decision != "block":
            raise ValueError("disabled source metadata must block long-term memory")
        if case.recall_intent is not True:
            raise ValueError("disabled source metadata must represent blocked recall")


def _validate_continuity_metadata(case: MemorySourceAuthorityCase) -> None:
    if case.continuity_metadata is None:
        return
    continuity = case.continuity_metadata.continuity
    if continuity == "background" and case.priority != "background":
        raise ValueError("background continuity metadata must stay background priority")
    if case.continuity_metadata.stale_promotion_retry is not None:
        if "memory_continuity_guard_metadata" not in case.reason_codes:
            raise ValueError("stale promotion metadata requires memory continuity guard reason")
    if case.continuity_metadata.memory_recall_source == "root":
        if case.continuity_metadata.path not in {"memory/ROOT.md", "MEMORY.md"}:
            raise ValueError("root memory continuity metadata must use root memory paths")


def _validate_expected_reason(case: MemorySourceAuthorityCase) -> None:
    expected_reason = _expected_reason_for_category(case.category)
    if expected_reason not in case.reason_codes:
        raise ValueError("memory/source authority reasonCodes must include category reason")


def _expected_reason_for_category(category: MemorySourceAuthorityCategory) -> str:
    return {
        "normal_recall_metadata": "normal_recall_metadata_only",
        "read_only_recall_metadata": "read_only_recall_metadata_only",
        "read_only_write_blocked": "read_only_blocks_writes",
        "incognito_recall_blocked": "incognito_blocks_recall",
        "incognito_write_blocked": "incognito_blocks_writes",
        "source_authority_long_term_disabled": "source_authority_disables_long_term_memory",
        "redaction_failure_blocks_projection": "redaction_failed_blocks_projection",
        "provider_unavailable_fail_open_no_claim": "provider_unavailable_fail_open_no_claim",
        "explicit_write_requires_receipt_before_claim": "write_receipt_required_before_claim",
        "memory_redact_authority_supersedes_provider": (
            "memory_redact_authority_supersedes_provider"
        ),
        "stale_conflicting_memory_background_only": "stale_conflicting_memory_background_only",
        "child_agent_memory_scope_isolated": "child_memory_scope_isolated",
        "selected_kb_current_source_background_only": "current_source_selected_kb_background_only",
        "attachment_current_source_background_only": "current_source_attachment_background_only",
        "image_current_source_background_only": "current_source_image_background_only",
        "classifier_disabled_blocks_recall": "classifier_disabled_long_term_memory",
        "root_memory_background_without_continuation": "root_memory_background_without_continuation",
        "qmd_active_with_continuation_overlap": "qmd_active_with_continuation_overlap",
        "stale_background_memory_retry_metadata": "memory_continuity_guard_metadata",
        "passive_background_memory_reference_audit_ok": "memory_continuity_guard_metadata",
        "hipocampus_root_precedes_legacy_memory_metadata": "hipocampus_root_precedes_legacy_memory",
    }[category]


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _FORBIDDEN_PATH_RE.search(rendered):
        raise ValueError("memory/source authority public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("memory/source authority public snapshot contains unsafe data")


def _resolve_fixture_path(path: str | Path, *, fixture_root: str | Path | None) -> Path:
    _reject_unsafe_path_text(str(path))
    candidate = Path(path)
    if fixture_root is None:
        resolved = candidate.resolve(strict=True)
        _reject_unsafe_path_text(str(resolved))
        return resolved
    _reject_unsafe_path_text(str(fixture_root))
    resolved_root = Path(fixture_root).resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_root))
    if not candidate.is_absolute():
        candidate = resolved_root / candidate
    resolved_candidate = candidate.resolve(strict=True)
    _reject_unsafe_path_text(str(resolved_candidate))
    if not resolved_candidate.is_relative_to(resolved_root):
        raise ValueError("memory/source authority fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("memory/source authority fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("memory/source authority fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("memory/source authority fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("memory/source authority fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("memory/source authority fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("memory/source authority mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("memory/source authority fixture values must be JSON-compatible")


def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    chars: list[str] = []
    previous_was_separator = False
    for char in value:
        if char.isalnum():
            chars.append(char.lower())
            previous_was_separator = False
        elif not previous_was_separator:
            chars.append("_")
            previous_was_separator = True
    return "".join(chars).strip("_")


__all__ = [
    "MemoryGuardScopeMetadata",
    "MemoryProviderCandidate",
    "MemorySourceAuthorityAttachmentFlags",
    "MemorySourceAuthorityCase",
    "MemorySourceAuthorityGuardFixture",
    "MemorySourceAuthorityProjection",
    "load_memory_source_authority_guard_fixture",
    "project_memory_source_authority_guard_fixture",
]
