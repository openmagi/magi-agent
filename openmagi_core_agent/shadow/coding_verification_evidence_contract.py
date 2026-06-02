from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from openmagi_core_agent.evidence.contracts import (
    evaluate_evidence_contract,
    evidence_command_digest,
)
from openmagi_core_agent.evidence.reports import public_evidence_verdict_report
from openmagi_core_agent.evidence.types import (
    EvidenceContract,
    EvidenceContractFailure,
    EvidenceContractVerdict,
    EvidenceRecord,
)
from openmagi_core_agent.transport.tool_preview import sanitize_tool_preview


CodingVerificationCategory = Literal[
    "post_edit_verification_pass",
    "diagnostics_verification_pass",
    "missing_gitdiff",
    "failed_testrun",
    "stale_verification",
    "audit_only_unverified_claim",
    "child_scoped_verification",
    "coding_child_review_freshness",
    "commit_checkpoint_verification",
    "planner_recommended_verification",
]
CodingAgentRole = Literal["coding"]
CodingRunOn = Literal["main", "child"]
CodingEvidenceAuthority = Literal[
    "block_ready_policy",
    "audit_only_no_block",
    "child_local_evidence_only",
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
_FORBIDDEN_PUBLIC_TOKENS = (
    "Bearer unsafe",
    "ghp_codingsecret",
    "sk-coding-secret",
    "SUPABASE_SERVICE_ROLE_KEY",
    "raw test output",
    "pythonResponseAuthority",
)
_FORBIDDEN_RAW_KEY_TOKENS = frozenset(
    {
        "adk_runner_invoked",
        "adk_runner_attached",
        "agent_memory_imported",
        "agent_memory_provider_called",
        "canary_attached",
        "canary_traffic_attached",
        "child_execution_attached",
        "child_runner",
        "code_executed",
        "evidence_block_enabled",
        "file_mutated",
        "git_executed",
        "hipocampus_qmd_live_called",
        "live_tool",
        "live_tool_dispatched",
        "memory_provider",
        "memory_provider_called",
        "patch_applied",
        "production_authority",
        "production_storage_written",
        "route_attached",
        "route_or_api_attached",
        "shell_executed",
        "shell_or_code_executed",
        "telegram_attached",
        "test_executed",
        "tool_host_dispatched",
        "tool_dispatched_live",
        "traffic_attached",
        "workspace_written",
    }
)
_REQUIRED_CATEGORIES = set(CodingVerificationCategory.__args__)  # type: ignore[attr-defined]
_CODE_DIAGNOSTICS_CHECKERS = frozenset({"typescript", "python", "go"})
_CODE_DIAGNOSTICS_REQUIRED_FIELDS = frozenset(
    {"checker", "passed", "exitCode", "diagnosticCount"}
)
_CODE_DIAGNOSTICS_PUBLIC_FIELDS = frozenset(
    {"checker", "passed", "exitCode", "diagnosticCount"}
)
_CODE_DIAGNOSTICS_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "diagnostic_output",
        "logs",
        "output",
        "raw_diagnostic_output",
        "raw_diagnostics",
        "raw_output",
        "stderr",
        "stdout",
    }
)
_PLANNER_COMMAND_KINDS = frozenset({"test", "lint", "typecheck", "build", "compile"})
_PLANNER_COMMAND_CONFIDENCES = frozenset({"high", "medium", "low"})
_PLANNER_COMMAND_REQUIRED_FIELDS = frozenset(
    {"kind", "command", "cwd", "runner", "confidence", "reason"}
)


class CodingVerificationAttachmentFlags(BaseModel):
    model_config = _MODEL_CONFIG

    adk_runner_invoked: Literal[False] = Field(default=False, alias="adkRunnerInvoked")
    live_tool_dispatched: Literal[False] = Field(default=False, alias="liveToolDispatched")
    shell_or_code_executed: Literal[False] = Field(
        default=False,
        alias="shellOrCodeExecuted",
    )
    git_executed: Literal[False] = Field(default=False, alias="gitExecuted")
    test_executed: Literal[False] = Field(default=False, alias="testExecuted")
    file_mutated: Literal[False] = Field(default=False, alias="fileMutated")
    patch_applied: Literal[False] = Field(default=False, alias="patchApplied")
    workspace_written: Literal[False] = Field(default=False, alias="workspaceWritten")
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
        "shell_or_code_executed",
        "git_executed",
        "test_executed",
        "file_mutated",
        "patch_applied",
        "workspace_written",
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


