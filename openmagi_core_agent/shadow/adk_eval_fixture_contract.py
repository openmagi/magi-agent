from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator


AdkEvalSourceFixtureKind: TypeAlias = Literal[
    "research_source_evidence",
    "coding_verification_evidence",
    "fact_grounding_verifier",
]
AdkEvalTaskType: TypeAlias = Literal["research", "coding", "fact_grounding"]
AdkEvalAgentRole: TypeAlias = Literal["research", "coding", "verifier"]
AdkEvalPrimitive: TypeAlias = Literal["AgentEvaluator"]
AdkEvalSuiteType: TypeAlias = Literal["metadata_only_eval_suite"]
AdkEvalCaseType: TypeAlias = Literal["metadata_only_eval_case"]
AdkEvalEvidenceSemantics: TypeAlias = Literal["product_contract_reference"]
AdkEvalVerdictSource: TypeAlias = Literal["existing_openmagi_fixture_projection"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)
_EXPECTED_SOURCE_FIXTURE_IDS: dict[AdkEvalSourceFixtureKind, str] = {
    "research_source_evidence": "research_source_evidence_matrix_0001",
    "coding_verification_evidence": "coding_verification_evidence_matrix_0001",
    "fact_grounding_verifier": "fact_grounding_verifier_matrix_0001",
}
_SOURCE_FIXTURE_PATHS: dict[AdkEvalSourceFixtureKind, str] = {
    "research_source_evidence": "research_source_evidence/policy_matrix.json",
    "coding_verification_evidence": "coding_verification_evidence/policy_matrix.json",
    "fact_grounding_verifier": "fact_grounding_verifier/policy_matrix.json",
}
_TASK_BY_SOURCE_KIND: dict[AdkEvalSourceFixtureKind, AdkEvalTaskType] = {
    "research_source_evidence": "research",
    "coding_verification_evidence": "coding",
    "fact_grounding_verifier": "fact_grounding",
}
_AGENT_ROLE_BY_SOURCE_KIND: dict[AdkEvalSourceFixtureKind, AdkEvalAgentRole] = {
    "research_source_evidence": "research",
    "coding_verification_evidence": "coding",
    "fact_grounding_verifier": "verifier",
}
_REQUIRED_SOURCE_KINDS = frozenset(_EXPECTED_SOURCE_FIXTURE_IDS)
_PRODUCTION_PATH_RE = re.compile(
    r"(?:^|[\\/])(?:data[\\/]bots|workspace|var[\\/]lib[\\/]kubelet)(?:[\\/]|$)|"
    r"pvc|supabase://|s3://|gs://|postgres(?:ql)?://|telegram|canary",
    re.IGNORECASE,
)
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_",
    "sk-",
    "SUPABASE_SERVICE_ROLE_KEY",
    "rawOutput",
    "hidden reasoning",
    "google.adk.evaluation",
    "Runner.run",
    "ToolHost.execute",
)
_FORBIDDEN_TRUE_KEYS = frozenset(
    {
        "adk_evaluation_imported",
        "adk_runner_attached",
        "adk_runner_invoked",
        "agent_evaluator_invoked",
        "agent_memory_imported",
        "chat_transport_attached",
        "evaluation_attached",
        "google_adk_evaluation_imported",
        "live_eval_attached",
        "live_tool_dispatched",
        "memory_provider_called",
        "model_called",
        "production_authority",
        "route_or_api_attached",
        "runner_attached",
        "runner_invoked",
        "tool_host_dispatched",
        "traffic_attached",
    }
)
_FORBIDDEN_INLINE_VERDICT_KEYS = frozenset(
    {
        "contract",
        "expected_failure_codes",
        "expected_matched_types",
        "expected_missing_types",
        "expected_ok",
        "expected_verdict_state",
        "failure_codes",
        "matched_evidence_types",
        "records",
        "verdict_state",
    }
)


class AdkEvalFixtureAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    evaluation_attached: Literal[False] = Field(default=False, alias="evaluationAttached")
    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    model_called: Literal[False] = Field(default=False, alias="modelCalled")
    toolhost_dispatched: Literal[False] = Field(default=False, alias="toolHostDispatched")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    route_or_api_attached: Literal[False] = Field(default=False, alias="routeOrApiAttached")
    memory_provider_called: Literal[False] = Field(default=False, alias="memoryProviderCalled")
    chat_transport_attached: Literal[False] = Field(default=False, alias="chatTransportAttached")

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
        "evaluation_attached",
        "adk_runner_invoked",
        "model_called",
        "toolhost_dispatched",
        "live_tool_dispatched",
        "traffic_attached",
        "production_authority",
        "route_or_api_attached",
        "memory_provider_called",
        "chat_transport_attached",
    )
    def _serialize_false(self, _value: object) -> bool:
        return False


class AdkEvalFixtureCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    source_case_id: str = Field(alias="sourceCaseId")
    source_fixture_kind: AdkEvalSourceFixtureKind = Field(alias="sourceFixtureKind")
    task_type: AdkEvalTaskType = Field(alias="taskType")
    agent_role: AdkEvalAgentRole = Field(alias="agentRole")
    future_adk_primitive: AdkEvalPrimitive = Field(alias="futureAdkPrimitive")
    future_adk_eval_case_type: AdkEvalCaseType = Field(alias="futureAdkEvalCaseType")
    openmagi_evidence_semantics: AdkEvalEvidenceSemantics = Field(
        alias="openMagiEvidenceSemantics",
    )
    verdict_source: AdkEvalVerdictSource = Field(alias="verdictSource")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    attachment_flags: AdkEvalFixtureAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.case_id.strip():
            raise ValueError("ADK eval metadata case requires caseId")
        if not self.source_case_id.strip():
            raise ValueError("ADK eval metadata case requires sourceCaseId")
        if self.task_type != _TASK_BY_SOURCE_KIND[self.source_fixture_kind]:
            raise ValueError("ADK eval metadata taskType must match sourceFixtureKind")
        if self.agent_role != _AGENT_ROLE_BY_SOURCE_KIND[self.source_fixture_kind]:
            raise ValueError("ADK eval metadata agentRole must match sourceFixtureKind")
        return self


