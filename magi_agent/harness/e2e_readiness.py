from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal, TypeAlias

from pydantic import ConfigDict, Field

from magi_agent.ops.authority import FalseOnlyAuthorityModel


ReadinessStatus: TypeAlias = Literal["activation_blocked"]
ActivationState: TypeAlias = Literal["activation_blocked"]

FINAL_INTEGRATION_ROW_ID = "final_integration_readiness"
DEPLOYMENT_BLOCKER_ROW_ID = "deployment_canary_activation_blocker"

E2E_HARNESS_REQUIRED_ROW_IDS: tuple[str, ...] = (
    "combined_matrix",
    "request_shape_snapshot",
    "local_adk_turn_runner",
    "tool_schema_output_artifact_store",
    "toolhost_kernel_scheduler",
    "local_read_search_source_projection",
    "event_transcript_projection",
    "approval_pause_resume",
    "read_ledger_workspace_mutation_safety",
    "coding_read_before_edit_mutation_recipe",
    "coding_evidence_gate",
    "shell_testrun_bash_safe_subset",
    "csv_spreadsheet_backoffice",
    "research_routing_agent_materialization",
    "research_citation_final_gate",
    "web_search_fetch_provider_boundary",
    "knowledge_browser_source_boundary",
    "mcp_toolsearch_adapter",
    "child_runner_evidence_envelope",
    "coding_subagent_recipe",
    "parallel_research_child_runner",
    "checkpoint_context_compaction",
    "research_benchmark_eval_capture",
    FINAL_INTEGRATION_ROW_ID,
    DEPLOYMENT_BLOCKER_ROW_ID,
)

DEPLOYMENT_CANARY_ACTIVATION_BLOCKERS: tuple[str, ...] = (
    "deployment_not_approved",
    "routing_not_approved",
    "secrets_not_bound",
    "model_calls_not_approved",
    "live_traffic_not_attached",
)

E2E_AUTHORITY_FLAG_ALIASES: tuple[str, ...] = (
    "traffic",
    "productionAuthority",
    "userVisibleOutput",
    "liveToolExecution",
    "modelCall",
    "network",
    "browser",
    "memoryWrite",
    "workspaceMutation",
    "channelDelivery",
    "schedulerMutation",
    "dbWrite",
    "transcriptWrite",
    "sseWrite",
)

_MODEL_CONFIG = ConfigDict(revalidate_instances="always")

_CORE_LAYER = "Core substrate"
_DOMAIN_OWNED_LAYERS = frozenset(
    {
        "Coding recipe/harness/plugin",
        "Research recipe/harness/plugin",
        "General automation plugin/harness",
        "Provider boundary",
    }
)
_DOMAIN_ROW_MARKERS = (
    "coding",
    "research",
    "csv",
    "spreadsheet",
    "backoffice",
    "web_search",
    "knowledge_browser",
    "mcp",
)
_SENSITIVE_TEXT_RE = re.compile(
    r"(?i)"
    r"(authorization\s*[:=]?\s*\S+|auth\s*[:=]\s*\S+|"
    r"bearer\s+\S+|sk-[a-z0-9._-]+|"
    r"secret[\w:._/-]*|token[\w:._/-]*|credential[\w:._/-]*|"
    r"cookie\s*[:=]?\s*\S*|set-cookie\s*[:=]?\s*\S*|"
    r"password\s*[:=]?\s*\S*|api[\s_-]?key\s*[:=]?\s*\S*|"
    r"session[\s_-]?key\s*[:=]?\s*\S*|"
    r"raw[\s_-]?(?:prompt|output|tool|log|args?|results?)\s*[:=]?\s*[^,;|\n]*|"
    r"(?:prompt|output)\s*[:=]\s*[^,;|\n]+|"
    r"tool[\s_-]?(?:log|output|args?|results?)\s*[:=]?\s*[^,;|\n]*|"
    r"/users/[^\s\"']+|/workspace/[^\s\"']+|"
    r"/home/[^\s\"']+|[^\s\"']*\.ssh[^\s\"']*|"
    r"\bid_rsa\b|"
    r"raw[\w:._/-]*|private[\w:._/-]*)"
)
_REF_PATH_RE = re.compile(r"^[A-Za-z0-9_./:-]+$")


class _ReadinessModel(FalseOnlyAuthorityModel):
    model_config = _MODEL_CONFIG