class CodingVerificationEvidenceCase(BaseModel):
    model_config = _MODEL_CONFIG

    case_id: str = Field(alias="caseId")
    category: CodingVerificationCategory
    agent_role: CodingAgentRole = Field(alias="agentRole")
    run_on: CodingRunOn = Field(alias="runOn")
    spawn_depth: int = Field(alias="spawnDepth", ge=0)
    turn_id: str = Field(alias="turnId")
    last_code_mutation_at: int | float = Field(alias="lastCodeMutationAt")
    completion_claim: str = Field(alias="completionClaim")
    authority: CodingEvidenceAuthority
    public_preview: str = Field(alias="publicPreview")
    expected_ok: bool = Field(alias="expectedOk")
    expected_verdict_state: str = Field(alias="expectedVerdictState")
    expected_missing_types: tuple[str, ...] = Field(alias="expectedMissingTypes")
    expected_matched_types: tuple[str, ...] = Field(alias="expectedMatchedTypes")
    expected_failure_codes: tuple[str, ...] = Field(alias="expectedFailureCodes")
    contract: EvidenceContract
    records: tuple[EvidenceRecord, ...]
    attachment_flags: CodingVerificationAttachmentFlags = Field(alias="attachmentFlags")

    @model_validator(mode="before")
    @classmethod
    def _validate_raw_case(cls, value: object) -> object:
        if isinstance(value, Mapping):
            _reject_unsafe_raw_value(value)
        return value

    @model_validator(mode="after")
    def _validate_case(self) -> Self:
        if not self.completion_claim.strip():
            raise ValueError("coding verification case requires completionClaim")
        if self.run_on == "child" and self.spawn_depth <= 0:
            raise ValueError("child coding verification case requires spawnDepth > 0")
        if self.run_on == "main" and self.spawn_depth != 0:
            raise ValueError("main coding verification case requires spawnDepth=0")
        if self.category == "child_scoped_verification" and self.run_on != "child":
            raise ValueError("child_scoped_verification requires runOn=child")
        if self.category != "child_scoped_verification" and self.run_on != "main":
            raise ValueError("non-child coding verification fixtures must use runOn=main")
        if self.category == "audit_only_unverified_claim":
            if self.authority != "audit_only_no_block" or self.contract.on_missing != "audit":
                raise ValueError("audit-only coding case must stay audit-only")
        elif self.authority == "audit_only_no_block":
            raise ValueError("audit_only_no_block authority is limited to audit-only cases")
        elif self.contract.on_missing != "block_final_answer":
            raise ValueError("blocking coding verification cases require block_final_answer policy")
        if self.authority == "child_local_evidence_only" and self.run_on != "child":
            raise ValueError("child-local authority requires child scope")
        _validate_contract_boundary(self)
        _validate_records(self)
        _validate_expected_verdict(self)
        return self


class CodingVerificationEvidenceFixture(BaseModel):
    model_config = _MODEL_CONFIG

    schema_version: Literal["codingVerificationEvidenceFixture.v1"] = Field(
        alias="schemaVersion",
    )
    fixture_id: str = Field(alias="fixtureId")
    source_runtime: Literal["typescript-core-agent"] = Field(alias="sourceRuntime")
    recording_mode: Literal["local_diagnostic_fixture"] = Field(alias="recordingMode")
    redaction_status: Literal["verified"] = Field(alias="redactionStatus")
    local_diagnostic: Literal[True] = Field(alias="localDiagnostic")
    attachment_flags: CodingVerificationAttachmentFlags = Field(alias="attachmentFlags")
    cases: tuple[CodingVerificationEvidenceCase, ...]

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
            raise ValueError("coding verification caseIds must be unique")
        categories = {case.category for case in self.cases}
        if not _REQUIRED_CATEGORIES.issubset(categories):
            raise ValueError("coding verification fixture is missing required categories")
        return self


