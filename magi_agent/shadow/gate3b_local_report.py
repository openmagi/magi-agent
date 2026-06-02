from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from magi_agent.shadow.gate3b_local_consumer import Gate3BLocalConsumedBundle


Gate3BLocalComparisonStatus: TypeAlias = Literal[
    "not_run",
    "schema_pass",
    "schema_mismatch",
    "redaction_violation",
    "invalid_handoff",
    "not_applicable",
]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_SECRET_TEXT_RE = re.compile(
    r"(?:"
    r"Authorization:\s*Bearer\s+\S+|"
    r"Bearer\s+\S+|"
    r"sk-[A-Za-z0-9_-]{8,}|"
    r"\b(?:gh[opusr]_[A-Za-z0-9_]{8,}|github_pat_[A-Za-z0-9_]+)|"
    r"\b(?:[A-Z][A-Z0-9_]*(?:_TOKEN|_SECRET|_PASSWORD|_API_KEY)|"
    r"STRIPE_SECRET_KEY|SUPABASE_SERVICE_ROLE_KEY)\s*=\s*"
    r"(?:'[^'\r\n]*'|\"[^\"\r\n]*\"|[^\s'\"`;,]+)|"
    r"\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*\S+"
    r")",
    re.IGNORECASE,
)
_PRODUCTION_TEXT_RE = re.compile(
    r"(?:"
    r"\b[a-z][a-z0-9+.-]*://\S+|"
    r"\bmagi\.pro\b\S*|"
    r"/(?:data|workspace|mnt|var|private|tmp)\S*|"
    r"\bbot-[A-Za-z0-9_-]+|"
    r"\b[a-z0-9._-]*\.kube[a-z0-9._/-]*|"
    r"\b[a-z0-9._/-]*(?:k3s|secret|secrets|mission-store|scheduler-store)[a-z0-9._/-]*|"
    r"\b(?:pvc|runtime-selector|runtime_selector|deploy\.sh)\b"
    r")",
    re.IGNORECASE,
)
_GENERAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?:^|[\s('\"`=:;,])(?:/(?!/)\S+|[a-zA-Z]:[\\/]\S*)"
)
_EXECUTION_TEXT_RE = re.compile(
    r"(?:"
    r"\b(?:shell|code runner|package manager|script|command)\b.{0,32}\bexecut(?:e|ed|ion)\b|"
    r"\bexecut(?:e|ed|ion)\b.{0,32}\b(?:shell|code runner|package manager|script|command)\b|"
    r"\b(?:shell|script|command)\b.{0,32}\bran\b|"
    r"\blive tool\b.{0,32}\b(?:dispatch|execut|side effect)|"
    r"\bexternal side effects?\b"
    r")",
    re.IGNORECASE,
)
_ALLOWED_EXECUTION_DECLARED_SURFACES = frozenset(
    {
        "recorded",
        "recorded_only",
        "recorded_metadata",
        "recorded_metadata_only",
        "declared_metadata",
        "declared_metadata_only",
    }
)
_PRIVATE_KEYS = frozenset(
    {
        "hidden_reasoning",
        "chain_of_thought",
        "private_reasoning",
        "reasoning_trace",
        "private_tool_preview",
        "private_tool_input",
        "private_tool_output",
        "raw_tool_preview",
        "raw_connector_credentials",
        "child_private_records",
        "private_preview",
    }
)
_CREDENTIAL_KEY_PARTS = frozenset(
    {
        "authorization",
        "auth",
        "bearer",
        "cookie",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
        "apikey",
        "api_key",
        "access_key",
        "access_token",
        "refresh_token",
        "github",
        "telegram_token",
        "raw_auth_headers",
        "connector_credentials",
    }
)
_EXECUTION_CLAIM_KEYS = frozenset(
    {
        "shell_executed",
        "shell_invoked",
        "shell_ran",
        "code_executed",
        "code_invoked",
        "live_tool_executed",
        "live_tool_dispatched",
        "package_manager_executed",
        "package_manager_invoked",
        "package_manager_ran",
        "script_executed",
        "script_dispatched",
        "script_invoked",
        "script_ran",
        "script_run",
        "command_executed",
        "command_dispatched",
        "command_invoked",
        "command_ran",
        "command_run",
        "generated_script_executed",
        "auto_executed",
        "tool_dispatched",
        "tool_invoked",
        "external_side_effects",
        "tool_side_effect",
        "tool_side_effects",
        "dispatched_live",
        "dispatched",
        "invoked",
    }
)
_FORBIDDEN_COMPACT_KEY_TOKENS = frozenset(
    {
        "productiontranscriptwrite",
        "productiontranscriptappend",
        "productionssewrite",
        "productionsseappend",
        "productionroute",
        "productionrouteattached",
        "productionstorage",
        "productionqueue",
        "uservisibleoutput",
        "useroutputattached",
        "telegram",
        "telegramattached",
        "livetool",
        "livetoolattached",
        "livetoolsideeffect",
        "liverunner",
        "liverunnerattached",
        "productionstorageattached",
        "productionqueueattached",
        "evidenceblock",
        "evidenceblockmode",
        "signedack",
        "signedexternalack",
        "rawacknowledgement",
        "customextractor",
        "childexecution",
        "livecaptureincluded",
        "livetrafficconsumed",
        "livecaptureconsumed",
        "rawconnectorcredentialsincluded",
        "canarytrafficenabled",
        "pvcmounted",
        "workspaceattached",
        "workspacemounted",
        "workspacemutation",
        "workspaceadoption",
        "adkrunnerinvoked",
        "adkrunnerattached",
        "backgroundresume",
        "schedulerrun",
        "schedulerresume",
    }
)
_FORBIDDEN_COMPACT_VALUE_TOKENS = frozenset(
    {
        "hiddenreasoning",
        "chainofthought",
        "privatereasoning",
        "reasoningtrace",
        "privatetoolinput",
        "privatetoolpreview",
        "rawtoolpreview",
        "rawconnectorcredentials",
        "signedack",
        "signedexternalack",
        "signedacknowledgementpayload",
        "signedexternalacknowledgementpayload",
        "productiontranscriptwrite",
        "productionsseappend",
        "productionroute",
        "productionrouteattached",
        "productionqueue",
        "productionstorage",
        "telegram",
        "telegramattached",
        "livetool",
        "livetoolattached",
        "liverunner",
        "liverunnerattached",
        "evidenceblock",
        "productionqueueattached",
        "evidenceblockmodeenabled",
        "customextractor",
        "customextractoroutput",
        "childexecution",
        "workspacemutation",
        "workspaceadoption",
        "workspaceadoptionrequested",
        "schedulerrun",
        "backgroundresume",
        "schedulerresume",
        "schedulerresumerequested",
        "livecaptureincluded",
        "livetrafficconsumed",
        "livecaptureconsumed",
        "rawconnectorcredentialsincluded",
        "canarytraffic",
        "canarytrafficenabled",
        "pvcmounted",
        "workspaceattached",
        "workspacemounted",
        "adkrunnerinvoked",
        "adkrunnerattached",
        "shellexecution",
        "shellrun",
        "commandexecution",
        "commandrun",
        "livetoolran",
    }
)
_FALSE_ATTACHMENT_FIELD_ALIASES = frozenset(
    {
        "adkRunnerInvoked",
        "liveShadowExecuted",
        "toolsExecuted",
        "shellOrCodeExecuted",
        "storageWritten",
        "queueEnqueued",
        "userVisibleOutputAttached",
        "evidenceBlockEnabled",
    }
)
_FALSE_ONLY_METADATA_KEYS = frozenset(
    {
        "adk_runner_invoked",
        "live_shadow_executed",
        "tools_executed",
        "shell_or_code_executed",
        "storage_written",
        "queue_enqueued",
        "user_visible_output_attached",
        "public_output_attached",
        "evidence_block_enabled",
        "production_route_attached",
        "production_transcript_attached",
        "production_sse_attached",
        "production_storage_attached",
        "production_queue_attached",
        "user_output_attached",
        "telegram_attached",
        "live_tool_attached",
        "live_runner_attached",
        "production_path_included",
        "live_capture_included",
        "live_traffic_consumed",
        "dispatched_live",
        "shell_executed",
        "code_executed",
        "live_tool_executed",
        "package_manager_executed",
        "script_executed",
        "command_executed",
        "generated_script_executed",
        "external_side_effects",
        "tool_side_effect",
        "tool_side_effects",
        "external_ack_included",
    }
)


class _Gate3BLocalReportModel(BaseModel):
    model_config = _MODEL_CONFIG

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**values)

    def model_copy(
        self,
        *,
        update: Mapping[str, object] | None = None,
        deep: bool = False,
    ) -> Self:
        data = self.model_dump(by_alias=False, mode="python", warnings=False)
        if update:
            alias_to_name = {
                field.alias: name
                for name, field in self.__class__.model_fields.items()
                if field.alias is not None
            }
            data.update({alias_to_name.get(key, key): value for key, value in update.items()})
        return self.__class__.model_validate(data)