class E2EReadinessAuthorityFlags(_ReadinessModel):
    traffic: Literal[False] = False
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    user_visible_output: Literal[False] = Field(default=False, alias="userVisibleOutput")
    live_tool_execution: Literal[False] = Field(default=False, alias="liveToolExecution")
    model_call: Literal[False] = Field(default=False, alias="modelCall")
    network: Literal[False] = False
    browser: Literal[False] = False
    memory_write: Literal[False] = Field(default=False, alias="memoryWrite")
    workspace_mutation: Literal[False] = Field(default=False, alias="workspaceMutation")
    channel_delivery: Literal[False] = Field(default=False, alias="channelDelivery")
    scheduler_mutation: Literal[False] = Field(default=False, alias="schedulerMutation")
    db_write: Literal[False] = Field(default=False, alias="dbWrite")
    transcript_write: Literal[False] = Field(default=False, alias="transcriptWrite")
    sse_write: Literal[False] = Field(default=False, alias="sseWrite")


class MatrixRowRef(_ReadinessModel):
    path: str
    state: str = "existing"


class MatrixReadinessRow(_ReadinessModel):
    row_id: str = Field(alias="id")
    capability: str
    requested_by: tuple[str, ...] = Field(alias="requestedBy")
    latest_main_covered_refs: tuple[MatrixRowRef, ...] = Field(
        default=(),
        alias="latestMainCoveredRefs",
    )
    missing_implementation: tuple[MatrixRowRef, ...] = Field(
        default=(),
        alias="missingImplementation",
    )
    owning_layer: str = Field(alias="owningLayer")
    adk_primitive: str = Field(alias="adkPrimitive")
    pr_slice_assignment: str = Field(alias="prSliceAssignment")
    dependencies: tuple[str, ...] = ()
    activation_gate: str = Field(alias="activationGate")
    status: str
    default_off: bool = Field(alias="defaultOff")
    traffic_attached: bool = Field(alias="trafficAttached")
    notes: str = ""

    def public_summary(self) -> dict[str, object]:
        return {
            "rowId": _sanitize_public_text(self.row_id),
            "capability": _sanitize_public_text(self.capability),
            "requestedBy": tuple(_sanitize_public_text(value) for value in self.requested_by),
            "owningLayer": _sanitize_public_text(self.owning_layer),
            "status": _sanitize_public_text(self.status),
            "defaultOff": True,
            "trafficAttached": False,
            "dependencies": tuple(_sanitize_public_text(value) for value in self.dependencies),
        }

    def public_adk_primitive_justification(self) -> dict[str, object]:
        return {
            "rowId": _sanitize_public_text(self.row_id),
            "owningLayer": _sanitize_public_text(self.owning_layer),
            "adkPrimitive": _sanitize_public_text(self.adk_primitive),
            "dependencyRefs": tuple(_sanitize_public_text(value) for value in self.dependencies),
            "coveredRefs": tuple(_sanitize_public_ref(ref.path) for ref in self.latest_main_covered_refs),
        }


class DependencyViolation(_ReadinessModel):
    row_id: str = Field(alias="rowId")
    missing_dependencies: tuple[str, ...] = Field(alias="missingDependencies")

    def public_projection(self) -> dict[str, object]:
        return {
            "rowId": _sanitize_public_text(self.row_id),
            "missingDependencies": tuple(
                _sanitize_public_text(value) for value in self.missing_dependencies
            ),
        }


class GenericPrimitiveProof(_ReadinessModel):
    core_substrate_row_ids: tuple[str, ...] = Field(alias="coreSubstrateRowIds")
    recipe_or_provider_owned_row_ids: tuple[str, ...] = Field(
        alias="recipeOrProviderOwnedRowIds",
    )
    core_owned_domain_workflow_row_ids: tuple[str, ...] = Field(
        default=(),
        alias="coreOwnedDomainWorkflowRowIds",
    )

    def public_projection(self) -> dict[str, object]:
        return {
            "coreSubstrateRowIds": tuple(
                _sanitize_public_text(value) for value in self.core_substrate_row_ids
            ),
            "recipeOrProviderOwnedRowIds": tuple(
                _sanitize_public_text(value) for value in self.recipe_or_provider_owned_row_ids
            ),
            "coreOwnedDomainWorkflowRowIds": tuple(
                _sanitize_public_text(value) for value in self.core_owned_domain_workflow_row_ids
            ),
        }