class CodingVerificationEvidenceProjection(BaseModel):
    model_config = _MODEL_CONFIG

    fixture_id: str = Field(alias="fixtureId")
    local_diagnostic: Literal[True] = Field(default=True, alias="localDiagnostic")
    attachment_flags: CodingVerificationAttachmentFlags = Field(alias="attachmentFlags")
    no_live_execution: Literal[True] = Field(alias="noLiveExecution")
    case_order: tuple[str, ...] = Field(alias="caseOrder")
    by_verdict_state: dict[str, int] = Field(alias="byVerdictState")
    by_category: dict[str, int] = Field(alias="byCategory")
    public_previews: dict[str, str] = Field(alias="publicPreviews")
    case_snapshots: dict[str, dict[str, object]] = Field(alias="caseSnapshots")

    def digest_only_report(self) -> dict[str, object]:
        """Return a public-safe report using sha256 digests instead of raw values.

        Verification commands are replaced with their sha256 digests so that no
        raw file paths, contents, or auth tokens leak into the public projection.
        """
        digest_snapshots: dict[str, dict[str, object]] = {}
        for case_id, snapshot in self.case_snapshots.items():
            safe = dict(snapshot)
            # Replace verification commands with digests
            commands = safe.get("verificationCommands")
            if isinstance(commands, (list, tuple)):
                safe["verificationCommandDigests"] = tuple(
                    evidence_command_digest(cmd) if isinstance(cmd, str) else cmd
                    for cmd in commands
                )
                del safe["verificationCommands"]
            # Replace planner commands with digests
            planner_commands = safe.get("plannerRecommendedCommands")
            if isinstance(planner_commands, (list, tuple)):
                safe["plannerRecommendedCommandDigests"] = tuple(
                    evidence_command_digest(cmd) if isinstance(cmd, str) else cmd
                    for cmd in planner_commands
                )
                del safe["plannerRecommendedCommands"]
            digest_snapshots[case_id] = safe
        return {
            "fixtureId": self.fixture_id,
            "localDiagnostic": True,
            "noLiveExecution": True,
            "caseOrder": list(self.case_order),
            "byVerdictState": dict(self.by_verdict_state),
            "byCategory": dict(self.by_category),
            "caseSnapshotDigests": digest_snapshots,
        }


def load_coding_verification_evidence_fixture(
    path: str | Path,
    *,
    fixture_root: str | Path | None = None,
) -> CodingVerificationEvidenceFixture:
    resolved_path = _resolve_fixture_path(path, fixture_root=fixture_root)
    with resolved_path.open("r", encoding="utf-8") as fixture_file:
        payload: object = json.load(fixture_file)
    return CodingVerificationEvidenceFixture.model_validate(payload)


def project_coding_verification_evidence_fixture(
    fixture: CodingVerificationEvidenceFixture | Mapping[str, Any],
) -> CodingVerificationEvidenceProjection:
    safe_fixture = _validated_fixture_snapshot(fixture)
    public_previews: dict[str, str] = {}
    case_snapshots: dict[str, dict[str, object]] = {}
    verdicts: list[EvidenceContractVerdict] = []
    for case in safe_fixture.cases:
        verdict = _evaluate_coding_verification_case(case)
        verdicts.append(verdict)
        preview = _public_preview(case)
        public_previews[case.case_id] = preview
        snapshot = _case_snapshot(case, verdict, preview=preview)
        _reject_unsafe_public_snapshot(snapshot)
        case_snapshots[case.case_id] = snapshot
    return CodingVerificationEvidenceProjection(
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
    fixture: CodingVerificationEvidenceFixture | Mapping[str, Any],
) -> CodingVerificationEvidenceFixture:
    if isinstance(fixture, CodingVerificationEvidenceFixture):
        return CodingVerificationEvidenceFixture.model_validate(
            fixture.model_dump(by_alias=True, mode="json", warnings=False)
        )
    return CodingVerificationEvidenceFixture.model_validate(fixture)


