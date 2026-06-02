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
from magi_agent.tools.manifest import ToolManifest
from magi_agent.transport.tool_preview import sanitize_tool_preview


OfficePackId = Literal[
    "openmagi.office-automation",
    "openmagi.spreadsheet-automation",
    "openmagi.browser-automation",
    "openmagi.document-review",
    "openmagi.lightweight-scripting",
]
OfficeAutomationCategory = Literal[
    "spreadsheet_cleanup_validation",
    "spreadsheet_reconciliation_preview",
    "spreadsheet_external_write_approval",
    "browser_extract_domain_allowlisted",
    "browser_download_long_tool_metadata",
    "browser_form_submit_approval_ack",
    "document_extract_fields_source_refs",
    "document_redline_no_source_mutation",
    "document_deliverable_render_required",
    "lightweight_script_scratch_metadata",
    "lightweight_script_network_write_denied",
    "office_composite_report_pack",
]
OfficeExecutionSurface = Literal[
    "atomic_tool",
    "controlled_composable",
    "generated_script",
    "adk_artifact_service",
    "connector_tool",
]
OfficeDecision = Literal[
    "allow_metadata_only",
    "approval_required",
    "deny",
    "dry_run_only",
    "block_until_evidence",
]
OfficeUnitOfWork = Literal["inline_tool_call", "long_tool_job", "metadata_only"]

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
_SECRET_SHAPED_VALUE_RE = re.compile(
    r"(?:Bearer\s+[A-Za-z0-9._~+/=-]{8,}|ghp_[A-Za-z0-9_]{8,}|"
    r"sk-(?:live|test|office)?[-_A-Za-z0-9]{8,}|"
    r"[A-Z0-9_]*(?:SECRET|TOKEN|KEY)[A-Z0-9_]*\s*[:=]\s*[^,\s}{]{4,})",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_officesecret",
    "sk-office-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "private connector token",
    "hidden reasoning",
    "raw tool args",
    "pythonResponseAuthority",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "artifact_written",
        "browser_session_attached",
        "canary_traffic_attached",
        "connector_called",
        "evidence_block_enabled",
        "external_write_performed",
        "generated_code_executed",
        "live_tool_dispatched",
        "memory_provider_called",
        "production_authority",
        "production_storage_written",
        "route_or_api_attached",
        "shell_or_code_executed",
        "telegram_attached",
    }
)
_REQUIRED_CATEGORIES = set(OfficeAutomationCategory.__args__)  # type: ignore[attr-defined]
_SHA256_REF_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class OfficeAutomationAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    generated_code_executed: Literal[False] = Field(
        default=False,
        alias="generatedCodeExecuted",
    )
    browser_session_attached: Literal[False] = Field(
        default=False,
        alias="browserSessionAttached",
    )
    connector_called: Literal[False] = Field(default=False, alias="connectorCalled")
    artifact_written: Literal[False] = Field(default=False, alias="artifactWritten")
    external_write_performed: Literal[False] = Field(
        default=False,
        alias="externalWritePerformed",
    )
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
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
        "live_tool_dispatched",
        "shell_or_code_executed",
        "generated_code_executed",
        "browser_session_attached",
        "connector_called",
        "artifact_written",
        "external_write_performed",
        "memory_provider_called",
        "production_storage_written",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
        "production_authority",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class OfficeAutomationCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: OfficeAutomationCategory
    recipe_pack_id: OfficePackId = Field(alias="recipePackId")
    task_profile: str = Field(alias="taskProfile")
    execution_surface: OfficeExecutionSurface = Field(alias="executionSurface")
    tool: ToolManifest
    decision: OfficeDecision
    unit_of_work: OfficeUnitOfWork = Field(alias="unitOfWork")
    public_preview: str = Field(alias="publicPreview")
    domain_allowlisted: bool = Field(default=False, alias="domainAllowlisted")
    network_intent: bool = Field(default=False, alias="networkIntent")
    network_write_intent: bool = Field(default=False, alias="networkWriteIntent")
    external_write_intent: bool = Field(default=False, alias="externalWriteIntent")
    requires_external_ack: bool = Field(default=False, alias="requiresExternalAck")
    external_ack_received: Literal[False] = Field(default=False, alias="externalAckReceived")
    long_running_tool_eligible: bool = Field(default=False, alias="longRunningToolEligible")
    generated_code_metadata_only: bool = Field(
        default=False,
        alias="generatedCodeMetadataOnly",
    )
    shell_or_code_execution_allowed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecutionAllowed",
    )
    script_artifact_ref: str | None = Field(default=None, alias="scriptArtifactRef")
    script_hash: str | None = Field(default=None, alias="scriptHash")
    artifact_refs: tuple[str, ...] = Field(default=(), alias="artifactRefs")
    source_refs: tuple[str, ...] = Field(default=(), alias="sourceRefs")
    composed_pack_ids: tuple[OfficePackId, ...] = Field(default=(), alias="composedPackIds")
    evidence_requirements: tuple[str, ...] = Field(default=(), alias="evidenceRequirements")
    render_verification_required: bool = Field(
        default=False,
        alias="renderVerificationRequired",
    )
    render_verification_passed: bool = Field(default=False, alias="renderVerificationPassed")
    delivery_claim_allowed: bool = Field(default=False, alias="deliveryClaimAllowed")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    control_request: ControlRequest | None = Field(default=None, alias="controlRequest")
    attachment_flags: OfficeAutomationAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        for value in (
            self.case_id,
            self.task_profile,
            self.public_preview,
            *(self.artifact_refs),
            *(self.source_refs),
            *(self.evidence_requirements),
            *(self.reason_codes),
        ):
            if not value.strip():
                raise ValueError("office automation metadata fields must be non-empty")
            _validate_public_value(value)

        if self.recipe_pack_id != "openmagi.office-automation" and self.composed_pack_ids:
            raise ValueError("only the umbrella office pack can declare composedPackIds")
        if self.recipe_pack_id == "openmagi.office-automation" and not self.composed_pack_ids:
            raise ValueError("umbrella office pack requires composedPackIds")

        if self.decision == "approval_required" and self.control_request is None:
            raise ValueError("approval-required office actions require ControlRequest metadata")
        if self.decision != "approval_required" and self.control_request is not None:
            raise ValueError("ControlRequest metadata is only valid for approval decisions")

        if self.network_intent and not self.domain_allowlisted and self.decision != "deny":
            raise ValueError("network office actions require domain allowlist or denial")
        if self.external_write_intent and self.decision != "approval_required":
            raise ValueError("external writes require approval-required decision metadata")
        if self.network_write_intent and self.decision not in {"approval_required", "deny"}:
            raise ValueError("network writes require approval or denial metadata")
        if self.requires_external_ack and self.decision != "approval_required":
            raise ValueError("external acknowledgement metadata requires approval posture")

        if self.tool.adk_tool_type == "LongRunningFunctionTool":
            if not self.long_running_tool_eligible or self.unit_of_work != "long_tool_job":
                raise ValueError("LongRunningFunctionTool metadata is only for long tool jobs")
        elif self.long_running_tool_eligible:
            raise ValueError("longRunningToolEligible requires LongRunningFunctionTool metadata")

        if self.execution_surface == "generated_script":
            if not self.generated_code_metadata_only:
                raise ValueError("generated script cases must be metadata-only")
            if self.script_artifact_ref is None or self.script_hash is None:
                raise ValueError("generated script metadata requires artifact ref and hash")
            _validate_public_value(self.script_artifact_ref)
            if not _SHA256_REF_RE.fullmatch(self.script_hash):
                raise ValueError("scriptHash must be sha256-prefixed lowercase hex")
        elif (
            self.generated_code_metadata_only
            or self.script_artifact_ref is not None
            or self.script_hash is not None
        ):
            raise ValueError("script metadata is only valid on generated_script surface")

        if self.render_verification_required and self.delivery_claim_allowed:
            if not self.render_verification_passed:
                raise ValueError("delivery claims require passing render verification")
        if self.delivery_claim_allowed and not self.render_verification_passed:
            raise ValueError("deliveryClaimAllowed requires render verification")

        return self