class FinalIntegrationReadinessReport(_ReadinessModel):
    schema_version: Literal["e2eHarnessFinalIntegrationReadiness.v1"] = Field(
        default="e2eHarnessFinalIntegrationReadiness.v1",
        alias="schemaVersion",
    )
    status: ReadinessStatus = "activation_blocked"
    activation_state: ActivationState = Field(default="activation_blocked", alias="activationState")
    readiness_metadata_complete: bool = Field(alias="readinessMetadataComplete")
    activation_allowed: Literal[False] = Field(default=False, alias="activationAllowed")
    traffic_attached: Literal[False] = Field(default=False, alias="trafficAttached")
    production_authority: Literal[False] = Field(default=False, alias="productionAuthority")
    final_integration_row_id: str = Field(alias="finalIntegrationRowId")
    deployment_blocker_row_id: str = Field(alias="deploymentBlockerRowId")
    covered_row_ids: tuple[str, ...] = Field(alias="coveredRowIds")
    required_row_ids: tuple[str, ...] = Field(alias="requiredRowIds")
    prerequisite_missing_row_ids: tuple[str, ...] = Field(alias="prerequisiteMissingRowIds")
    implementation_gap_row_ids: tuple[str, ...] = Field(alias="implementationGapRowIds")
    default_off_violations: tuple[str, ...] = Field(alias="defaultOffViolations")
    traffic_attachment_violations: tuple[str, ...] = Field(alias="trafficAttachmentViolations")
    dependency_violations: tuple[DependencyViolation, ...] = Field(alias="dependencyViolations")
    deployment_activation_blockers: tuple[str, ...] = Field(alias="deploymentActivationBlockers")
    authority_flags: E2EReadinessAuthorityFlags = Field(alias="authorityFlags")
    generic_primitive_proof: GenericPrimitiveProof = Field(alias="genericPrimitiveProof")
    row_summaries: tuple[MatrixReadinessRow, ...] = Field(alias="rowSummaries")

    def public_projection(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "status": self.status,
            "activationState": self.activation_state,
            "readinessMetadataComplete": self.readiness_metadata_complete,
            "activationAllowed": False,
            "trafficAttached": False,
            "productionAuthority": False,
            "authorityFlags": self.authority_flags.model_dump(by_alias=True),
            "finalIntegrationRowId": _sanitize_public_text(self.final_integration_row_id),
            "deploymentBlockerRowId": _sanitize_public_text(self.deployment_blocker_row_id),
            "coveredRowIds": tuple(_sanitize_public_text(value) for value in self.covered_row_ids),
            "requiredRowIds": tuple(_sanitize_public_text(value) for value in self.required_row_ids),
            "prerequisiteMissingRowIds": tuple(
                _sanitize_public_text(value) for value in self.prerequisite_missing_row_ids
            ),
            "implementationGapRowIds": tuple(
                _sanitize_public_text(value) for value in self.implementation_gap_row_ids
            ),
            "defaultOffViolations": tuple(
                _sanitize_public_text(value) for value in self.default_off_violations
            ),
            "trafficAttachmentViolations": tuple(
                _sanitize_public_text(value) for value in self.traffic_attachment_violations
            ),
            "dependencyViolations": tuple(
                violation.public_projection() for violation in self.dependency_violations
            ),
            "deploymentActivationBlockers": tuple(
                _sanitize_public_text(value) for value in self.deployment_activation_blockers
            ),
            "genericPrimitiveProof": self.generic_primitive_proof.public_projection(),
            "rowSummaries": tuple(row.public_summary() for row in self.row_summaries),
            "adkPrimitiveJustifications": tuple(
                row.public_adk_primitive_justification() for row in self.row_summaries
            ),
        }