class AdkEvalFixtureSuite(BaseModel):
    model_config = _MODEL_CONFIG

    suite_id: str = Field(alias="suiteId")
    source_fixture_kind: AdkEvalSourceFixtureKind = Field(alias="sourceFixtureKind")
    source_fixture_id: str = Field(alias="sourceFixtureId")
    future_adk_suite_name: str = Field(alias="futureAdkSuiteName")
    future_adk_primitive: AdkEvalPrimitive = Field(alias="futureAdkPrimitive")
    future_adk_eval_suite_type: AdkEvalSuiteType = Field(alias="futureAdkEvalSuiteType")
    openmagi_evidence_contract: AdkEvalSourceFixtureKind = Field(
        alias="openMagiEvidenceContract",
    )
    openmagi_evidence_semantics: AdkEvalEvidenceSemantics = Field(
        alias="openMagiEvidenceSemantics",
    )
    verdict_source: AdkEvalVerdictSource = Field(alias="verdictSource")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    attachment_flags: AdkEvalFixtureAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[AdkEvalFixtureCase, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_suite(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_suite(self) -> Self:
        if not self.suite_id.strip():
            raise ValueError("ADK eval metadata suite requires suiteId")
        expected_fixture_id = _EXPECTED_SOURCE_FIXTURE_IDS[self.source_fixture_kind]
        if self.source_fixture_id != expected_fixture_id:
            raise ValueError("ADK eval metadata suite sourceFixtureId must match source kind")
        if self.openmagi_evidence_contract != self.source_fixture_kind:
            raise ValueError("OpenMagi evidence contract must match source fixture kind")
        if not self.future_adk_suite_name.strip():
            raise ValueError("ADK eval metadata suite requires futureAdkSuiteName")
        if not self.cases:
            raise ValueError("ADK eval metadata suite requires cases")
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("ADK eval metadata caseIds must be unique within suite")
        source_case_ids = [case.source_case_id for case in self.cases]
        if len(source_case_ids) != len(set(source_case_ids)):
            raise ValueError("ADK eval metadata sourceCaseIds must be unique within suite")
        for case in self.cases:
            if case.source_fixture_kind != self.source_fixture_kind:
                raise ValueError("ADK eval metadata case sourceFixtureKind must match suite")
        return self


class AdkEvalFixtureContract(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["adkEvalFixtureContract.v1"] = Field(alias="schemaVersion")
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    target_runtime: Literal["python-adk-future"] = Field(alias="targetRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    attachment_flags: AdkEvalFixtureAttachmentFlags = Field(alias="attachmentFlags")
    suites: tuple[AdkEvalFixtureSuite, ...]

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_fixture(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_fixture(self) -> Self:
        suite_ids = [suite.suite_id for suite in self.suites]
        if len(suite_ids) != len(set(suite_ids)):
            raise ValueError("ADK eval metadata suiteIds must be unique")
        source_kinds = [suite.source_fixture_kind for suite in self.suites]
        if set(source_kinds) != _REQUIRED_SOURCE_KINDS:
            raise ValueError(
                "ADK eval metadata fixture must cover research, coding, "
                "and fact-grounding suites",
            )
        if len(source_kinds) != len(set(source_kinds)):
            raise ValueError("ADK eval metadata source fixture kinds must be unique")
        case_ids = [
            case.case_id
            for suite in self.suites
            for case in suite.cases
        ]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("ADK eval metadata caseIds must be globally unique")
        return self


class AdkEvalFixtureProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    metadata_only: Literal[True] = Field(alias="metadataOnly")
    default_off: Literal[True] = Field(alias="defaultOff")
    attachment_flags: AdkEvalFixtureAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    suite_order: tuple[str, ...] = Field(alias="suiteOrder")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    source_case_ids_by_suite: dict[str, tuple[str, ...]] = Field(
        alias="sourceCaseIdsBySuite",
    )
    by_source_fixture: dict[str, int] = Field(alias="bySourceFixture")
    by_task_type: dict[str, int] = Field(alias="byTaskType")
    suite_snapshots: dict[str, dict[str, object]] = Field(alias="suiteSnapshots")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")


def load_adk_eval_fixture_contract(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> AdkEvalFixtureContract:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return AdkEvalFixtureContract.model_validate(payload)


def project_adk_eval_fixture_contract(
    fixture: AdkEvalFixtureContract | Mapping[str, Any],
    *,
    reference_fixture_root: str | Path | None = None,
) -> AdkEvalFixtureProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    if reference_fixture_root is not None:
        _validate_source_fixture_references(
            safe_fixture,
            reference_fixture_root=reference_fixture_root,
        )

    suite_snapshots: dict[str, dict[str, object]] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    source_case_ids_by_suite: dict[str, tuple[str, ...]] = {}
    case_order: list[str] = []
    source_fixture_counts: Counter[str] = Counter()
    task_counts: Counter[str] = Counter()

    for suite in safe_fixture.suites:
        source_case_ids = tuple(case.source_case_id for case in suite.cases)
        source_case_ids_by_suite[suite.suite_id] = source_case_ids
        source_fixture_counts[suite.source_fixture_kind] += len(suite.cases)
        suite_snapshots[suite.suite_id] = _suite_snapshot(suite, source_case_ids)
        _reject_unsafe_public_snapshot(suite_snapshots[suite.suite_id])

        for case in suite.cases:
            case_order.append(case.case_id)
            task_counts[case.task_type] += 1
            snapshot = _case_snapshot(case, suite)
            _reject_unsafe_public_snapshot(snapshot)
            case_snapshots[case.case_id] = snapshot

    return AdkEvalFixtureProjection(
        fixtureId=safe_fixture.fixture_id,
        localDiagnostic=True,
        metadataOnly=True,
        defaultOff=True,
        attachmentFlags=safe_fixture.attachment_flags,
        noLiveExecution=True,
        suiteOrder=tuple(suite.suite_id for suite in safe_fixture.suites),
        caseOrder=tuple(case_order),
        sourceCaseIdsBySuite=source_case_ids_by_suite,
        bySourceFixture=dict(source_fixture_counts),
        byTaskType=dict(task_counts),
        suiteSnapshots=suite_snapshots,
        caseSnapshots=case_snapshots,
    )


def _validated_fixture_snapshot(
    fixture: AdkEvalFixtureContract | Mapping[str, Any],
) -> AdkEvalFixtureContract:
    if isinstance(fixture, AdkEvalFixtureContract):
        return AdkEvalFixtureContract.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return AdkEvalFixtureContract.model_validate(fixture)


def _suite_snapshot(
    suite: AdkEvalFixtureSuite,
    source_case_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "suiteId": suite.suite_id,
        "sourceFixtureKind": suite.source_fixture_kind,
        "sourceFixtureId": suite.source_fixture_id,
        "futureAdkSuiteName": suite.future_adk_suite_name,
        "futureAdkPrimitive": suite.future_adk_primitive,
        "futureAdkEvalSuiteType": suite.future_adk_eval_suite_type,
        "openMagiEvidenceContract": suite.openmagi_evidence_contract,
        "openMagiEvidenceSemantics": suite.openmagi_evidence_semantics,
        "verdictSource": suite.verdict_source,
        "sourceCaseIds": source_case_ids,
        "caseCount": len(suite.cases),
        "localDiagnostic": True,
        "metadataOnly": True,
        "defaultOff": True,
        "evaluationAttached": False,
        "adkRunnerInvoked": False,
        "modelCalled": False,
        "toolHostDispatched": False,
        "liveToolDispatched": False,
        "trafficAttached": False,
        "productionAuthority": False,
    }


def _case_snapshot(
    case: AdkEvalFixtureCase,
    suite: AdkEvalFixtureSuite,
) -> dict[str, object]:
    return {
        "caseId": case.case_id,
        "sourceFixtureKind": case.source_fixture_kind,
        "sourceFixtureId": suite.source_fixture_id,
        "sourceCaseId": case.source_case_id,
        "taskType": case.task_type,
        "agentRole": case.agent_role,
        "futureAdkPrimitive": case.future_adk_primitive,
        "futureAdkEvalCaseType": case.future_adk_eval_case_type,
        "openMagiEvidenceSemantics": case.openmagi_evidence_semantics,
        "verdictSource": case.verdict_source,
        "localDiagnostic": True,
        "metadataOnly": True,
        "defaultOff": True,
        "evaluationAttached": False,
        "adkRunnerInvoked": False,
        "modelCalled": False,
        "toolHostDispatched": False,
        "liveToolDispatched": False,
        "trafficAttached": False,
        "productionAuthority": False,
    }


def _validate_source_fixture_references(
    fixture: AdkEvalFixtureContract,
    *,
    reference_fixture_root: str | Path,
) -> None:
    reference_case_ids = _load_reference_case_ids(reference_fixture_root)
    for suite in fixture.suites:
        expected_case_ids = reference_case_ids[suite.source_fixture_kind]
        source_case_ids = tuple(case.source_case_id for case in suite.cases)
        if source_case_ids != expected_case_ids:
            raise ValueError(
                "ADK eval metadata sourceCaseIds must match existing fixture caseIds",
            )


def _load_reference_case_ids(
    reference_fixture_root: str | Path,
) -> dict[AdkEvalSourceFixtureKind, tuple[str, ...]]:
    root = Path(reference_fixture_root).resolve(strict=True)
    loaded: dict[AdkEvalSourceFixtureKind, tuple[str, ...]] = {}
    for source_kind, relative_path in _SOURCE_FIXTURE_PATHS.items():
        path = (root / relative_path).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("source fixture path must stay under reference fixture root")
        with path.open("r", encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)
        if not isinstance(payload, Mapping):
            raise ValueError("source fixture must be a JSON object")
        fixture_id = payload.get("fixtureId")
        expected_fixture_id = _EXPECTED_SOURCE_FIXTURE_IDS[source_kind]
        if fixture_id != expected_fixture_id:
            raise ValueError("source fixture id mismatch")
        cases = payload.get("cases")
        if not isinstance(cases, list) or not cases:
            raise ValueError("source fixture must contain cases")
        case_ids: list[str] = []
        for case in cases:
            if not isinstance(case, Mapping):
                raise ValueError("source fixture case must be an object")
            case_id = case.get("caseId")
            if not isinstance(case_id, str) or not case_id.strip():
                raise ValueError("source fixture case requires caseId")
            case_ids.append(case_id)
        loaded[source_kind] = tuple(case_ids)
    return loaded


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
        raise ValueError("ADK eval metadata fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _PRODUCTION_PATH_RE.search(path_text):
        raise ValueError("ADK eval metadata fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _PRODUCTION_PATH_RE.search(value):
            raise ValueError("ADK eval metadata fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("ADK eval metadata fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized_key = _normalize_key(key)
            if normalized_key in _FORBIDDEN_INLINE_VERDICT_KEYS:
                raise ValueError("ADK eval metadata must not duplicate verdict logic")
            if nested_value is True and normalized_key in _FORBIDDEN_TRUE_KEYS:
                raise ValueError("ADK eval metadata fixture cannot claim live behavior")
            _reject_unsafe_raw_value(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_unsafe_raw_value(item)


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _PRODUCTION_PATH_RE.search(rendered):
        raise ValueError("ADK eval metadata public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("ADK eval metadata public snapshot contains unsafe data")


def _validate_json_like(value: object) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int) and not isinstance(value, bool):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("ADK eval metadata fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("ADK eval metadata mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("ADK eval metadata fixture values must be JSON-compatible")


def _normalize_key(key: object) -> str:
    if not isinstance(key, str):
        raise ValueError("ADK eval metadata mappings must use string keys")
    spaced = re.sub(r"(?<!^)(?=[A-Z])", "_", key)
    return re.sub(r"[^a-z0-9]+", "_", spaced.lower()).strip("_")


__all__ = [
    "AdkEvalFixtureAttachmentFlags",
    "AdkEvalFixtureCase",
    "AdkEvalFixtureContract",
    "AdkEvalFixtureProjection",
    "AdkEvalFixtureSuite",
    "load_adk_eval_fixture_contract",
    "project_adk_eval_fixture_contract",
]
