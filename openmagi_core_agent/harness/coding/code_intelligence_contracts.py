from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CodeIntelligenceOperation = Literal[
    "diagnostics",
    "definition",
    "references",
    "hover",
    "symbols",
    "rename",
    "code_action",
]
CodeIntelligenceStatus = Literal["projected", "provider_unavailable", "blocked"]
CodeIntelligenceReportStatus = Literal["projected", "provider_unavailable", "blocked"]
AdkArtifactServiceBoundary = Literal["ArtifactService"]

REQUIRED_CODE_INTELLIGENCE_OPERATIONS: tuple[CodeIntelligenceOperation, ...] = (
    "diagnostics",
    "definition",
    "references",
    "hover",
    "symbols",
    "rename",
    "code_action",
)
ADK_PRIMITIVE_NAMES: tuple[
    Literal[
        "FunctionTool.name",
        "FunctionTool.description",
        "FunctionTool.input_schema",
        "Agent.metadata",
    ],
    ...,
] = (
    "FunctionTool.name",
    "FunctionTool.description",
    "FunctionTool.input_schema",
    "Agent.metadata",
)

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_REF_RE = re.compile(
    r"^(?:"
    r"file-ref:sha256:[a-f0-9]{64}|"
    r"source-ref:sha256:[a-f0-9]{64}|"
    r"workspace-ref:sha256:[a-f0-9]{64}|"
    r"provider-ref:[A-Za-z][A-Za-z0-9_.:-]{1,120}|"
    r"operation-ref:[A-Za-z][A-Za-z0-9_.:-]{1,120}|"
    r"code-action:sha256:[a-f0-9]{64}|"
    r"claim-ref:sha256:[a-f0-9]{64}|"
    r"test-ref:sha256:[a-f0-9]{64}|"
    r"artifact:code-intelligence-diagnostics:sha256:[a-f0-9]{64}"
    r")$"
)
_LANGUAGE_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+.-]{0,40}$")
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")
_PRIVATE_TEXT_RE = re.compile(
    r"(?:"
    r"/Users(?:/[^\s,;}\"']*)?|"
    r"/home(?:/[^\s,;}\"']*)?|"
    r"/workspace(?:/[^\s,;}\"']*)?|"
    r"/data/bots(?:/[^\s,;}\"']*)?|"
    r"/var/lib(?:/[^\s,;}\"']*)?|"
    r"authorization|"
    r"bearer|"
    r"cookie|"
    r"token|"
    r"secret|"
    r"password|"
    r"credential|"
    r"private[_-]?key|"
    r"raw[_ -]?(?:tool|prompt|output|result|log|edit|lsp)|"
    r"newText|"
    r"toolhost|"
    r"dispatcher|"
    r"registry|"
    r"openmagi_core_agent\.runtime"
    r")",
    re.IGNORECASE,
)


class CodeIntelligenceAuthorityFlags(BaseModel):
    model_config = _MODEL_CONFIG

    lsp_provider_attached: Literal[False] = Field(default=False, alias="lspProviderAttached")
    lsp_subprocess_started: Literal[False] = Field(
        default=False,
        alias="lspSubprocessStarted",
    )
    subprocess_started: Literal[False] = Field(default=False, alias="subprocessStarted")
    workspace_mutated: Literal[False] = Field(default=False, alias="workspaceMutated")
    model_provider_invoked: Literal[False] = Field(default=False, alias="modelProviderInvoked")
    tool_executed: Literal[False] = Field(default=False, alias="toolExecuted")
    core_runtime_touched: Literal[False] = Field(default=False, alias="coreRuntimeTouched")
    mcp_or_browser_activated: Literal[False] = Field(
        default=False,
        alias="mcpOrBrowserActivated",
    )

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