class OfficeAutomationContractFixture(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    cases: tuple[OfficeAutomationCase, ...]
    attachment_flags: OfficeAutomationAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        categories = {case.category for case in self.cases}
        if categories != _REQUIRED_CATEGORIES:
            missing = sorted(_REQUIRED_CATEGORIES - categories)
            extra = sorted(categories - _REQUIRED_CATEGORIES)
            raise ValueError(
                "office automation fixture must cover every category: "
                f"missing={missing}, extra={extra}"
            )
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("office automation caseId values must be unique")
        if not self.fixture_id.strip():
            raise ValueError("fixtureId must be non-empty")
        _validate_public_value(self.fixture_id)
        return self


class OfficeAutomationContractProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    version: int
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    no_live_execution: Literal[True] = Field(default=True, alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_pack: dict[OfficePackId, int] = Field(alias="byPack")
    by_execution_surface: dict[OfficeExecutionSurface, int] = Field(
        alias="byExecutionSurface",
    )
    by_decision: dict[OfficeDecision, int] = Field(alias="byDecision")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    control_requests: dict[str, dict[str, str]] = Field(alias="controlRequests")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")
    attachment_flags: OfficeAutomationAttachmentFlags = Field(alias="attachmentFlags")


def load_office_automation_contract_fixture(
    fixture_name: str,
    *,
    fixture_root: Path | None = None,
) -> OfficeAutomationContractFixture:
    root = fixture_root or Path(__file__).parents[2] / "tests" / "fixtures" / "office_automation"
    payload = json.loads((root / fixture_name).read_text(encoding="utf-8"))
    return OfficeAutomationContractFixture.model_validate(payload)


def project_office_automation_contract_fixture(
    fixture: OfficeAutomationContractFixture,
) -> OfficeAutomationContractProjection:
    pack_counts = Counter(case.recipe_pack_id for case in fixture.cases)
    surface_counts = Counter(case.execution_surface for case in fixture.cases)
    decision_counts = Counter(case.decision for case in fixture.cases)
    control_requests = {
        case.case_id: {
            "requestId": case.control_request.request_id,
            "turnId": case.control_request.turn_id,
            "toolName": case.control_request.tool_name,
            "reason": case.control_request.reason,
        }
        for case in fixture.cases
        if case.control_request is not None
    }
    return OfficeAutomationContractProjection(
        fixtureId=fixture.fixture_id,
        version=fixture.version,
        localDiagnostic=True,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in fixture.cases),
        byPack=dict(pack_counts),
        byExecutionSurface=dict(surface_counts),
        byDecision=dict(decision_counts),
        publicPreviews={case.case_id: case.public_preview for case in fixture.cases},
        controlRequests=control_requests,
        caseSnapshots={
            case.case_id: {
                "recipePackId": case.recipe_pack_id,
                "taskProfile": case.task_profile,
                "executionSurface": case.execution_surface,
                "toolName": case.tool.name,
                "adkToolType": case.tool.adk_tool_type,
                "decision": case.decision,
                "unitOfWork": case.unit_of_work,
                "evidenceRequirements": case.evidence_requirements,
                "artifactRefs": case.artifact_refs,
                "sourceRefs": case.source_refs,
                "deliveryClaimAllowed": case.delivery_claim_allowed,
                "externalWriteIntent": case.external_write_intent,
                "attachmentFlags": case.attachment_flags.model_dump(by_alias=True),
            }
            for case in fixture.cases
        },
        attachmentFlags=fixture.attachment_flags,
    )


def _validate_public_value(value: str) -> None:
    if _PRODUCTION_PATH_RE.search(value) or _FORBIDDEN_PATH_RE.search(value):
        raise ValueError("office automation metadata cannot expose production paths")
    if _SECRET_SHAPED_VALUE_RE.search(value):
        raise ValueError("office automation metadata cannot expose secret-shaped values")
    sanitized = sanitize_tool_preview(value)
    for token in _FORBIDDEN_PUBLIC_TOKENS:
        if token in sanitized:
            raise ValueError("office automation metadata contains unsafe public token")


def _reject_unsafe_raw_value(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _camel_to_snake(str(key))
            if normalized_key in _FORBIDDEN_RAW_KEY_TOKENS and item is True:
                raise ValueError(f"{key} cannot be true in office automation fixtures")
            _reject_unsafe_raw_value(item)
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_unsafe_raw_value(item)
        return

    if isinstance(value, str):
        _validate_public_value(value)
        return

    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("office automation metadata must use finite numbers")


def _camel_to_snake(value: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()
