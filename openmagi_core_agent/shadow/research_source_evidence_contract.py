from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.evidence.contracts import evaluate_evidence_contract
from openmagi_core_agent.evidence.reports import public_evidence_verdict_report
from openmagi_core_agent.evidence.types import (
    EvidenceContract,
    EvidenceContractVerdict,
    EvidenceRecord,
)
from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview


ResearchSourceCategory = Literal[
    "web_search_source_inspection_pass",
    "missing_source_inspection",
    "citation_uninspected_source",
    "knowledge_search_source_ledger_pass",
    "temporal_context_clock_pass",
    "audit_only_missing_source",
    "child_research_source_scoped_child",
    "browser_source_schema_pass",
    "external_repo_source_schema_pass",
    "external_doc_source_schema_pass",
]
ResearchAgentRole = Literal["research"]
ResearchRunOn = Literal["main", "child"]
ResearchSourceAuthority = Literal[
    "citation_gate_block_ready_policy",
    "source_ledger_required",
    "audit_only_no_block",
    "child_local_source_evidence_only",
]

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
_SOURCE_ID_RE = re.compile(r"^src_[1-9][0-9]*$")
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_researchsecret",
    "sk-research-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "hidden reasoning",
    "private raw page",
    "pythonResponseAuthority",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_attached",
        "agent_memory_imported",
        "agent_memory_provider_called",
        "browser_executed",
        "canary_attached",
        "canary_traffic_attached",
        "code_executed",
        "evidence_block_enabled",
        "file_mutated",
        "hipocampus_qmd_live_called",
        "live_tool",
        "live_tool_dispatched",
        "memory_provider",
        "memory_provider_called",
        "production_authority",
        "production_storage_written",
        "route_attached",
        "route_or_api_attached",
        "shell_executed",
        "shell_or_code_executed",
        "source_fetched",
        "telegram_attached",
        "tool_host_dispatched",
        "tool_dispatched_live",
        "traffic_attached",
        "web_search_executed",
        "workspace_written",
    }
)
_ALLOWED_RECORD_TYPES = frozenset(
    {"WebSearch", "KnowledgeSearch", "SourceInspection", "DateRange", "Clock"}
)
_ALLOWED_SOURCE_KINDS = frozenset({"tool_trace", "transcript", "adk_event"})
_ALLOWED_RESEARCH_SOURCE_KINDS = frozenset(
    {
        "web_search",
        "web_fetch",
        "browser",
        "kb",
        "file",
        "external_repo",
        "external_doc",
        "subagent_result",
        "clock",
    }
)
_REQUIRED_CATEGORIES = set(ResearchSourceCategory.__args__)  # type: ignore[attr-defined]


class ResearchSourceAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    web_search_executed: Literal[False] = Field(default=False, alias="webSearchExecuted")
    browser_executed: Literal[False] = Field(default=False, alias="browserExecuted")
    source_fetched: Literal[False] = Field(default=False, alias="sourceFetched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    memory_provider_called: Literal[False] = Field(
        default=False,
        alias="memoryProviderCalled",
    )
    agent_memory_imported: Literal[False] = Field(default=False, alias="agentMemoryImported")
    hipocampus_qmd_live_called: Literal[False] = Field(
        default=False,
        alias="hipocampusQmdLiveCalled",
    )
    production_storage_written: Literal[False] = Field(
        default=False,
        alias="productionStorageWritten",
    )
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    telegram_attached: Literal[False] = Field(default=False, alias="telegramAttached")
    canary_traffic_attached: Literal[False] = Field(
        default=False,
        alias="canaryTrafficAttached",
    )
    evidence_block_enabled: Literal[False] = Field(default=False, alias="evidenceBlockEnabled")

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
        "web_search_executed",
        "browser_executed",
        "source_fetched",
        "shell_or_code_executed",
        "memory_provider_called",
        "agent_memory_imported",
        "hipocampus_qmd_live_called",
        "production_storage_written",
        "production_authority",
        "route_or_api_attached",
        "telegram_attached",
        "canary_traffic_attached",
        "evidence_block_enabled",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class ResearchSourceEvidenceCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: ResearchSourceCategory
    agent_role: ResearchAgentRole = Field(alias="agentRole")
    run_on: ResearchRunOn = Field(alias="runOn")
    spawn_depth: int = Field(alias="spawnDepth", ge=0)
    turn_id: str = Field(alias="turnId")
    contract_start: int | float = Field(alias="contractStart")
    source_sensitive: bool = Field(alias="sourceSensitive")
    research_claim: str = Field(alias="researchClaim")
    citation_refs: tuple[str, ...] = Field(alias="citationRefs")
    authority: ResearchSourceAuthority
    public_preview: str = Field(alias="publicPreview")
    expected_ok: bool = Field(alias="expectedOk")
    expected_verdict_state: str = Field(alias="expectedVerdictState")
    expected_missing_types: tuple[str, ...] = Field(alias="expectedMissingTypes")
    expected_matched_types: tuple[str, ...] = Field(alias="expectedMatchedTypes")
    expected_failure_codes: tuple[str, ...] = Field(alias="expectedFailureCodes")
    contract: EvidenceContract
    records: tuple[EvidenceRecord, ...]
    attachment_flags: ResearchSourceAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.research_claim.strip():
            raise ValueError("research source case requires researchClaim")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child research source case requires spawnDepth > 0")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main research source case requires spawnDepth=0")
        if self.category == "child_research_source_scoped_child" and self.run_on != "child":
            raise ValueError("child research source fixture requires runOn=child")
        if self.category != "child_research_source_scoped_child" and self.run_on != "main":
            raise ValueError("non-child research source fixtures must use runOn=main")
        if self.category == "audit_only_missing_source":
            if self.authority != "audit_only_no_block" or self.contract.on_missing != "audit":
                raise ValueError("audit-only research source case must stay audit-only")
        elif self.authority == "audit_only_no_block":
            raise ValueError("audit_only_no_block authority is limited to audit-only cases")
        elif self.contract.on_missing != "block_final_answer":
            raise ValueError("blocking research source cases require block_final_answer policy")
        if self.authority == "child_local_source_evidence_only" and self.run_on != "child":
            raise ValueError("child-local authority requires child scope")
        if not self.citation_refs:
            raise ValueError("research source cases require citationRefs")
        for citation_ref in self.citation_refs:
            if _SOURCE_ID_RE.fullmatch(citation_ref) is None:
                raise ValueError("citationRefs must use inspected source id metadata names")
        _validate_contract_boundary(self)
        _validate_records(self)
        _validate_expected_verdict(self)
        if self.expected_ok and not set(self.citation_refs).issubset(
            _inspected_source_ids(self.records)
        ):
            raise ValueError("passing research source cases must cite inspected source ids")
        return self


class ResearchSourceEvidenceFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["researchSourceEvidenceFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: ResearchSourceAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[ResearchSourceEvidenceCase, ...]

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
            raise ValueError("research source caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("research source fixture is missing required categories")
        return self


class ResearchSourceEvidenceProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: ResearchSourceAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_verdict_state: dict[str, int] = Field(alias="byVerdictState")
    by_category: dict[str, int] = Field(alias="byCategory")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_research_source_evidence_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> ResearchSourceEvidenceFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return ResearchSourceEvidenceFixture.model_validate(payload)


def project_research_source_evidence_fixture(
    fixture: ResearchSourceEvidenceFixture | Mapping[str, Any],
) -> ResearchSourceEvidenceProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews: dict[str, str] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    verdicts: list[EvidenceContractVerdict] = []
    for case in safe_fixture.cases:
        verdict = evaluate_evidence_contract(case.contract, case.records)
        verdicts.append(verdict)
        preview = _public_preview(case)
        public_previews[case.case_id] = preview
        snapshot = _case_snapshot(case, verdict, preview=preview)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot
    return ResearchSourceEvidenceProjection(
        fixtureId=safe_fixture.fixture_id,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        caseOrder=tuple(case.case_id for case in safe_fixture.cases),
        byVerdictState=dict(Counter(verdict.state for verdict in verdicts)),
        byCategory=dict(Counter(case.category for case in safe_fixture.cases)),
        publicPreviews=public_previews,
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: ResearchSourceEvidenceFixture | Mapping[str, Any],
) -> ResearchSourceEvidenceFixture:
    if isinstance(fixture, ResearchSourceEvidenceFixture):
        return ResearchSourceEvidenceFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return ResearchSourceEvidenceFixture.model_validate(fixture)


def _case_snapshot(
    case: ResearchSourceEvidenceCase,
    verdict: EvidenceContractVerdict,
    *,
    preview: str,
) -> dict[str, object]:
    verdict_report = public_evidence_verdict_report(verdict)
    matched_types = tuple(record.type for record in verdict.matched_evidence)
    missing_types = tuple(requirement.type for requirement in verdict.missing_requirements)
    failure_codes = tuple(failure.code for failure in verdict.failures)
    snapshot: dict[str, object] = {
        "caseId": case.case_id,
        "category": case.category,
        "scope": {
            "agentRole": case.agent_role,
            "runOn": case.run_on,
            "spawnDepth": case.spawn_depth,
        },
        "turnId": case.turn_id,
        "contractStart": case.contract_start,
        "sourceSensitive": case.source_sensitive,
        "researchClaim": sanitize_tool_preview(case.research_claim),
        "citationRefs": case.citation_refs,
        "authority": case.authority,
        "publicPreview": preview,
        "contractId": verdict.contract_id,
        "ok": verdict.ok,
        "verdictState": verdict.state,
        "enforcement": verdict.enforcement,
        "recordedEvidenceTypes": _recorded_types_in_requirement_order(case),
        "matchedEvidenceTypes": matched_types,
        "missingRequirementTypes": missing_types,
        "failureCodes": failure_codes,
        "requirementCoverage": verdict.requirement_coverage,
        "sourceIds": tuple(_source_ids(case.records)),
        "sourceKinds": tuple(_source_kinds(case.records)),
        "trafficAttached": verdict_report.traffic_attached,
        "executionAttached": verdict_report.execution_attached,
    }
    return snapshot


def _recorded_types_in_requirement_order(
    case: ResearchSourceEvidenceCase,
) -> tuple[str, ...]:
    record_types = tuple(dict.fromkeys(record.type for record in case.records))
    ordered: list[str] = []
    for requirement in case.contract.requirements:
        if requirement.type in record_types and requirement.type not in ordered:
            ordered.append(requirement.type)
    for record_type in record_types:
        if record_type not in ordered:
            ordered.append(record_type)
    return tuple(ordered)


def _source_ids(records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for record in records:
        raw_ids = record.fields.get("sourceIds")
        if not isinstance(raw_ids, tuple | list):
            continue
        for raw_id in raw_ids:
            if isinstance(raw_id, str) and raw_id not in values:
                values.append(raw_id)
    return tuple(values)


def _inspected_source_ids(records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for record in records:
        if record.type != "SourceInspection" or record.fields.get("inspected") is not True:
            continue
        raw_ids = record.fields.get("sourceIds")
        if not isinstance(raw_ids, tuple | list):
            continue
        for raw_id in raw_ids:
            if isinstance(raw_id, str) and raw_id not in values:
                values.append(raw_id)
    return tuple(values)


def _source_kinds(records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
    values: list[str] = []
    for record in records:
        raw_kind = record.fields.get("sourceKind")
        if isinstance(raw_kind, str) and raw_kind not in values:
            values.append(raw_kind)
    return tuple(values)


def _public_preview(case: ResearchSourceEvidenceCase) -> str:
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(case.public_preview))
    return redacted


def _validate_contract_boundary(case: ResearchSourceEvidenceCase) -> None:
    if case.contract.traffic_attached or case.contract.execution_attached:
        raise ValueError("research source contracts must stay traffic-free")
    if case.contract.when is None:
        raise ValueError("research source contract must include boundary metadata")
    boundary = case.contract.when.get("contractStart")
    if not _strict_number_equal(boundary, case.contract_start):
        raise ValueError("contract contractStart must match case boundary")
    if case.contract.when.get("sourceSensitive") is not case.source_sensitive:
        raise ValueError("contract sourceSensitive must match case metadata")
    execution_boundary_present = "executionBoundary" in case.contract.when
    execution_boundary = case.contract.when.get("executionBoundary")
    if case.run_on == "child":
        if execution_boundary != "child":
            raise ValueError("child research source contract requires child boundary metadata")
    elif execution_boundary_present:
        raise ValueError("main research source contract cannot carry boundary metadata")
    requirement_types = tuple(requirement.type for requirement in case.contract.requirements)
    if "SourceInspection" not in requirement_types:
        raise ValueError("research source contract must require SourceInspection")
    if not {"WebSearch", "KnowledgeSearch", "Clock"}.intersection(requirement_types):
        raise ValueError("research source contract must include source-producing metadata")


def _validate_records(case: ResearchSourceEvidenceCase) -> None:
    for record in case.records:
        if record.type not in _ALLOWED_RECORD_TYPES:
            raise ValueError("research source fixtures only allow source evidence types")
        if record.source.kind not in _ALLOWED_SOURCE_KINDS:
            raise ValueError("research source records must be recorded event metadata")
        if case.run_on == "child":
            if record.source.kind != "transcript":
                raise ValueError("child research source evidence must come from transcript metadata")
            if record.source.metadata.get("executionBoundary") != "child":
                raise ValueError("child research source evidence requires child boundary metadata")
        elif "executionBoundary" in record.source.metadata:
            raise ValueError("main research evidence cannot carry execution boundary metadata")
        if not _strict_number_equal(record.metadata.get("contractStart"), case.contract_start):
            raise ValueError("research source records must carry matching contractStart metadata")
        _validate_record_shape(record)
        _reject_unsafe_public_snapshot(
            {
                "preview": record.preview,
                "fields": record.model_dump(by_alias=True, mode="json").get("fields", {}),
            }
        )


def _validate_record_shape(record: EvidenceRecord) -> None:
    if record.type in {"WebSearch", "KnowledgeSearch"}:
        _require_fields(record, "query", "resultCount", "sourceKind", "sourceIds")
    elif record.type == "SourceInspection":
        _require_fields(record, "sourceIds", "sourceKind", "inspected")
        if record.fields.get("inspected") is not True:
            raise ValueError("SourceInspection evidence must be inspected=true")
    elif record.type == "Clock":
        _require_fields(record, "sourceKind", "date")
        if record.fields.get("sourceKind") != "clock":
            raise ValueError("Clock evidence must use sourceKind=clock")
    elif record.type == "DateRange":
        _require_fields(record, "sourceKind")
    raw_source_kind = record.fields.get("sourceKind")
    if not isinstance(raw_source_kind, str) or raw_source_kind not in _ALLOWED_RESEARCH_SOURCE_KINDS:
        raise ValueError("research source evidence requires supported sourceKind metadata")
    raw_source_ids = record.fields.get("sourceIds")
    if raw_source_ids is not None:
        if not isinstance(raw_source_ids, tuple | list) or not raw_source_ids:
            raise ValueError("sourceIds must be a non-empty source id list")
        for source_id in raw_source_ids:
            if not isinstance(source_id, str) or _SOURCE_ID_RE.fullmatch(source_id) is None:
                raise ValueError("sourceIds must use src_N metadata names")


def _require_fields(record: EvidenceRecord, *field_names: str) -> None:
    missing = tuple(field_name for field_name in field_names if field_name not in record.fields)
    if missing:
        raise ValueError(f"{record.type} evidence is missing fields: {missing}")


def _validate_expected_verdict(case: ResearchSourceEvidenceCase) -> None:
    verdict = evaluate_evidence_contract(case.contract, case.records)
    matched_types = tuple(record.type for record in verdict.matched_evidence)
    missing_types = tuple(requirement.type for requirement in verdict.missing_requirements)
    failure_codes = tuple(failure.code for failure in verdict.failures)
    if verdict.ok != case.expected_ok:
        raise ValueError("research source expectedOk does not match evaluated verdict")
    if verdict.state != case.expected_verdict_state:
        raise ValueError("research source expectedVerdictState does not match verdict")
    if missing_types != case.expected_missing_types:
        raise ValueError("research source expectedMissingTypes does not match verdict")
    if matched_types != case.expected_matched_types:
        raise ValueError("research source expectedMatchedTypes does not match verdict")
    if failure_codes != case.expected_failure_codes:
        raise ValueError("research source expectedFailureCodes does not match verdict")


def _strict_number_equal(left: object, right: int | float) -> bool:
    if isinstance(left, bool) or not isinstance(left, int | float):
        return False
    if type(left) is not type(right):
        return False
    return left == right


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _FORBIDDEN_PATH_RE.search(rendered):
        raise ValueError("research source public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("research source public snapshot contains unsafe data")


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
        raise ValueError("research source fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("research source fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("research source fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("research source fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("research source fixture cannot claim live behavior")
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
        raise ValueError("research source fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("research source mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("research source fixture values must be JSON-compatible")


def _normalize_key(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", value)
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
    "ResearchSourceAttachmentFlags",
    "ResearchSourceEvidenceCase",
    "ResearchSourceEvidenceFixture",
    "ResearchSourceEvidenceProjection",
    "load_research_source_evidence_fixture",
    "project_research_source_evidence_fixture",
]