class CodeIntelligenceSpan(BaseModel):
    model_config = _MODEL_CONFIG

    file_ref: str = Field(alias="fileRef")
    start_line: int = Field(alias="startLine", ge=1)
    start_column: int = Field(alias="startColumn", ge=1)
    end_line: int = Field(alias="endLine", ge=1)
    end_column: int = Field(alias="endColumn", ge=1)
    source_digest: str = Field(alias="sourceDigest")

    @field_validator("file_ref")
    @classmethod
    def _validate_file_ref(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("source_digest")
    @classmethod
    def _validate_source_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @model_validator(mode="after")
    def _validate_span_order(self) -> Self:
        if (self.end_line, self.end_column) < (self.start_line, self.start_column):
            raise ValueError("span end must not precede start")
        return self


class CodeIntelligenceSourceMetadata(BaseModel):
    model_config = _MODEL_CONFIG

    source_ref: str = Field(alias="sourceRef")
    workspace_ref: str = Field(alias="workspaceRef")
    language_id: str = Field(alias="languageId")
    provider_ref: str = Field(alias="providerRef")

    @field_validator("source_ref", "workspace_ref", "provider_ref")
    @classmethod
    def _validate_refs(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("language_id")
    @classmethod
    def _validate_language_id(cls, value: str) -> str:
        cleaned = value.strip()
        _reject_private_text(cleaned, "languageId")
        if _LANGUAGE_ID_RE.fullmatch(cleaned) is None:
            raise ValueError("languageId must be a public language identifier")
        return cleaned


class CodeIntelligenceObservation(BaseModel):
    model_config = _MODEL_CONFIG

    operation: CodeIntelligenceOperation
    status: CodeIntelligenceStatus
    span: CodeIntelligenceSpan
    source_metadata: CodeIntelligenceSourceMetadata = Field(alias="sourceMetadata")
    result_digest: str = Field(alias="resultDigest")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")

    @field_validator("result_digest")
    @classmethod
    def _validate_result_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: Sequence[str]) -> tuple[str, ...]:
        return _safe_reason_codes(value)


class CodeActionProjection(BaseModel):
    model_config = _MODEL_CONFIG

    action_id: str = Field(alias="actionId")
    title_digest: str = Field(alias="titleDigest")
    target_files: tuple[str, ...] = Field(alias="targetFiles")
    edit_count: int = Field(alias="editCount", ge=0)
    operation_ref: str = Field(alias="operationRef")

    @field_validator("action_id", "operation_ref")
    @classmethod
    def _validate_refs(cls, value: str) -> str:
        return _safe_ref(value)

    @field_validator("title_digest")
    @classmethod
    def _validate_title_digest(cls, value: str) -> str:
        return _safe_digest(value)

    @field_validator("target_files")
    @classmethod
    def _validate_target_files(cls, value: Sequence[str]) -> tuple[str, ...]:
        refs = tuple(_safe_ref(item) for item in value)
        if not refs:
            raise ValueError("targetFiles must include at least one file ref")
        return refs


class CodeIntelligenceReport(BaseModel):
    model_config = _MODEL_CONFIG

    status: CodeIntelligenceReportStatus
    success: bool
    operation_statuses: Mapping[CodeIntelligenceOperation, CodeIntelligenceStatus] = Field(
        alias="operationStatuses",
    )
    observations: tuple[CodeIntelligenceObservation, ...] = ()
    code_actions: tuple[CodeActionProjection, ...] = Field(default=(), alias="codeActions")
    diagnostics_report_ref: str | None = Field(default=None, alias="diagnosticsReportRef")
    adk_artifact_service_boundary: AdkArtifactServiceBoundary = Field(
        default="ArtifactService",
        alias="adkArtifactServiceBoundary",
    )
    adk_primitive_names: tuple[
        Literal[
            "FunctionTool.name",
            "FunctionTool.description",
            "FunctionTool.input_schema",
            "Agent.metadata",
        ],
        ...,
    ] = Field(default=ADK_PRIMITIVE_NAMES, alias="adkPrimitiveNames")
    code_intelligence_claim_refs: tuple[str, ...] = Field(
        default=(),
        alias="codeIntelligenceClaimRefs",
    )
    test_verification_evidence_refs: tuple[str, ...] = Field(
        default=(),
        alias="testVerificationEvidenceRefs",
    )
    test_verification_satisfied: bool = Field(
        default=False,
        alias="testVerificationSatisfied",
    )
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    default_off: Literal[True] = Field(default=True, alias="defaultOff")
    local_only: Literal[True] = Field(default=True, alias="localOnly")
    live_authority_allowed: Literal[False] = Field(default=False, alias="liveAuthorityAllowed")
    core_touch_allowed: Literal[False] = Field(default=False, alias="coreTouchAllowed")
    authority_flags: CodeIntelligenceAuthorityFlags = Field(
        default_factory=CodeIntelligenceAuthorityFlags,
        alias="authorityFlags",
    )

    @model_validator(mode="before")
    @classmethod
    def _force_inert_contract(cls, value: object) -> dict[str, object]:
        payload = dict(value) if isinstance(value, Mapping) else {}
        payload["defaultOff"] = True
        payload["localOnly"] = True
        payload["liveAuthorityAllowed"] = False
        payload["coreTouchAllowed"] = False
        payload["authorityFlags"] = CodeIntelligenceAuthorityFlags().model_dump(
            by_alias=True,
            mode="python",
        )
        claim_refs = tuple(payload.get("codeIntelligenceClaimRefs") or ())
        test_refs = tuple(payload.get("testVerificationEvidenceRefs") or ())
        payload["testVerificationSatisfied"] = bool(test_refs)
        reason_codes = tuple(payload.get("reasonCodes") or ())
        if claim_refs and not test_refs and "test_verification_evidence_required" not in reason_codes:
            payload["reasonCodes"] = (*reason_codes, "test_verification_evidence_required")
        return payload

    @field_validator("operation_statuses")
    @classmethod
    def _validate_operation_statuses(
        cls,
        value: Mapping[CodeIntelligenceOperation, CodeIntelligenceStatus],
    ) -> Mapping[CodeIntelligenceOperation, CodeIntelligenceStatus]:
        statuses = dict(value)
        if not statuses:
            raise ValueError("operationStatuses must not be empty")
        return statuses

    @field_validator("diagnostics_report_ref")
    @classmethod
    def _validate_diagnostics_report_ref(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _safe_ref(value)

    @field_validator("adk_primitive_names")
    @classmethod
    def _validate_adk_names(cls, value: Sequence[str]) -> tuple[str, ...]:
        names = tuple(value)
        if names != ADK_PRIMITIVE_NAMES:
            raise ValueError("adkPrimitiveNames must remain ADK metadata names only")
        return names

    @field_validator("code_intelligence_claim_refs")
    @classmethod
    def _validate_claim_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        return tuple(_safe_ref(item) for item in value)

    @field_validator("test_verification_evidence_refs")
    @classmethod
    def _validate_test_refs(cls, value: Sequence[str]) -> tuple[str, ...]:
        refs = tuple(_safe_ref(item) for item in value)
        if any(not ref.startswith("test-ref:sha256:") for ref in refs):
            raise ValueError("testVerificationEvidenceRefs must be test artifact refs")
        return refs

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: Sequence[str]) -> tuple[str, ...]:
        return _safe_reason_codes(value)

    @model_validator(mode="after")
    def _validate_report_consistency(self) -> Self:
        if self.status == "provider_unavailable":
            if self.success:
                raise ValueError("provider-unavailable report cannot be success")
            if any(status != "provider_unavailable" for status in self.operation_statuses.values()):
                raise ValueError("provider-unavailable report must mark operations unavailable")
            if self.observations or self.code_actions or self.diagnostics_report_ref is not None:
                raise ValueError("provider-unavailable report cannot carry projected metadata")
        if self.success and self.status != "projected":
            raise ValueError("successful report must be projected")
        projected_observations: set[CodeIntelligenceOperation] = set()
        for observation in self.observations:
            if observation.status == "projected":
                projected_observations.add(observation.operation)
        for operation, status in self.operation_statuses.items():
            if status == "projected" and operation not in projected_observations:
                raise ValueError("projected operations require stable observation metadata")
        if self.code_actions and "code_action" not in projected_observations:
            raise ValueError("codeActions require projected code_action operation metadata")
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
        _ = deep
        payload = self.model_dump(by_alias=True, mode="python", warnings=False)
        if update:
            payload.update(dict(update))
        return type(self).model_validate(payload)

    def public_projection(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, mode="json")


def build_code_intelligence_contract(
    *,
    providerAvailable: bool,
    requestedOperations: Sequence[CodeIntelligenceOperation] = REQUIRED_CODE_INTELLIGENCE_OPERATIONS,
    observations: Sequence[CodeIntelligenceObservation | Mapping[str, object]] = (),
    codeActions: Sequence[CodeActionProjection | Mapping[str, object]] = (),
    diagnosticsReportDigest: str | None = None,
    codeIntelligenceClaims: Sequence[str] = (),
    testVerificationEvidenceRefs: Sequence[str] = (),
) -> CodeIntelligenceReport:
    requested_operations = tuple(requestedOperations)
    if not requested_operations:
        raise ValueError("requestedOperations must not be empty")

    if not providerAvailable:
        return CodeIntelligenceReport.model_validate(
            {
                "status": "provider_unavailable",
                "success": False,
                "operationStatuses": {
                    operation: "provider_unavailable" for operation in requested_operations
                },
                "observations": (),
                "codeActions": (),
                "diagnosticsReportRef": None,
                "codeIntelligenceClaimRefs": tuple(codeIntelligenceClaims),
                "testVerificationEvidenceRefs": tuple(testVerificationEvidenceRefs),
                "reasonCodes": ("provider_unavailable",),
            }
        )

    projected_observations = tuple(
        item
        if isinstance(item, CodeIntelligenceObservation)
        else CodeIntelligenceObservation.model_validate(item)
        for item in observations
    )
    projected_actions = tuple(
        item if isinstance(item, CodeActionProjection) else CodeActionProjection.model_validate(item)
        for item in codeActions
    )
    operation_statuses: dict[CodeIntelligenceOperation, CodeIntelligenceStatus] = {
        operation: "blocked" for operation in requested_operations
    }
    for observation in projected_observations:
        operation_statuses[observation.operation] = observation.status

    status: CodeIntelligenceReportStatus = (
        "projected"
        if projected_observations
        and all(item == "projected" for item in operation_statuses.values())
        else "blocked"
    )
    success = status == "projected"
    reason_codes: tuple[str, ...] = ()
    if status == "blocked":
        reason_codes = ("code_intelligence_metadata_incomplete",)

    diagnostics_ref = None
    if any(observation.operation == "diagnostics" for observation in projected_observations):
        diagnostics_ref = _diagnostics_artifact_ref(
            diagnosticsReportDigest
            or _digest(
                tuple(
                    observation.model_dump(by_alias=True, mode="json")
                    for observation in projected_observations
                    if observation.operation == "diagnostics"
                )
            )
        )

    return CodeIntelligenceReport.model_validate(
        {
            "status": status,
            "success": success,
            "operationStatuses": operation_statuses,
            "observations": projected_observations,
            "codeActions": projected_actions,
            "diagnosticsReportRef": diagnostics_ref,
            "codeIntelligenceClaimRefs": tuple(codeIntelligenceClaims),
            "testVerificationEvidenceRefs": tuple(testVerificationEvidenceRefs),
            "reasonCodes": reason_codes,
        }
    )


def project_code_intelligence_report(
    **kwargs: Any,
) -> CodeIntelligenceReport:
    return build_code_intelligence_contract(**kwargs)


def _safe_digest(value: str) -> str:
    _reject_private_text(value, "digest")
    if _DIGEST_RE.fullmatch(value) is None:
        raise ValueError("digest must be sha256")
    return value


def _safe_ref(value: str) -> str:
    cleaned = value.strip()
    _reject_private_text(cleaned, "ref")
    if _SAFE_REF_RE.fullmatch(cleaned) is None:
        raise ValueError("value must be a digest or metadata-only code-intelligence ref")
    return cleaned


def _safe_reason_codes(value: Sequence[str]) -> tuple[str, ...]:
    codes = tuple(value)
    for item in codes:
        _reject_private_text(item, "reason code")
        if _REASON_CODE_RE.fullmatch(item) is None:
            raise ValueError("reason codes must be safe public identifiers")
    return codes


def _reject_private_text(value: str, label: str) -> None:
    if _PRIVATE_TEXT_RE.search(value):
        raise ValueError(f"{label} must not contain private paths, raw output, or tool data")


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def _diagnostics_artifact_ref(digest: str) -> str:
    return f"artifact:code-intelligence-diagnostics:{_safe_digest(digest)}"


__all__ = [
    "ADK_PRIMITIVE_NAMES",
    "CodeActionProjection",
    "CodeIntelligenceAuthorityFlags",
    "CodeIntelligenceObservation",
    "CodeIntelligenceOperation",
    "CodeIntelligenceReport",
    "CodeIntelligenceReportStatus",
    "CodeIntelligenceSourceMetadata",
    "CodeIntelligenceSpan",
    "CodeIntelligenceStatus",
    "REQUIRED_CODE_INTELLIGENCE_OPERATIONS",
    "build_code_intelligence_contract",
    "project_code_intelligence_report",
]