class Gate3BLocalReportAttachmentFlags(_Gate3BLocalReportModel):
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_shadow_executed: Literal[False] = Field(default=False, alias="liveShadowExecuted")
    tools_executed: Literal[False] = Field(default=False, alias="toolsExecuted")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    storage_written: Literal[False] = Field(default=False, alias="storageWritten")
    queue_enqueued: Literal[False] = Field(default=False, alias="queueEnqueued")
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    evidence_block_enabled: Literal[False] = Field(
        default=False,
        alias="evidenceBlockEnabled",
    )

    @classmethod
    def model_construct(
        cls,
        _fields_set: set[str] | None = None,
        **values: Any,
    ) -> Self:
        return cls(**{key: False for key in cls.model_fields})

    @model_validator(mode="before")
    @classmethod
    def _force_false_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        return {field.alias or name: False for name, field in cls.model_fields.items()}

    @field_serializer(
        "adk_runner_invoked",
        "live_shadow_executed",
        "tools_executed",
        "shell_or_code_executed",
        "storage_written",
        "queue_enqueued",
        "user_visible_output_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class Gate3BLocalParitySummary(_Gate3BLocalReportModel):
    handoff_validation: Gate3BLocalComparisonStatus = Field(alias="handoffValidation")
    transcript_projection: Gate3BLocalComparisonStatus = Field(alias="transcriptProjection")
    event_projection: Gate3BLocalComparisonStatus = Field(alias="eventProjection")
    control_projection: Gate3BLocalComparisonStatus = Field(alias="controlProjection")
    tool_projection: Gate3BLocalComparisonStatus = Field(alias="toolProjection")
    evidence_audit: Gate3BLocalComparisonStatus = Field(alias="evidenceAudit")
    runner_execution: Literal["not_run"] = Field(default="not_run", alias="runnerExecution")

    @field_serializer("runner_execution")
    def _serialize_runner_not_run(self, _value: object) -> Literal["not_run"]:
        return "not_run"


class Gate3BLocalRedactionSummary(_Gate3BLocalReportModel):
    input_verified: bool = Field(alias="inputVerified")
    output_verified: bool = Field(alias="outputVerified")
    violations: tuple[str, ...] = ()

    @field_validator("violations")
    @classmethod
    def _sanitize_violations(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_sanitize_public_text(item) for item in value)


class Gate3BLocalEvidenceAuditSummary(_Gate3BLocalReportModel):
    audit_record_count: int = Field(default=0, ge=0, alias="auditRecordCount")
    external_ack_included: Literal[False] = Field(
        default=False,
        alias="externalAckIncluded",
    )

    @field_serializer("external_ack_included")
    def _serialize_no_external_ack(self, _value: object) -> bool:
        return False


class Gate3BLocalReportCounts(_Gate3BLocalReportModel):
    transcript_entries: int = Field(default=0, ge=0, alias="transcriptEntries")
    agent_events: int = Field(default=0, ge=0, alias="agentEvents")
    control_events: int = Field(default=0, ge=0, alias="controlEvents")
    recorded_tool_results: int = Field(default=0, ge=0, alias="recordedToolResults")
    evidence_records: int = Field(default=0, ge=0, alias="evidenceRecords")


class Gate3BLocalPublicSummary(_Gate3BLocalReportModel):
    status: Gate3BLocalComparisonStatus
    preview: str

    @field_validator("preview")
    @classmethod
    def _sanitize_preview(cls, value: str) -> str:
        return _sanitize_public_text(value)


class Gate3BLocalComparisonReport(_Gate3BLocalReportModel):
    schema_version: Literal["gate3b.localComparisonReport.v1"] = Field(
        default="gate3b.localComparisonReport.v1",
        alias="schemaVersion",
    )
    bundle_id: str = Field(alias="bundleId")
    source_bundle_id: str = Field(alias="sourceBundleId")
    source_path: str = Field(alias="sourcePath")
    source_runtime: Literal["typescript-core-agent"] = Field(
        default="typescript-core-agent",
        alias="sourceRuntime",
    )
    shadow_runtime: Literal["python-adk"] = Field(default="python-adk", alias="shadowRuntime")
    handoff_mode: Literal["gate3b_local_file_to_gate3a_recorded_handoff"] = Field(
        alias="handoffMode",
    )
    report_mode: Literal["local_diagnostic_metadata_only"] = Field(
        default="local_diagnostic_metadata_only",
        alias="reportMode",
    )
    recipe_snapshot_id: str = Field(alias="recipeSnapshotId")
    pack_ids: tuple[str, ...] = Field(default=(), alias="packIds")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_shadow_executed: Literal[False] = Field(default=False, alias="liveShadowExecuted")
    tools_executed: Literal[False] = Field(default=False, alias="toolsExecuted")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    storage_written: Literal[False] = Field(default=False, alias="storageWritten")
    queue_enqueued: Literal[False] = Field(default=False, alias="queueEnqueued")
    user_visible_output_attached: Literal[False] = Field(
        default=False,
        alias="userVisibleOutputAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")
    attachment_flags: Gate3BLocalReportAttachmentFlags = Field(
        default_factory=Gate3BLocalReportAttachmentFlags,
        alias="attachmentFlags",
    )
    parity: Gate3BLocalParitySummary
    redaction: Gate3BLocalRedactionSummary
    evidence_audit: Gate3BLocalEvidenceAuditSummary = Field(alias="evidenceAudit")
    counts: Gate3BLocalReportCounts
    failures: tuple[str, ...] = ()
    public_summary: Gate3BLocalPublicSummary = Field(alias="publicSummary")

    @model_validator(mode="before")
    @classmethod
    def _force_false_attachment_inputs(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            return value
        data = dict(value)
        alias_to_name = {
            field.alias: name
            for name, field in cls.model_fields.items()
            if field.alias is not None
        }
        for alias in _FALSE_ATTACHMENT_FIELD_ALIASES:
            data.pop(alias_to_name.get(alias, alias), None)
            data[alias] = False
        data["attachmentFlags"] = Gate3BLocalReportAttachmentFlags()
        data.pop("attachment_flags", None)
        return data

    @field_validator("bundle_id", "source_bundle_id", "source_path", "recipe_snapshot_id")
    @classmethod
    def _sanitize_identity(cls, value: str) -> str:
        return _sanitize_public_text(value)

    @field_validator("pack_ids", "failures")
    @classmethod
    def _sanitize_string_tuple(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_sanitize_public_text(item) for item in value)

    @field_serializer(
        "adk_runner_invoked",
        "live_shadow_executed",
        "tools_executed",
        "shell_or_code_executed",
        "storage_written",
        "queue_enqueued",
        "user_visible_output_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False

    @field_serializer("attachment_flags")
    def _serialize_false_attachment_flags(
        self,
        _value: Gate3BLocalReportAttachmentFlags,
    ) -> dict[str, bool]:
        return Gate3BLocalReportAttachmentFlags().model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        )


def build_gate3b_local_comparison_reports(
    consumed_bundles: Iterable[Gate3BLocalConsumedBundle | Mapping[str, object]],
) -> tuple[Gate3BLocalComparisonReport, ...]:
    consumed = tuple(
        item
        if isinstance(item, Gate3BLocalConsumedBundle)
        else Gate3BLocalConsumedBundle.model_validate(item)
        for item in consumed_bundles
    )
    ordered = sorted(consumed, key=lambda item: (item.consumed_at, item.bundle_id, item.source_path))
    return tuple(_build_report_for_consumed_bundle(item) for item in ordered)


def _build_report_for_consumed_bundle(
    consumed: Gate3BLocalConsumedBundle,
) -> Gate3BLocalComparisonReport:
    if _contains_redaction_violation(consumed.handoff_metadata):
        return _report_for_invalid_consumed(
            consumed,
            status="redaction_violation",
            failure="Gate 3B local handoff metadata contained [REDACTED]",
            preview="Gate 3B local handoff metadata contained [REDACTED].",
        )
    try:
        _reject_unsafe_handoff_value(consumed.handoff_metadata)
    except Exception:
        return _report_for_invalid_consumed(
            consumed,
            status="invalid_handoff",
            failure="Gate 3B local handoff metadata failed local diagnostic validation",
            preview="Gate 3B local handoff metadata failed local diagnostic validation.",
        )
    try:
        bundle = _validate_gate3a_recorded_handoff(consumed.recorded_bundle_payload)
    except Exception:
        return _report_for_invalid_consumed(
            consumed,
            status="invalid_handoff",
            failure="Gate 3B local handoff failed Gate 3A recorded bundle validation",
            preview="Gate 3B local handoff failed Gate 3A recorded bundle validation.",
        )
    return _report_for_valid_consumed(consumed, bundle)


def _report_for_valid_consumed(
    consumed: Gate3BLocalConsumedBundle,
    bundle: Mapping[str, object],
) -> Gate3BLocalComparisonReport:
    recipe = _required_mapping(bundle.get("recipe"))
    return Gate3BLocalComparisonReport.model_validate(
        {
            "bundleId": consumed.bundle_id,
            "sourceBundleId": str(bundle["bundleId"]),
            "sourcePath": consumed.source_path,
            "handoffMode": consumed.handoff_mode,
            "recipeSnapshotId": str(recipe["recipeSnapshotId"]),
            "packIds": _string_tuple(recipe.get("packIds", ())),
            "parity": _parity("schema_pass"),
            "redaction": {
                "inputVerified": True,
                "outputVerified": True,
                "violations": (),
            },
            "evidenceAudit": {
                "auditRecordCount": len(_sequence(bundle.get("evidenceRecords"))),
                "externalAckIncluded": False,
            },
            "counts": {
                "transcriptEntries": len(_sequence(bundle.get("transcriptEntries"))),
                "agentEvents": len(_sequence(bundle.get("agentEvents"))),
                "controlEvents": len(_sequence(bundle.get("controlEvents"))),
                "recordedToolResults": len(_sequence(bundle.get("recordedToolResults"))),
                "evidenceRecords": len(_sequence(bundle.get("evidenceRecords"))),
            },
            "publicSummary": {
                "status": "schema_pass",
                "preview": "Gate 3B local handoff validated as local diagnostic metadata.",
            },
        }
    )


def _report_for_invalid_consumed(
    consumed: Gate3BLocalConsumedBundle,
    *,
    status: Literal["invalid_handoff", "redaction_violation"],
    failure: str,
    preview: str,
) -> Gate3BLocalComparisonReport:
    return Gate3BLocalComparisonReport.model_validate(
        {
            "bundleId": consumed.bundle_id,
            "sourceBundleId": consumed.bundle_id,
            "sourcePath": consumed.source_path,
            "handoffMode": consumed.handoff_mode,
            "recipeSnapshotId": "unknown_local_diagnostic_recipe",
            "packIds": (),
            "parity": _parity(status),
            "redaction": {
                "inputVerified": status != "redaction_violation",
                "outputVerified": True,
                "violations": ("[REDACTED]",) if status == "redaction_violation" else (),
            },
            "evidenceAudit": {
                "auditRecordCount": 0,
                "externalAckIncluded": False,
            },
            "counts": {
                "transcriptEntries": 0,
                "agentEvents": 0,
                "controlEvents": 0,
                "recordedToolResults": 0,
                "evidenceRecords": 0,
            },
            "failures": (_sanitize_public_text(failure),),
            "publicSummary": {
                "status": status,
                "preview": preview,
            },
        }
    )


def _parity(status: Gate3BLocalComparisonStatus) -> dict[str, str]:
    return {
        "handoffValidation": status,
        "transcriptProjection": "not_applicable" if status != "schema_pass" else "schema_pass",
        "eventProjection": "not_applicable" if status != "schema_pass" else "schema_pass",
        "controlProjection": "not_applicable",
        "toolProjection": "not_applicable" if status != "schema_pass" else "schema_pass",
        "evidenceAudit": "not_applicable" if status != "schema_pass" else "schema_pass",
        "runnerExecution": "not_run",
    }


def _contains_redaction_violation(value: object) -> bool:
    if isinstance(value, str):
        return _SECRET_TEXT_RE.search(value) is not None
    if isinstance(value, Mapping):
        return any(
            _contains_redaction_violation(str(key)) or _contains_redaction_violation(item)
            for key, item in value.items()
        )
    if isinstance(value, list | tuple):
        return any(_contains_redaction_violation(item) for item in value)
    return False


def _validate_gate3a_recorded_handoff(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, Mapping):
        raise ValueError("Gate 3A recorded handoff must be a JSON object")
    _reject_unsafe_handoff_value(payload)
    required_values = {
        "schemaVersion": "gate3a.recordedBundle.v1",
        "sourceRuntime": "typescript-core-agent",
        "recordingMode": "recorded_redacted",
        "redactionStatus": "verified",
    }
    for key, expected in required_values.items():
        if payload.get(key) != expected:
            raise ValueError("Gate 3B local report requires a Gate 3A recorded handoff")
    if not isinstance(payload.get("bundleId"), str) or not payload["bundleId"]:
        raise ValueError("Gate 3A recorded handoff bundleId is required")
    provenance = _required_mapping(payload.get("sourceProvenance"))
    if provenance.get("productionPathIncluded") is not False:
        raise ValueError("Gate 3A recorded handoff must not include production paths")
    if provenance.get("liveCaptureIncluded") is not False:
        raise ValueError("Gate 3A recorded handoff must remain recorded input")
    _required_mapping(payload.get("turn"))
    recipe = _required_mapping(payload.get("recipe"))
    if not isinstance(recipe.get("recipeSnapshotId"), str) or not recipe["recipeSnapshotId"]:
        raise ValueError("Gate 3A recorded handoff recipeSnapshotId is required")
    _string_tuple(recipe.get("packIds", ()))
    for key in (
        "transcriptEntries",
        "agentEvents",
        "recordedToolResults",
        "controlEvents",
        "evidenceRecords",
    ):
        _mapping_sequence(payload.get(key, ()))
    return payload


def _reject_unsafe_handoff_value(
    value: object,
    *,
    _path: tuple[str, ...] = (),
) -> None:
    if isinstance(value, str):
        _reject_unsafe_handoff_string(value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Gate 3A recorded handoff keys must be strings")
            normalized_key = _normalize_handoff_key(key)
            if _is_credential_key(normalized_key):
                raise ValueError("Gate 3A recorded handoff must not contain credential keys")
            if normalized_key in _PRIVATE_KEYS:
                raise ValueError("Gate 3A recorded handoff must not contain private fields")
            if normalized_key in _FALSE_ONLY_METADATA_KEYS and item is not False:
                raise ValueError("Gate 3A recorded handoff false-only metadata must be false")
            if _is_forbidden_execution_declared_surface(normalized_key, item):
                raise ValueError(
                    "Gate 3A recorded handoff execution surfaces must stay recorded-only"
                )
            if _is_malformed_execution_surface(normalized_key, item):
                raise ValueError("Gate 3A recorded handoff executionSurface must be an object")
            if _is_execution_claim_key(normalized_key) and item is not False:
                raise ValueError("Gate 3A recorded handoff execution flags must be false")
            if _is_forbidden_handoff_key(normalized_key) and item is not False:
                raise ValueError("Gate 3A recorded handoff contains live or production keys")
            if item is True and not _is_allowed_true_handoff_key(normalized_key, _path):
                raise ValueError("Gate 3A recorded handoff true flags must be explicit metadata")
            _reject_unsafe_handoff_value(item, _path=(*_path, normalized_key))
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_handoff_value(item, _path=_path)
        return
    if value is None or isinstance(value, bool | int | float):
        return
    raise ValueError("Gate 3A recorded handoff must be JSON-like")


def _required_mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Gate 3A recorded handoff mapping is required")
    return value


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError("Gate 3A recorded handoff array is required")
    return tuple(value)


def _mapping_sequence(value: object) -> tuple[Mapping[str, object], ...]:
    sequence = _sequence(value)
    if not all(isinstance(item, Mapping) for item in sequence):
        raise ValueError("Gate 3A recorded handoff array entries must be objects")
    return sequence  # type: ignore[return-value]


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list | tuple):
        raise ValueError("Gate 3A recorded handoff string array is required")
    strings: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError("Gate 3A recorded handoff string array entries must be strings")
        _reject_unsafe_handoff_string(item)
        strings.append(item)
    return tuple(strings)


def _sanitize_public_text(value: object, *, max_chars: int = 180) -> str:
    text = _SECRET_TEXT_RE.sub("[REDACTED]", str(value))
    text = _GENERAL_ABSOLUTE_PATH_RE.sub("[REDACTED]", text)
    text = _PRODUCTION_TEXT_RE.sub("[REDACTED]", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "[REDACTED]"
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def _reject_unsafe_handoff_string(value: str) -> None:
    normalized = value.strip()
    if not normalized:
        return
    if _SECRET_TEXT_RE.search(normalized):
        raise ValueError("Gate 3A recorded handoff contains credential-shaped strings")
    if _GENERAL_ABSOLUTE_PATH_RE.search(normalized):
        raise ValueError("Gate 3A recorded handoff contains absolute paths")
    if _PRODUCTION_TEXT_RE.search(normalized):
        raise ValueError("Gate 3A recorded handoff contains production paths or hosts")
    if _EXECUTION_TEXT_RE.search(normalized):
        raise ValueError("Gate 3A recorded handoff contains live execution claims")
    compact_value = _compact_handoff_text(normalized)
    if any(token in compact_value for token in _FORBIDDEN_COMPACT_VALUE_TOKENS):
        raise ValueError("Gate 3A recorded handoff contains forbidden runtime surfaces")


def _normalize_handoff_key(value: str) -> str:
    acronym_spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", value)
    camel_spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", acronym_spaced)
    return re.sub(r"[^a-z0-9]+", "_", camel_spaced.lower()).strip("_")


def _compact_handoff_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _normalize_handoff_key(value))


def _is_allowed_true_handoff_key(
    normalized_key: str,
    parent_path: tuple[str, ...],
) -> bool:
    parent = parent_path[-1] if parent_path else ""
    return (
        (parent == "recipe" and normalized_key == "hard_safety_enabled")
        or normalized_key == "recorded_only"
    )


def _is_forbidden_execution_declared_surface(
    normalized_key: str,
    value: object,
) -> bool:
    if normalized_key != "declared_surface":
        return False
    if not isinstance(value, str):
        return True
    return _normalize_handoff_key(value) not in _ALLOWED_EXECUTION_DECLARED_SURFACES


def _is_malformed_execution_surface(normalized_key: str, value: object) -> bool:
    return normalized_key == "execution_surface" and not isinstance(value, Mapping)


def _is_credential_key(normalized_key: str) -> bool:
    compact_key = normalized_key.replace("_", "")
    if compact_key in {"apikey", "telegramtoken", "rawauthheaders", "connectorcredentials"}:
        return True
    parts = frozenset(part for part in normalized_key.split("_") if part)
    credential_pairs = (
        {"api", "key"},
        {"access", "key"},
        {"client", "secret"},
        {"provider", "key"},
        {"service", "key"},
    )
    if any(pair <= parts for pair in credential_pairs):
        return True
    return bool(parts & _CREDENTIAL_KEY_PARTS)


def _is_execution_claim_key(normalized_key: str) -> bool:
    compact_key = normalized_key.replace("_", "")
    if normalized_key in _EXECUTION_CLAIM_KEYS:
        return True
    return bool(
        (
            "executed" in compact_key
            or "sideeffects" in compact_key
            or "invoked" in compact_key
            or "dispatched" in compact_key
        )
        and any(
            token in compact_key
            for token in ("shell", "code", "tool", "package", "script", "command", "external")
        )
    )


def _is_forbidden_handoff_key(normalized_key: str) -> bool:
    compact_key = normalized_key.replace("_", "")
    if compact_key in _FORBIDDEN_COMPACT_KEY_TOKENS:
        return True
    if any(token in compact_key for token in _FORBIDDEN_COMPACT_KEY_TOKENS):
        return True
    parts = frozenset(part for part in normalized_key.split("_") if part)
    if {"signed", "ack"} <= parts or {"external", "ack"} <= parts:
        return True
    if {"evidence", "block"} <= parts:
        return True
    if {"custom", "extractor"} <= parts:
        return True
    if (
        {"workspace", "mutation"} <= parts
        or {"workspace", "adoption"} <= parts
        or {"workspace", "attached"} <= parts
        or {"workspace", "mounted"} <= parts
    ):
        return True
    if {"live", "capture", "consumed"} <= parts:
        return True
    if {"raw", "connector", "credentials", "included"} <= parts:
        return True
    if {"pvc", "mounted"} <= parts:
        return True
    if {"adk", "runner"} <= parts and parts & {"invoked", "attached"}:
        return True
    if {"child", "execution"} <= parts:
        return True
    if "production" in parts and parts & {"attached", "write", "append", "route", "sse"}:
        return True
    if "authority" in parts and parts & {"python", "delay", "alter", "block", "influence"}:
        return True
    return False


__all__ = [
    "Gate3BLocalComparisonReport",
    "Gate3BLocalComparisonStatus",
    "Gate3BLocalEvidenceAuditSummary",
    "Gate3BLocalParitySummary",
    "Gate3BLocalPublicSummary",
    "Gate3BLocalRedactionSummary",
    "Gate3BLocalReportAttachmentFlags",
    "Gate3BLocalReportCounts",
    "build_gate3b_local_comparison_reports",
]