def build_final_integration_readiness_report(
    rows: Iterable[Mapping[str, object]],
) -> FinalIntegrationReadinessReport:
    parsed_rows = tuple(MatrixReadinessRow.model_validate(row) for row in rows)
    rows_by_id = {row.row_id: row for row in parsed_rows}
    required_row_ids = tuple(
        row_id
        for row_id in E2E_HARNESS_REQUIRED_ROW_IDS
        if row_id not in {FINAL_INTEGRATION_ROW_ID, DEPLOYMENT_BLOCKER_ROW_ID}
    )
    missing_expected_ids = tuple(
        row_id for row_id in E2E_HARNESS_REQUIRED_ROW_IDS if row_id not in rows_by_id
    )
    dependency_violations = _dependency_violations(parsed_rows, rows_by_id)
    final_row = rows_by_id.get(FINAL_INTEGRATION_ROW_ID)
    deployment_row = rows_by_id.get(DEPLOYMENT_BLOCKER_ROW_ID)
    prerequisite_missing_row_ids = (
        missing_expected_ids
        + tuple(
            row.row_id
            for row in parsed_rows
            if row.row_id in required_row_ids and row.status == "missing"
        )
    )
    implementation_gap_row_ids = tuple(
        row.row_id
        for row in parsed_rows
        if row.row_id in required_row_ids and row.missing_implementation
    )
    default_off_violations = tuple(row.row_id for row in parsed_rows if row.default_off is not True)
    traffic_attachment_violations = tuple(
        row.row_id for row in parsed_rows if row.traffic_attached is not False
    )
    readiness_metadata_complete = (
        _row_is_metadata_complete(final_row)
        and _row_is_metadata_complete(deployment_row)
        and prerequisite_missing_row_ids == ()
        and implementation_gap_row_ids == ()
        and default_off_violations == ()
        and traffic_attachment_violations == ()
        and dependency_violations == ()
    )

    return FinalIntegrationReadinessReport(
        readinessMetadataComplete=readiness_metadata_complete,
        finalIntegrationRowId=FINAL_INTEGRATION_ROW_ID,
        deploymentBlockerRowId=DEPLOYMENT_BLOCKER_ROW_ID,
        coveredRowIds=tuple(row.row_id for row in parsed_rows),
        requiredRowIds=required_row_ids,
        prerequisiteMissingRowIds=prerequisite_missing_row_ids,
        implementationGapRowIds=implementation_gap_row_ids,
        defaultOffViolations=default_off_violations,
        trafficAttachmentViolations=traffic_attachment_violations,
        dependencyViolations=dependency_violations,
        deploymentActivationBlockers=DEPLOYMENT_CANARY_ACTIVATION_BLOCKERS
        if deployment_row is not None
        else (),
        authorityFlags=E2EReadinessAuthorityFlags(),
        genericPrimitiveProof=_generic_primitive_proof(parsed_rows),
        rowSummaries=parsed_rows,
    )


def _dependency_violations(
    rows: Sequence[MatrixReadinessRow],
    rows_by_id: Mapping[str, MatrixReadinessRow],
) -> tuple[DependencyViolation, ...]:
    violations: list[DependencyViolation] = []
    for row in rows:
        missing = tuple(dependency for dependency in row.dependencies if dependency not in rows_by_id)
        if missing:
            violations.append(
                DependencyViolation(rowId=row.row_id, missingDependencies=missing)
            )
    return tuple(violations)


def _row_is_metadata_complete(row: MatrixReadinessRow | None) -> bool:
    if row is None:
        return False
    return (
        row.status == "activation_blocked"
        and row.default_off is True
        and row.traffic_attached is False
        and row.missing_implementation == ()
    )


def _generic_primitive_proof(rows: Sequence[MatrixReadinessRow]) -> GenericPrimitiveProof:
    core_ids = tuple(row.row_id for row in rows if row.owning_layer == _CORE_LAYER)
    recipe_or_provider_ids = tuple(
        row.row_id for row in rows if row.owning_layer in _DOMAIN_OWNED_LAYERS
    )
    core_domain_ids = tuple(
        row.row_id
        for row in rows
        if row.owning_layer == _CORE_LAYER and any(marker in row.row_id for marker in _DOMAIN_ROW_MARKERS)
    )
    return GenericPrimitiveProof(
        coreSubstrateRowIds=core_ids,
        recipeOrProviderOwnedRowIds=recipe_or_provider_ids,
        coreOwnedDomainWorkflowRowIds=core_domain_ids,
    )


def _sanitize_public_ref(value: str) -> str:
    clean = _sanitize_public_text(value)
    if clean == "[redacted]":
        return clean
    if not _REF_PATH_RE.match(clean):
        return "[redacted]"
    return clean


def _sanitize_public_text(value: object) -> str:
    clean = str(value).strip()
    clean = _SENSITIVE_TEXT_RE.sub("[redacted]", clean)
    clean = re.sub(
        r"(?i)\b("
        r"raw|private|secret|token|authorization|bearer|credential|cookie|"
        r"auth|password|api[\s_-]?key|session[\s_-]?key|set-cookie|"
        r"raw[\s_-]?(?:prompt|output|tool|log|args?|results?)|"
        r"tool[\s_-]?(?:log|output|args?|results?)"
        r")\b",
        "[redacted]",
        clean,
    )
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or "[redacted]"


__all__ = [
    "DEPLOYMENT_CANARY_ACTIVATION_BLOCKERS",
    "DEPLOYMENT_BLOCKER_ROW_ID",
    "E2E_AUTHORITY_FLAG_ALIASES",
    "E2E_HARNESS_REQUIRED_ROW_IDS",
    "E2EReadinessAuthorityFlags",
    "FINAL_INTEGRATION_ROW_ID",
    "FinalIntegrationReadinessReport",
    "GenericPrimitiveProof",
    "MatrixReadinessRow",
    "build_final_integration_readiness_report",
]