def _case_snapshot(
    case: CodingVerificationEvidenceCase,
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
        "lastCodeMutationAt": case.last_code_mutation_at,
        "completionClaim": sanitize_tool_preview(case.completion_claim),
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
        "verificationCommands": _verification_commands(verdict, case.records),
        "plannerRecommendedCommands": _planner_recommended_commands(case.records),
        "trafficAttached": verdict_report.traffic_attached,
        "executionAttached": verdict_report.execution_attached,
    }
    return snapshot


def _verification_commands(
    verdict: EvidenceContractVerdict,
    records: tuple[EvidenceRecord, ...],
) -> tuple[str, ...]:
    commands: list[str] = []
    for record in verdict.matched_evidence:
        if record.type != "TestRun":
            continue
        command = record.fields.get("command")
        if isinstance(command, str) and command not in commands:
            commands.append(command)
    for record in records:
        if record.type != "TestRun":
            continue
        command = record.fields.get("command")
        if isinstance(command, str) and command not in commands:
            commands.append(command)
    for failure in verdict.failures:
        if failure.requirement_type != "TestRun":
            continue
        actual = failure.metadata.get("actual")
        if isinstance(actual, str) and actual not in commands:
            commands.append(actual)
    return tuple(commands)


def _planner_recommended_commands(records: tuple[EvidenceRecord, ...]) -> tuple[str, ...]:
    commands: list[str] = []
    for record in records:
        if record.type != "custom:ProjectVerificationPlanner":
            continue
        for command in _planner_command_strings(record.fields.get("commands")):
            if command not in commands:
                commands.append(command)
    return tuple(commands)


def _recorded_types_in_requirement_order(
    case: CodingVerificationEvidenceCase,
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


def _public_preview(case: CodingVerificationEvidenceCase) -> str:
    redacted = _PRODUCTION_PATH_RE.sub("[redacted-path]", sanitize_tool_preview(case.public_preview))
    return redacted


def _validate_contract_boundary(case: CodingVerificationEvidenceCase) -> None:
    if case.contract.traffic_attached or case.contract.execution_attached:
        raise ValueError("coding verification contracts must stay traffic-free")
    if case.contract.when is None:
        raise ValueError("coding verification contract must include boundary metadata")
    boundary = case.contract.when.get("lastCodeMutation")
    if not _strict_number_equal(boundary, case.last_code_mutation_at):
        raise ValueError("contract lastCodeMutation must match case boundary")
    requirement_types = tuple(requirement.type for requirement in case.contract.requirements)
    if "GitDiff" not in requirement_types:
        raise ValueError("coding verification contract must require GitDiff")
    if case.category == "diagnostics_verification_pass":
        if "CodeDiagnostics" not in requirement_types or "TestRun" in requirement_types:
            raise ValueError(
                "diagnostics verification fixture must require GitDiff and CodeDiagnostics"
            )
    elif "TestRun" not in requirement_types:
        raise ValueError("coding verification contract must require GitDiff and TestRun")
    elif "CodeDiagnostics" in requirement_types:
        raise ValueError("CodeDiagnostics post-edit verification is limited to diagnostics fixtures")
    if case.category == "commit_checkpoint_verification" and "CommitCheckpoint" not in requirement_types:
        raise ValueError("commit checkpoint fixture must require CommitCheckpoint evidence")
    if (
        case.category == "coding_child_review_freshness"
        and "custom:CodingChildReview" not in requirement_types
    ):
        raise ValueError("child review freshness fixture must require reviewer evidence")
    if (
        case.category == "planner_recommended_verification"
        and "custom:ProjectVerificationPlanner" not in requirement_types
    ):
        raise ValueError("planner verification fixture must require planner evidence")


def _validate_records(case: CodingVerificationEvidenceCase) -> None:
    for record in case.records:
        if record.source.kind not in {"tool_trace", "transcript"}:
            raise ValueError("coding verification records must be recorded tool/transcript metadata")
        if case.run_on == "child":
            if record.source.kind != "transcript":
                raise ValueError("child coding evidence must come from child transcript metadata")
            if record.source.metadata.get("executionBoundary") != "child":
                raise ValueError("child coding evidence requires child execution boundary metadata")
        elif record.source.metadata.get("executionBoundary") == "child":
            raise ValueError("main coding evidence cannot carry child execution boundary metadata")
        if record.type == "TestRun":
            if "command" not in record.fields or "exitCode" not in record.fields:
                raise ValueError("TestRun evidence requires command and exitCode fields")
        if record.type == "GitDiff" and "changedFiles" not in record.fields:
            raise ValueError("GitDiff evidence requires changedFiles metadata")
        if record.type == "CodeDiagnostics":
            _validate_code_diagnostics_record(case, record)
        if record.type == "CommitCheckpoint" and "checkpointId" not in record.fields:
            raise ValueError("CommitCheckpoint evidence requires checkpointId metadata")
        if record.type == "custom:CodingChildReview":
            _validate_coding_child_review_record(case, record)
        if record.type == "custom:ProjectVerificationPlanner":
            _validate_project_verification_planner_record(case, record)
        _reject_unsafe_public_snapshot(
            {
                "preview": record.preview,
                "fields": record.model_dump(by_alias=True, mode="json").get("fields", {}),
            }
        )


def _validate_code_diagnostics_record(
    case: CodingVerificationEvidenceCase,
    record: EvidenceRecord,
) -> None:
    if case.category != "diagnostics_verification_pass":
        raise ValueError("CodeDiagnostics evidence is limited to diagnostics verification fixtures")
    if record.source.kind != "tool_trace" or record.source.tool_name != "CodeDiagnostics":
        raise ValueError("CodeDiagnostics evidence must be recorded CodeDiagnostics tool metadata")
    if record.source.metadata.get("recordedOnly") is not True:
        raise ValueError("CodeDiagnostics evidence must be recorded-only")
    if record.source.metadata.get("evidenceKind") != "diagnostics":
        raise ValueError("CodeDiagnostics evidence requires diagnostics evidenceKind metadata")
    if record.source.metadata.get("action") != "diagnostics":
        raise ValueError("CodeDiagnostics evidence requires diagnostics action metadata")
    if record.observed_at <= case.last_code_mutation_at:
        raise ValueError("CodeDiagnostics evidence must be observed after the latest mutation")
    if not _strict_number_equal(
        record.metadata.get("lastCodeMutation"),
        case.last_code_mutation_at,
    ):
        raise ValueError("CodeDiagnostics evidence must carry latest mutation metadata")
    missing_fields = _CODE_DIAGNOSTICS_REQUIRED_FIELDS.difference(record.fields.keys())
    if missing_fields:
        raise ValueError("CodeDiagnostics evidence is missing required metadata fields")
    _reject_code_diagnostics_raw_output_keys(record.fields)
    _reject_code_diagnostics_raw_output_keys(record.metadata)
    _reject_code_diagnostics_raw_output_keys(record.source.metadata)
    checker = record.fields.get("checker")
    if checker not in _CODE_DIAGNOSTICS_CHECKERS:
        raise ValueError("CodeDiagnostics checker metadata is invalid")
    if record.fields.get("passed") is not True:
        raise ValueError("CodeDiagnostics evidence requires passed=true")
    if record.fields.get("exitCode") != 0 or isinstance(record.fields.get("exitCode"), bool):
        raise ValueError("CodeDiagnostics evidence requires exitCode=0")
    diagnostic_count = record.fields.get("diagnosticCount")
    if diagnostic_count != 0 or isinstance(diagnostic_count, bool):
        raise ValueError("CodeDiagnostics evidence requires diagnosticCount=0")
    public_safe_fields = record.metadata.get("publicSafeFields")
    if not isinstance(public_safe_fields, (list, tuple)):
        raise ValueError("CodeDiagnostics evidence requires publicSafeFields metadata")
    if not set(public_safe_fields).issubset(_CODE_DIAGNOSTICS_PUBLIC_FIELDS):
        raise ValueError("CodeDiagnostics publicSafeFields must exclude raw diagnostic output")


def _validate_coding_child_review_record(
    case: CodingVerificationEvidenceCase,
    record: EvidenceRecord,
) -> None:
    if case.category != "coding_child_review_freshness":
        raise ValueError("coding child review evidence is limited to freshness fixtures")
    if record.source.kind != "transcript" or record.source.tool_name != "SpawnAgent":
        raise ValueError("coding child review evidence must be recorded SpawnAgent transcript metadata")
    if record.fields.get("reviewerPersona") != "reviewer":
        raise ValueError("coding child review evidence requires reviewer persona metadata")
    if record.fields.get("reviewedMutationAt") != case.last_code_mutation_at:
        raise ValueError("coding child review evidence must target the latest mutation boundary")
    tool_call_count = record.fields.get("toolCallCount")
    if isinstance(tool_call_count, bool) or not isinstance(tool_call_count, int):
        raise ValueError("coding child review evidence requires deterministic toolCallCount metadata")
    if tool_call_count <= 0:
        raise ValueError("coding child review evidence requires reviewer tool evidence")
    if record.fields.get("finalTextPresent") is not True:
        raise ValueError("coding child review evidence requires non-empty reviewer final text metadata")


def _validate_project_verification_planner_record(
    case: CodingVerificationEvidenceCase,
    record: EvidenceRecord,
) -> None:
    if case.category != "planner_recommended_verification":
        raise ValueError("project verification planner evidence is limited to planner fixtures")
    if record.source.kind != "transcript" or record.source.tool_name != "ProjectVerificationPlanner":
        raise ValueError(
            "project verification planner evidence must be recorded "
            "ProjectVerificationPlanner transcript metadata"
        )
    if record.source.metadata.get("evidenceKind") != "verification_plan":
        raise ValueError("project verification planner evidence requires verification_plan metadata")
    if record.source.metadata.get("recordedOnly") is not True:
        raise ValueError("project verification planner evidence must be recorded-only")
    cwd = record.fields.get("cwd")
    if not _non_empty_string(cwd):
        raise ValueError("project verification planner evidence requires cwd metadata")
    commands = record.fields.get("commands")
    if not isinstance(commands, (list, tuple)) or not commands:
        raise ValueError("project verification planner evidence requires command object metadata")
    command_strings = tuple(
        _validate_project_verification_command(command, cwd)
        for command in commands
    )
    command_count = record.fields.get("commandCount")
    if isinstance(command_count, bool) or command_count != len(commands):
        raise ValueError("project verification planner commandCount must match commands")
    project_types = record.fields.get("projectTypes")
    if not isinstance(project_types, (list, tuple)) or not project_types or not all(
        isinstance(project_type, str) and project_type.strip()
        for project_type in project_types
    ):
        raise ValueError("project verification planner evidence requires projectTypes metadata")
    warnings = record.fields.get("warnings")
    if not isinstance(warnings, (list, tuple)) or not all(
        isinstance(warning, str) for warning in warnings
    ):
        raise ValueError("project verification planner evidence requires warnings metadata")
    test_commands = {
        command
        for evidence_record in case.records
        if evidence_record.type == "TestRun"
        and isinstance(command := evidence_record.fields.get("command"), str)
    }
    if not test_commands.intersection(command_strings):
        raise ValueError("project verification planner command must feed selected TestRun command")


def _validate_project_verification_command(
    command: object,
    planner_cwd: object,
) -> str:
    if not isinstance(command, Mapping):
        raise ValueError("project verification planner commands must be structured objects")
    missing_fields = _PLANNER_COMMAND_REQUIRED_FIELDS.difference(command.keys())
    if missing_fields:
        raise ValueError("project verification planner command is missing TS metadata fields")
    kind = command.get("kind")
    if kind not in _PLANNER_COMMAND_KINDS:
        raise ValueError("project verification planner command kind is invalid")
    command_text = command.get("command")
    if not isinstance(command_text, str) or not command_text.strip():
        raise ValueError("project verification planner command text must be non-empty")
    cwd = command.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip() or cwd != planner_cwd:
        raise ValueError("project verification planner command cwd must match planner cwd")
    if command.get("runner") != "TestRun":
        raise ValueError("project verification planner command runner must be TestRun")
    if command.get("confidence") not in _PLANNER_COMMAND_CONFIDENCES:
        raise ValueError("project verification planner command confidence is invalid")
    reason = command.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("project verification planner command reason must be non-empty")
    return command_text


def _planner_command_strings(commands: object) -> tuple[str, ...]:
    if not isinstance(commands, (list, tuple)):
        return ()
    command_strings: list[str] = []
    for command in commands:
        if not isinstance(command, Mapping):
            continue
        command_text = command.get("command")
        if isinstance(command_text, str) and command_text.strip():
            command_strings.append(command_text)
    return tuple(command_strings)


def _reject_code_diagnostics_raw_output_keys(value: object) -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if _normalize_key(key) in _CODE_DIAGNOSTICS_FORBIDDEN_FIELD_NAMES:
                raise ValueError("CodeDiagnostics evidence must not include raw diagnostic output")
            _reject_code_diagnostics_raw_output_keys(nested_value)
        return
    if isinstance(value, list | tuple):
        for item in value:
            _reject_code_diagnostics_raw_output_keys(item)


def _evaluate_coding_verification_case(
    case: CodingVerificationEvidenceCase,
) -> EvidenceContractVerdict:
    verdict = evaluate_evidence_contract(case.contract, case.records)
    if case.category != "coding_child_review_freshness":
        return verdict
    return _enforce_strict_child_review_freshness(case, verdict)


def _enforce_strict_child_review_freshness(
    case: CodingVerificationEvidenceCase,
    verdict: EvidenceContractVerdict,
) -> EvidenceContractVerdict:
    stale_review_records = tuple(
        record
        for record in verdict.matched_evidence
        if record.type == "custom:CodingChildReview"
        and record.observed_at <= case.last_code_mutation_at
    )
    if not stale_review_records:
        return verdict

    fresh_review_record = _fresh_child_review_record(case)
    if fresh_review_record is not None:
        matched_evidence = tuple(
            fresh_review_record if record in stale_review_records else record
            for record in verdict.matched_evidence
        )
        return EvidenceContractVerdict(
            contractId=verdict.contract_id,
            ok=verdict.ok,
            state=verdict.state,
            enforcement=verdict.enforcement,
            missingRequirements=verdict.missing_requirements,
            matchedEvidence=matched_evidence,
            failures=verdict.failures,
            retryMessage=verdict.retry_message,
            requirementCoverage=verdict.requirement_coverage,
            trafficAttached=False,
            executionAttached=False,
        )

    strict_failures = tuple(
        EvidenceContractFailure(
            code="EVIDENCE_CONTRACT_STALE",
            contractId=case.contract.id,
            requirementType="custom:CodingChildReview",
            message="Reviewer evidence must be observed strictly after the latest mutation.",
            metadata={
                "boundary": "last_code_mutation",
                "boundaryObservedAt": case.last_code_mutation_at,
                "recordObservedAt": record.observed_at,
            },
        )
        for record in stale_review_records
    )
    matched_evidence = tuple(
        record
        for record in verdict.matched_evidence
        if record not in stale_review_records
    )
    failures = (*verdict.failures, *strict_failures)
    return EvidenceContractVerdict(
        contractId=verdict.contract_id,
        ok=False,
        state="block_ready" if verdict.enforcement == "block_final_answer" else "failed",
        enforcement=verdict.enforcement,
        missingRequirements=verdict.missing_requirements,
        matchedEvidence=matched_evidence,
        failures=failures,
        retryMessage=verdict.retry_message,
        requirementCoverage=verdict.requirement_coverage,
        trafficAttached=False,
        executionAttached=False,
    )


def _fresh_child_review_record(
    case: CodingVerificationEvidenceCase,
) -> EvidenceRecord | None:
    review_requirements = tuple(
        requirement
        for requirement in case.contract.requirements
        if requirement.type == "custom:CodingChildReview"
    )
    if not review_requirements:
        return None
    requirement = review_requirements[0]
    candidates = tuple(
        record
        for record in case.records
        if record.type == "custom:CodingChildReview"
        and record.status == "ok"
        and record.observed_at > case.last_code_mutation_at
        and _matches_child_review_requirement(record, requirement.fields)
    )
    if not candidates:
        return None
    return min(candidates, key=lambda record: record.observed_at)


def _matches_child_review_requirement(
    record: EvidenceRecord,
    requirement_fields: Mapping[str, Any],
) -> bool:
    for field_name, matcher in requirement_fields.items():
        if not hasattr(matcher, "model_fields_set") or "equals" not in matcher.model_fields_set:
            return False
        expected = matcher.equals
        if record.fields.get(field_name) != expected:
            return False
    return True


def _validate_expected_verdict(case: CodingVerificationEvidenceCase) -> None:
    verdict = _evaluate_coding_verification_case(case)
    matched_types = tuple(record.type for record in verdict.matched_evidence)
    missing_types = tuple(requirement.type for requirement in verdict.missing_requirements)
    failure_codes = tuple(failure.code for failure in verdict.failures)
    if verdict.ok != case.expected_ok:
        raise ValueError("coding verification expectedOk does not match evaluated verdict")
    if verdict.state != case.expected_verdict_state:
        raise ValueError("coding verification expectedVerdictState does not match verdict")
    if missing_types != case.expected_missing_types:
        raise ValueError("coding verification expectedMissingTypes does not match verdict")
    if matched_types != case.expected_matched_types:
        raise ValueError("coding verification expectedMatchedTypes does not match verdict")
    if failure_codes != case.expected_failure_codes:
        raise ValueError("coding verification expectedFailureCodes does not match verdict")


def _strict_number_equal(left: object, right: int | float) -> bool:
    if isinstance(left, bool) or not isinstance(left, int | float):
        return False
    if type(left) is not type(right):
        return False
    return left == right


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reject_unsafe_public_snapshot(value: Mapping[str, object]) -> None:
    rendered = json.dumps(value, sort_keys=True)
    if _FORBIDDEN_PATH_RE.search(rendered):
        raise ValueError("coding verification public snapshot contains production paths")
    if any(token in rendered for token in _FORBIDDEN_PUBLIC_TOKENS):
        raise ValueError("coding verification public snapshot contains unsafe data")


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
        raise ValueError("coding verification fixture path must stay under fixture_root")
    return resolved_candidate


def _reject_unsafe_path_text(path_text: str) -> None:
    if _FORBIDDEN_PATH_RE.search(path_text):
        raise ValueError("coding verification fixtures must be local and non-production")


def _reject_unsafe_raw_value(value: object) -> None:
    _validate_json_like(value)
    if isinstance(value, str):
        if _FORBIDDEN_PATH_RE.search(value):
            raise ValueError("coding verification fixture contains unsafe path")
        if any(token in value for token in _FORBIDDEN_PUBLIC_TOKENS):
            raise ValueError("coding verification fixture contains unsafe data")
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = _normalize_key(key)
            if nested_value is True and normalized in _FORBIDDEN_RAW_KEY_TOKENS:
                raise ValueError("coding verification fixture cannot claim live behavior")
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
        raise ValueError("coding verification fixture values must be JSON-compatible")
    if isinstance(value, list | tuple):
        for item in value:
            _validate_json_like(item)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise ValueError("coding verification mappings must use string keys")
            _validate_json_like(nested_value)
        return
    raise ValueError("coding verification fixture values must be JSON-compatible")


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
    "CodingVerificationAttachmentFlags",
    "CodingVerificationEvidenceCase",
    "CodingVerificationEvidenceFixture",
    "CodingVerificationEvidenceProjection",
    "load_coding_verification_evidence_fixture",
    "project_coding_verification_evidence_fixture",
]
