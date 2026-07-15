"""Authoritative evidence closure contracts for completion evaluation.

The contracts in this module are deliberately inert.  They do not read a store,
activate a policy, or execute an effect.  Instead, they make the complete input
to a completion evaluator immutable and mechanically cross-checkable.
"""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, ValidationInfo, field_validator, model_validator

from magi_agent.execution_authority.contracts import (
    Requirement,
    ResearchClaimRequirement,
    ResearchProofObligation,
    TaskContractSnapshot,
    canonical_task_contract_digest,
)
from magi_agent.execution_authority.envelopes import (
    ActionSnapshot,
    AttemptSnapshot,
    CompletionVerdict,
    DependencyHealth,
    EnvelopeModel,
    EvidenceEdge,
    EvidenceNode,
    FinalizationRequest,
    canonical_evidence_node_digest,
)
from magi_agent.execution_authority.state_machine import (
    ActionState,
    CompletionStatus,
    EvidenceKind,
    EvidenceSemanticClass,
    RequirementState,
)


ResearchClaimState = Literal[
    "satisfied",
    "unsatisfied",
    "conflicted",
    "insufficient_evidence",
    "blocked",
]
ConflictDisposition = Literal["none", "resolved", "disclosed", "blocked"]
VerificationMode = Literal["snapshot_verified", "terminal_evidence"]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _digest_payload(value: object) -> str:
    return "sha256:" + sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _model_digest(model: EnvelopeModel, *, exclude: frozenset[str]) -> str:
    return _digest_payload(model.model_dump(by_alias=True, mode="json", exclude=set(exclude)))


def _require_unique_sorted_strings(
    value: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if any(type(item) is not str or not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain nonblank exact strings")
    if value != tuple(sorted(value)) or len(value) != len(set(value)):
        raise ValueError(f"{field_name} must be unique and sorted")
    return value


def _require_unique_strings_in_order(
    value: tuple[str, ...],
    *,
    field_name: str,
) -> tuple[str, ...]:
    if any(type(item) is not str or not item.strip() for item in value):
        raise ValueError(f"{field_name} must contain nonblank exact strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{field_name} must be unique")
    return value


class EvidenceJournalAnchor(EnvelopeModel):
    """The immutable journal coordinate from which one evidence node was projected."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    evidence_id: str = Field(alias="evidenceId", min_length=1)
    partition_id: str = Field(alias="partitionId", min_length=1)
    journal_sequence: int = Field(alias="journalSequence", ge=1, strict=True)
    journal_event_hash: str = Field(alias="journalEventHash")


class EvidenceProjectionSnapshot(EnvelopeModel):
    """Exact evidence projection bytes sealed at a task admission barrier."""

    schema_id: Literal["magi.evidence_projection_snapshot.v1"] = Field(
        default="magi.evidence_projection_snapshot.v1",
        alias="schemaId",
    )
    task_contract_id: str = Field(alias="taskContractId", min_length=1)
    task_version: int = Field(alias="taskVersion", ge=1, strict=True)
    task_contract_digest: str = Field(alias="taskContractDigest")
    task_contract_snapshot_ref: str = Field(alias="taskContractSnapshotRef")
    task_partition_id: str = Field(alias="taskPartitionId", min_length=1)
    completion_epoch_id: str = Field(alias="completionEpochId", min_length=1)
    state_root: str = Field(alias="stateRoot")
    barrier_admission_sequence: int = Field(
        alias="barrierAdmissionSequence",
        ge=0,
        strict=True,
    )
    projection_id: str = Field(alias="projectionId", min_length=1)
    projection_compare_version: int = Field(
        alias="projectionCompareVersion",
        ge=0,
        strict=True,
    )
    nodes: tuple[EvidenceNode, ...] = Field(min_length=1)
    edges: tuple[EvidenceEdge, ...]
    source_journal_anchors: tuple[EvidenceJournalAnchor, ...] = Field(
        alias="sourceJournalAnchors",
        min_length=1,
    )
    evidence_root: str | None = Field(default=None, alias="evidenceRoot")

    @model_validator(mode="after")
    def _bind_exact_projection(self) -> Self:
        expected_partition = f"task:{self.task_contract_id}:{self.task_version}"
        if self.task_partition_id != expected_partition:
            raise ValueError("taskPartitionId does not match task identity")
        expected_ref = f"authority-task://{self.task_contract_digest}"
        if self.task_contract_snapshot_ref != expected_ref:
            raise ValueError("taskContractSnapshotRef does not match taskContractDigest")

        node_ids = tuple(node.evidence_id for node in self.nodes)
        if node_ids != tuple(sorted(node_ids)) or len(node_ids) != len(set(node_ids)):
            raise ValueError("evidence nodes must be unique and sorted by evidenceId")
        edge_ids = tuple(edge.edge_id for edge in self.edges)
        if edge_ids != tuple(sorted(edge_ids)) or len(edge_ids) != len(set(edge_ids)):
            raise ValueError("evidence edges must be unique and sorted by edgeId")

        expected_anchors = tuple(
            EvidenceJournalAnchor(
                evidenceId=node.evidence_id,
                partitionId=node.partition_id,
                journalSequence=node.journal_sequence,
                journalEventHash=node.journal_event_hash,
            )
            for node in self.nodes
        )
        if self.source_journal_anchors != expected_anchors:
            raise ValueError("sourceJournalAnchors must exactly cover the projected evidence nodes")
        journal_coordinates = tuple(
            (anchor.partition_id, anchor.journal_sequence) for anchor in self.source_journal_anchors
        )
        if len(journal_coordinates) != len(set(journal_coordinates)):
            raise ValueError("sourceJournalAnchors contain a duplicate journal coordinate")
        event_hashes = tuple(anchor.journal_event_hash for anchor in self.source_journal_anchors)
        if len(event_hashes) != len(set(event_hashes)):
            raise ValueError("sourceJournalAnchors contain a duplicate event hash")

        for node in self.nodes:
            coordinate_checks = (
                ("taskContractId", node.task_contract_id, self.task_contract_id),
                ("taskVersion", node.task_version, self.task_version),
                ("taskContractDigest", node.task_contract_digest, self.task_contract_digest),
                ("completionEpochId", node.completion_epoch_id, self.completion_epoch_id),
                ("stateRoot", node.state_root, self.state_root),
            )
            for alias, observed, expected in coordinate_checks:
                if observed != expected:
                    raise ValueError(f"evidence node {alias} does not match projection")
            if node.admission_sequence > self.barrier_admission_sequence:
                raise ValueError("evidence node admissionSequence exceeds the sealed barrier")

        known_ids = set(node_ids)
        edge_shapes: set[tuple[str, str, str]] = set()
        for edge in self.edges:
            if edge.source_evidence_id not in known_ids or edge.target_evidence_id not in known_ids:
                raise ValueError("evidence edge endpoint does not exist in the projection")
            shape = (
                edge.source_evidence_id,
                edge.target_evidence_id,
                edge.kind,
            )
            if shape in edge_shapes:
                raise ValueError("evidence projection contains a duplicate semantic edge")
            edge_shapes.add(shape)
        for node in self.nodes:
            for parent_id in node.parent_evidence_ids:
                if parent_id not in known_ids:
                    raise ValueError("evidence parent does not exist in the projection")
                if not any(
                    edge.source_evidence_id == parent_id
                    and edge.target_evidence_id == node.evidence_id
                    for edge in self.edges
                ):
                    raise ValueError("evidence parent lacks an anchored projection edge")

        expected_root = _model_digest(self, exclude=frozenset({"evidence_root"}))
        if self.evidence_root is not None and self.evidence_root != expected_root:
            raise ValueError("evidenceRoot does not match the exact evidence projection")
        object.__setattr__(self, "evidence_root", expected_root)
        return self


def canonical_evidence_projection_root(snapshot: EvidenceProjectionSnapshot) -> str:
    """Return the root after revalidating every embedded node and edge."""

    if type(snapshot) is not EvidenceProjectionSnapshot:
        raise TypeError("snapshot must be an exact EvidenceProjectionSnapshot")
    validated = EvidenceProjectionSnapshot.model_validate(
        snapshot.model_dump(by_alias=True, mode="json")
    )
    assert validated.evidence_root is not None
    return validated.evidence_root


class ResearchClaimEvidenceBinding(EnvelopeModel):
    """Claim-local proof selection and research-policy disposition."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    claim_id: str = Field(alias="claimId", min_length=1)
    proposition_digest: str = Field(alias="propositionDigest")
    state: ResearchClaimState
    proof_evidence_ids: tuple[str, ...] = Field(alias="proofEvidenceIds")
    discovery_evidence_ids: tuple[str, ...] = Field(alias="discoveryEvidenceIds")
    conflict_disposition: ConflictDisposition = Field(alias="conflictDisposition")
    resolution_evidence_ids: tuple[str, ...] = Field(alias="resolutionEvidenceIds")
    disclosure_evidence_ids: tuple[str, ...] = Field(alias="disclosureEvidenceIds")
    stopping_rules_satisfied: tuple[str, ...] = Field(alias="stoppingRulesSatisfied")
    stopping_evidence_ids: tuple[str, ...] = Field(alias="stoppingEvidenceIds")
    snippet_acceptance_evidence_ids: tuple[str, ...] = Field(alias="snippetAcceptanceEvidenceIds")

    @field_validator(
        "proof_evidence_ids",
        "discovery_evidence_ids",
        "resolution_evidence_ids",
        "disclosure_evidence_ids",
        "stopping_evidence_ids",
        "snippet_acceptance_evidence_ids",
    )
    @classmethod
    def _ordered_evidence_ids(
        cls,
        value: tuple[str, ...],
        info: ValidationInfo,
    ) -> tuple[str, ...]:
        return _require_unique_sorted_strings(
            value,
            field_name=info.field_name or "evidence IDs",
        )

    @field_validator("stopping_rules_satisfied")
    @classmethod
    def _unique_stopping_rules(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_strings_in_order(
            value,
            field_name="stoppingRulesSatisfied",
        )

    @model_validator(mode="after")
    def _validate_local_sets(self) -> Self:
        if self.state == "satisfied" and not self.proof_evidence_ids:
            raise ValueError("satisfied research claims require proof evidence")
        if set(self.proof_evidence_ids).intersection(self.discovery_evidence_ids):
            raise ValueError("proof and discovery evidence IDs must be disjoint")
        proof_ids = set(self.proof_evidence_ids)
        for field_name, values in (
            ("resolutionEvidenceIds", self.resolution_evidence_ids),
            ("disclosureEvidenceIds", self.disclosure_evidence_ids),
            ("snippetAcceptanceEvidenceIds", self.snippet_acceptance_evidence_ids),
        ):
            if not set(values).issubset(proof_ids):
                raise ValueError(f"{field_name} must be included in proofEvidenceIds")
        return self


class RequirementEvidenceBinding(EnvelopeModel):
    """The exact evidence selected for one terminal Task Contract requirement."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    requirement_id: str = Field(alias="requirementId", min_length=1)
    state: RequirementState
    proof_evidence_ids: tuple[str, ...] = Field(alias="proofEvidenceIds")
    research_claims: tuple[ResearchClaimEvidenceBinding, ...] = Field(alias="researchClaims")

    @field_validator("proof_evidence_ids")
    @classmethod
    def _sorted_proof_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_sorted_strings(value, field_name="proofEvidenceIds")

    @model_validator(mode="after")
    def _terminal_requirement(self) -> Self:
        if self.state in {RequirementState.PENDING, RequirementState.SUPERSEDED}:
            raise ValueError("requirement evidence binding must use a terminal state")
        if self.state is RequirementState.SATISFIED and not self.proof_evidence_ids:
            raise ValueError("satisfied requirement requires proofEvidenceIds")
        claim_ids = tuple(claim.claim_id for claim in self.research_claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("research claims must be unique")
        return self


_TRUST_QUERY_CLASS: dict[str, str] = {
    "official": "official_primary",
    "peer_reviewed": "peer_reviewed_primary",
    "first_party": "first_party_data",
    "reputable_secondary": "reputable_secondary",
    "exploratory": "exploratory",
    "untrusted_snippet": "exploratory",
}
_PRIMARY_TRUST_TIERS = {"official", "peer_reviewed", "first_party"}
_SECONDARY_TRUST_TIERS = {"reputable_secondary", "exploratory"}
_SOURCE_EVIDENCE_KINDS = {
    EvidenceKind.SOURCE_SNAPSHOT,
    EvidenceKind.SOURCE_SPAN,
    EvidenceKind.EXTRACTION,
}


def _validate_source_class_matrix(node: EvidenceNode) -> str:
    source = node.research_source
    if source is None:
        raise ValueError("research source evidence requires researchSource metadata")
    if source.source_class == "primary" and source.trust_tier not in _PRIMARY_TRUST_TIERS:
        raise ValueError("primary sourceClass has an incompatible trustTier")
    if source.source_class == "secondary" and source.trust_tier not in _SECONDARY_TRUST_TIERS:
        raise ValueError("secondary sourceClass has an incompatible trustTier")
    if source.source_class == "snippet" and source.trust_tier != "untrusted_snippet":
        raise ValueError("snippet sourceClass requires untrusted_snippet trustTier")
    return _TRUST_QUERY_CLASS[source.trust_tier]


def _validate_research_claim(
    *,
    requirement: Requirement,
    research: ResearchProofObligation,
    claim_contract: ResearchClaimRequirement,
    binding: ResearchClaimEvidenceBinding,
    nodes_by_id: dict[str, EvidenceNode],
    edges: tuple[EvidenceEdge, ...],
) -> None:
    if binding.claim_id != claim_contract.claim_id:
        raise ValueError("research claim binding does not match Task Contract claimId")
    if binding.proposition_digest != claim_contract.proposition_digest:
        raise ValueError("research propositionDigest does not match Task Contract")

    selected_ids = (*binding.proof_evidence_ids, *binding.discovery_evidence_ids)
    for evidence_id in selected_ids:
        node = nodes_by_id.get(evidence_id)
        if node is None:
            raise ValueError(f"research evidence {evidence_id} does not exist")
        if requirement.requirement_id not in node.requirement_ids:
            raise ValueError(
                f"research evidence {evidence_id} does not name requirement "
                f"{requirement.requirement_id}"
            )
        if binding.claim_id not in node.claim_ids:
            raise ValueError(
                f"research evidence {evidence_id} does not name claim {binding.claim_id}"
            )

    proof_nodes = tuple(nodes_by_id[evidence_id] for evidence_id in binding.proof_evidence_ids)
    proof_kinds = {node.kind.value for node in proof_nodes}
    missing_kinds = set(requirement.proof.evidence_kinds).difference(proof_kinds)
    if missing_kinds:
        raise ValueError(
            f"research claim {binding.claim_id} does not satisfy evidenceKinds: "
            f"{sorted(missing_kinds)}"
        )

    all_nodes = tuple(nodes_by_id[evidence_id] for evidence_id in selected_ids)
    source_nodes = tuple(node for node in all_nodes if node.kind in _SOURCE_EVIDENCE_KINDS)
    proof_source_nodes = tuple(node for node in proof_nodes if node.kind in _SOURCE_EVIDENCE_KINDS)
    if binding.state == "satisfied" and not proof_source_nodes:
        raise ValueError(f"research claim {binding.claim_id} lacks source evidence")
    for node in source_nodes:
        query_class = _validate_source_class_matrix(node)
        if query_class not in research.query_classes:
            raise ValueError(f"research source for {binding.claim_id} is outside queryClasses")
        if node.freshness.rule != claim_contract.freshness:
            raise ValueError(f"research source for {binding.claim_id} violates claim freshness")

    primary_proof = any(
        node.research_source is not None and node.research_source.source_class == "primary"
        for node in proof_source_nodes
    )
    primary_available = any(
        node.research_source is not None and node.research_source.source_class == "primary"
        for node in source_nodes
    )
    if research.primary_source_rule == "required" and not primary_proof:
        raise ValueError(f"research claim {binding.claim_id} requires primary evidence")
    if research.primary_source_rule == "required_when_available" and not primary_proof:
        unavailable_proof = tuple(
            nodes_by_id[evidence_id]
            for evidence_id in binding.stopping_evidence_ids
            if evidence_id in nodes_by_id
            and "primary_source_unavailable" in nodes_by_id[evidence_id].reason_codes
            and nodes_by_id[evidence_id].coverage.coverage_kind in {"query_plan", "source_set"}
        )
        if primary_available or not unavailable_proof:
            raise ValueError(f"research claim {binding.claim_id} lacks primary availability proof")

    claim_ids = set(selected_ids)
    conflict_edges = tuple(
        edge
        for edge in edges
        if edge.kind == "contradicts"
        and edge.source_evidence_id in claim_ids
        and edge.target_evidence_id in claim_ids
    )
    if conflict_edges:
        allowed_dispositions: dict[str, set[str]] = {
            "resolve": {"resolved"},
            "resolve_or_disclose": {"resolved", "disclosed"},
            "disclose": {"disclosed"},
            "block": {"blocked"},
        }
        if binding.conflict_disposition not in allowed_dispositions[research.conflict_handling]:
            raise ValueError(
                f"research claim {binding.claim_id} conflictDisposition does not satisfy policy"
            )
        if binding.conflict_disposition == "resolved":
            if not binding.resolution_evidence_ids or any(
                "conflict_resolved" not in nodes_by_id[evidence_id].reason_codes
                for evidence_id in binding.resolution_evidence_ids
            ):
                raise ValueError("resolved conflicts require anchored resolution evidence")
        if binding.conflict_disposition == "disclosed":
            if not binding.disclosure_evidence_ids or any(
                "conflict_disclosed" not in nodes_by_id[evidence_id].reason_codes
                for evidence_id in binding.disclosure_evidence_ids
            ):
                raise ValueError("disclosed conflicts require anchored disclosure evidence")
        if binding.conflict_disposition == "blocked" and binding.state != "blocked":
            raise ValueError("blocked conflictDisposition requires a blocked claim state")
    elif (
        binding.conflict_disposition != "none"
        or binding.resolution_evidence_ids
        or binding.disclosure_evidence_ids
    ):
        raise ValueError("conflict-free claim must use conflictDisposition none")

    if binding.stopping_rules_satisfied != research.stopping_rules:
        raise ValueError(f"research claim {binding.claim_id} does not cover all stoppingRules")
    for evidence_id in binding.stopping_evidence_ids:
        node = nodes_by_id.get(evidence_id)
        if node is None:
            raise ValueError(f"stopping evidence {evidence_id} does not exist")
        if requirement.requirement_id not in node.requirement_ids:
            raise ValueError("stopping evidence belongs to another requirement")
        if binding.claim_id not in node.claim_ids:
            raise ValueError("stopping evidence belongs to another research claim")
    if "source_classes_exhausted" in research.stopping_rules and not any(
        "source_classes_exhausted" in nodes_by_id[evidence_id].reason_codes
        for evidence_id in binding.stopping_evidence_ids
    ):
        raise ValueError("source_classes_exhausted requires anchored stopping evidence")
    if "explicit_budget_reached" in research.stopping_rules and not any(
        "explicit_budget_reached" in nodes_by_id[evidence_id].reason_codes
        for evidence_id in binding.stopping_evidence_ids
    ):
        raise ValueError("explicit_budget_reached requires anchored stopping evidence")

    snippet_proof_ids = {
        node.evidence_id
        for node in proof_source_nodes
        if node.research_source is not None and node.research_source.source_class == "snippet"
    }
    snippet_discovery_ids = {
        node.evidence_id
        for node in source_nodes
        if node.evidence_id in binding.discovery_evidence_ids
        and node.research_source is not None
        and node.research_source.source_class == "snippet"
    }
    allowance = research.limited_snippet_allowance
    if allowance == "forbidden" and (snippet_proof_ids or snippet_discovery_ids):
        raise ValueError("forbidden snippet evidence appears in the research closure")
    if allowance == "discovery_only" and snippet_proof_ids:
        raise ValueError("discovery_only snippets cannot be used as proof evidence")
    if allowance == "explicitly_accepted_proof" and snippet_proof_ids:
        if not binding.snippet_acceptance_evidence_ids:
            raise ValueError("snippet proof requires explicit acceptance evidence")
        for evidence_id in binding.snippet_acceptance_evidence_ids:
            node = nodes_by_id[evidence_id]
            if (
                node.kind not in {EvidenceKind.ENTAILMENT_VERDICT, EvidenceKind.REQUIREMENT_VERDICT}
                or node.semantic_class is not EvidenceSemanticClass.VERDICT
                or "snippet_proof_explicitly_accepted" not in node.reason_codes
            ):
                raise ValueError("snippet acceptance must be an anchored verdict")
    elif binding.snippet_acceptance_evidence_ids:
        raise ValueError("snippet acceptance evidence is not allowed by this Task Contract")


class EvidenceClosureBinding(EnvelopeModel):
    """Task Contract plus the exact evidence graph and requirement-local proof map."""

    schema_id: Literal["magi.evidence_closure_binding.v1"] = Field(
        default="magi.evidence_closure_binding.v1",
        alias="schemaId",
    )
    task_contract: TaskContractSnapshot = Field(alias="taskContract")
    task_contract_digest: str = Field(alias="taskContractDigest")
    projection: EvidenceProjectionSnapshot
    requirements: tuple[RequirementEvidenceBinding, ...]
    closure_digest: str | None = Field(default=None, alias="closureDigest")

    @model_validator(mode="after")
    def _validate_task_local_closure(self) -> Self:
        expected_task_digest = canonical_task_contract_digest(self.task_contract)
        if self.task_contract_digest != expected_task_digest:
            raise ValueError("taskContractDigest does not match embedded Task Contract")
        projection_checks = (
            (
                "taskContractId",
                self.projection.task_contract_id,
                self.task_contract.task_contract_id,
            ),
            ("taskVersion", self.projection.task_version, self.task_contract.version),
            ("taskContractDigest", self.projection.task_contract_digest, expected_task_digest),
            (
                "completionEpochId",
                self.projection.completion_epoch_id,
                self.task_contract.completion_epoch_id,
            ),
        )
        for alias, observed, expected in projection_checks:
            if observed != expected:
                raise ValueError(f"projection {alias} does not match Task Contract")

        active_requirements = tuple(
            requirement
            for requirement in self.task_contract.requirements
            if requirement.state is not RequirementState.SUPERSEDED
        )
        expected_requirement_ids = tuple(
            requirement.requirement_id for requirement in active_requirements
        )
        observed_requirement_ids = tuple(
            requirement.requirement_id for requirement in self.requirements
        )
        if observed_requirement_ids != expected_requirement_ids:
            raise ValueError(
                "requirement bindings must exactly cover active Task Contract requirements"
            )

        claim_owners: dict[str, str] = {}
        for requirement in active_requirements:
            research = requirement.proof.research
            if research is None:
                continue
            for claim in research.claims:
                previous = claim_owners.setdefault(claim.claim_id, requirement.requirement_id)
                if previous != requirement.requirement_id:
                    raise ValueError("research claim IDs must be unique across the Task Contract")

        requirements_by_id = {
            requirement.requirement_id: requirement for requirement in active_requirements
        }
        nodes_by_id = {node.evidence_id: node for node in self.projection.nodes}
        known_requirement_ids = set(requirements_by_id)
        known_claim_ids = set(claim_owners)
        for projection_node in self.projection.nodes:
            unknown_requirements = set(projection_node.requirement_ids).difference(
                known_requirement_ids
            )
            if unknown_requirements:
                raise ValueError("evidence node names a requirement outside the Task Contract")
            unknown_claims = set(projection_node.claim_ids).difference(known_claim_ids)
            if unknown_claims:
                raise ValueError("evidence node names a research claim outside the Task Contract")
            for claim_id in projection_node.claim_ids:
                if claim_owners[claim_id] not in projection_node.requirement_ids:
                    raise ValueError("evidence node claimId is detached from its requirement")

        for requirement_binding in self.requirements:
            contract = requirements_by_id[requirement_binding.requirement_id]
            proof_nodes: list[EvidenceNode] = []
            for evidence_id in requirement_binding.proof_evidence_ids:
                proof_node = nodes_by_id.get(evidence_id)
                if proof_node is None:
                    raise ValueError(f"requirement evidence {evidence_id} does not exist")
                if contract.requirement_id not in proof_node.requirement_ids:
                    raise ValueError("requirement evidence belongs to another requirement")
                proof_nodes.append(proof_node)
            observed_kinds = {node.kind.value for node in proof_nodes}
            missing_kinds = set(contract.proof.evidence_kinds).difference(observed_kinds)
            if missing_kinds:
                raise ValueError(
                    f"requirement {contract.requirement_id} does not satisfy evidenceKinds: "
                    f"{sorted(missing_kinds)}"
                )
            if requirement_binding.state is RequirementState.SATISFIED and not any(
                node.freshness.rule == contract.proof.freshness for node in proof_nodes
            ):
                raise ValueError(f"requirement {contract.requirement_id} lacks required freshness")
            if contract.proof.required_producer is not None and any(
                node.producer_id != contract.proof.required_producer for node in proof_nodes
            ):
                raise ValueError("requirement proof uses an unauthorized producer")

            research = contract.proof.research
            if research is None:
                if requirement_binding.research_claims:
                    raise ValueError("non-research requirement cannot bind research claims")
                continue
            expected_claim_ids = tuple(claim.claim_id for claim in research.claims)
            observed_claim_ids = tuple(
                claim.claim_id for claim in requirement_binding.research_claims
            )
            if observed_claim_ids != expected_claim_ids:
                raise ValueError("research claims must exactly cover the Task Contract")
            proof_id_set = set(requirement_binding.proof_evidence_ids)
            for claim_contract, claim_binding in zip(
                research.claims,
                requirement_binding.research_claims,
                strict=True,
            ):
                if not set(claim_binding.proof_evidence_ids).issubset(proof_id_set):
                    raise ValueError("research claim evidence is outside requirement proof")
                _validate_research_claim(
                    requirement=contract,
                    research=research,
                    claim_contract=claim_contract,
                    binding=claim_binding,
                    nodes_by_id=nodes_by_id,
                    edges=self.projection.edges,
                )
            if requirement_binding.state is RequirementState.SATISFIED and any(
                claim.state != "satisfied" for claim in requirement_binding.research_claims
            ):
                raise ValueError("satisfied research requirement requires every claim satisfied")

        expected_digest = _model_digest(self, exclude=frozenset({"closure_digest"}))
        if self.closure_digest is not None and self.closure_digest != expected_digest:
            raise ValueError("closureDigest does not match the exact evidence closure")
        object.__setattr__(self, "closure_digest", expected_digest)
        return self


class CompletionActionBinding(EnvelopeModel):
    """Exact logical action, terminal attempt, and its verification evidence IDs."""

    schema_id: Literal["magi.completion_action_binding.v1"] = Field(
        default="magi.completion_action_binding.v1",
        alias="schemaId",
    )
    action_snapshot: ActionSnapshot = Field(alias="actionSnapshot")
    attempt_snapshot: AttemptSnapshot = Field(alias="attemptSnapshot")
    verification_mode: VerificationMode = Field(alias="verificationMode")
    terminal_verification_evidence_ids: tuple[str, ...] = Field(
        alias="terminalVerificationEvidenceIds"
    )
    binding_digest: str | None = Field(default=None, alias="bindingDigest")

    @field_validator("terminal_verification_evidence_ids")
    @classmethod
    def _sorted_terminal_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _require_unique_sorted_strings(
            value,
            field_name="terminalVerificationEvidenceIds",
        )

    @model_validator(mode="after")
    def _bind_action_attempt(self) -> Self:
        action = self.action_snapshot
        attempt = self.attempt_snapshot
        checks = (
            ("action", attempt.action_id, action.action_id),
            ("partition", attempt.partition_id, action.partition_id),
            ("Task Contract", attempt.task_contract_digest, action.task_contract_digest),
            ("intent", attempt.action_intent_digest, action.intent_digest),
        )
        for name, observed, expected in checks:
            if observed != expected:
                raise ValueError(f"completion action {name} binding does not match")
        resolution = action.resolution
        if resolution is None:
            raise ValueError("completion action requires a durable logical resolution")
        if attempt.attempt_id not in resolution.source_attempt_ids:
            raise ValueError("completion action resolution does not include the bound attempt")

        if self.verification_mode == "snapshot_verified":
            if attempt.state is not ActionState.VERIFIED or attempt.verification is None:
                raise ValueError("snapshot_verified requires a VERIFIED AttemptSnapshot")
            if resolution.logical_state is not ActionState.VERIFIED:
                raise ValueError("snapshot_verified requires a VERIFIED logical resolution")
            if self.terminal_verification_evidence_ids != (attempt.verification.evidence_id,):
                raise ValueError("snapshot_verified must bind the exact verification evidence ID")
        else:
            allowed_terminal_states = {
                ActionState.COMMITTED,
                ActionState.ABORTED,
                ActionState.PARTIAL,
                ActionState.UNKNOWN,
            }
            if attempt.state not in allowed_terminal_states:
                raise ValueError("terminal_evidence requires an allowed terminal attempt state")
            if not self.terminal_verification_evidence_ids:
                raise ValueError("terminal_evidence requires verification evidence")
            expected_resolution = (
                ActionState.VERIFIED if attempt.state is ActionState.COMMITTED else attempt.state
            )
            if resolution.logical_state is not expected_resolution:
                raise ValueError("terminal evidence does not match logical action resolution")

        expected_digest = _model_digest(self, exclude=frozenset({"binding_digest"}))
        if self.binding_digest is not None and self.binding_digest != expected_digest:
            raise ValueError("bindingDigest does not match CompletionActionBinding")
        object.__setattr__(self, "binding_digest", expected_digest)
        return self


class EvidenceAttribution(EnvelopeModel):
    """One response evidence ID attributed to one exact requirement/claim pair."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    evidence_id: str = Field(alias="evidenceId", min_length=1)
    requirement_id: str = Field(alias="requirementId", min_length=1)
    research_claim_id: str | None = Field(
        default=None,
        alias="researchClaimId",
        min_length=1,
    )


class ResponseClaimEvidenceBinding(EnvelopeModel):
    """Claim-local attribution for one exact response manifest segment."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    response_claim_id: str = Field(alias="responseClaimId", min_length=1)
    attributions: tuple[EvidenceAttribution, ...]

    @model_validator(mode="after")
    def _unique_evidence_ids(self) -> Self:
        evidence_ids = tuple(attribution.evidence_id for attribution in self.attributions)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("response claim evidence attributions must be unique")
        return self


class CompletionDependencyBinding(EnvelopeModel):
    """A dependency snapshot linked to the evidence node that recorded it."""

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    health: DependencyHealth
    source_evidence_id: str = Field(alias="sourceEvidenceId", min_length=1)
    binding_digest: str | None = Field(default=None, alias="bindingDigest")

    @model_validator(mode="after")
    def _derive_binding_digest(self) -> Self:
        expected = _model_digest(self, exclude=frozenset({"binding_digest"}))
        if self.binding_digest is not None and self.binding_digest != expected:
            raise ValueError("bindingDigest does not match dependency snapshot")
        object.__setattr__(self, "binding_digest", expected)
        return self


def _dependency_health_digest(health: DependencyHealth) -> str:
    return _digest_payload(health.model_dump(by_alias=True, mode="json"))


class CompletionEvaluationBinding(EnvelopeModel):
    """Replay-complete, anti-laundering input and output of completion evaluation."""

    schema_id: Literal["magi.completion_evaluation_binding.v1"] = Field(
        default="magi.completion_evaluation_binding.v1",
        alias="schemaId",
    )
    request: FinalizationRequest
    evidence_closure: EvidenceClosureBinding = Field(alias="evidenceClosure")
    action_bindings: tuple[CompletionActionBinding, ...] = Field(alias="actionBindings")
    dependency_snapshots: tuple[CompletionDependencyBinding, ...] = Field(
        alias="dependencySnapshots"
    )
    response_claim_bindings: tuple[ResponseClaimEvidenceBinding, ...] = Field(
        alias="responseClaimBindings"
    )
    verdict: CompletionVerdict
    evaluation_digest: str | None = Field(default=None, alias="evaluationDigest")

    @model_validator(mode="after")
    def _validate_complete_evaluation(self) -> Self:
        request = self.request
        closure = self.evidence_closure
        projection = closure.projection
        verdict = self.verdict

        if request.task_contract != closure.task_contract:
            raise ValueError("FinalizationRequest Task Contract does not match evidence closure")
        request_projection_checks = (
            ("taskContractDigest", request.task_contract_digest, closure.task_contract_digest),
            ("taskPartitionId", request.task_partition_id, projection.task_partition_id),
            ("completionEpochId", request.completion_epoch_id, projection.completion_epoch_id),
            ("stateRoot", request.state_root, projection.state_root),
            ("evidenceRoot", request.evidence_root, projection.evidence_root),
            (
                "barrierAdmissionSequence",
                request.barrier_admission_sequence,
                projection.barrier_admission_sequence,
            ),
        )
        for alias, observed, expected in request_projection_checks:
            if observed != expected:
                raise ValueError(f"FinalizationRequest {alias} does not match evidence closure")

        observed_health = tuple(binding.health for binding in self.dependency_snapshots)
        if observed_health != request.dependency_health:
            raise ValueError("dependencySnapshots must exactly equal FinalizationRequest health")

        nodes_by_id = {node.evidence_id: node for node in projection.nodes}
        for dependency_binding in self.dependency_snapshots:
            dependency_node = nodes_by_id.get(dependency_binding.source_evidence_id)
            if dependency_node is None:
                raise ValueError("dependency snapshot source evidence does not exist")
            if dependency_node.kind is not EvidenceKind.DEPENDENCY_HEALTH:
                raise ValueError("dependency snapshot requires dependency_health evidence")
            if dependency_node.content_digest != _dependency_health_digest(
                dependency_binding.health
            ):
                raise ValueError("dependency health evidence contentDigest does not match snapshot")
            if (
                dependency_node.task_contract_digest != request.task_contract_digest
                or dependency_node.completion_epoch_id != request.completion_epoch_id
                or dependency_node.state_root != request.state_root
            ):
                raise ValueError("dependency snapshot evidence uses stale task coordinates")

        request_verdict_checks = (
            ("finalizationId", verdict.finalization_id, request.finalization_id),
            (
                "finalizationRequestDigest",
                verdict.finalization_request_digest,
                request.finalization_request_digest,
            ),
            (
                "responseClaimManifestDigest",
                verdict.response_claim_manifest_digest,
                request.response_claim_manifest_digest,
            ),
            ("taskContractId", verdict.task_contract_id, request.task_contract.task_contract_id),
            ("taskVersion", verdict.task_version, request.task_contract.version),
            ("taskContractDigest", verdict.task_contract_digest, request.task_contract_digest),
            (
                "taskContractSnapshotRef",
                verdict.task_contract_snapshot_ref,
                request.task_contract_snapshot_ref,
            ),
            ("taskPartitionId", verdict.task_partition_id, request.task_partition_id),
            ("completionEpochId", verdict.completion_epoch_id, request.completion_epoch_id),
            ("stateRoot", verdict.state_root, request.state_root),
            ("evidenceRoot", verdict.evidence_root, request.evidence_root),
            (
                "barrierAdmissionSequence",
                verdict.barrier_admission_sequence,
                request.barrier_admission_sequence,
            ),
            (
                "responseDigest",
                verdict.response_digest,
                request.claim_manifest.candidate_response_digest,
            ),
        )
        for alias, observed, expected in request_verdict_checks:
            if observed != expected:
                raise ValueError(f"CompletionVerdict {alias} does not match request")

        requirement_bindings = {binding.requirement_id: binding for binding in closure.requirements}
        observed_result_ids = tuple(result.requirement_id for result in verdict.requirements)
        if observed_result_ids != tuple(requirement_bindings):
            raise ValueError("CompletionVerdict requirements do not match evidence closure")
        claim_bindings: dict[tuple[str, str], ResearchClaimEvidenceBinding] = {}
        for requirement_binding in closure.requirements:
            for claim_binding in requirement_binding.research_claims:
                claim_bindings[(requirement_binding.requirement_id, claim_binding.claim_id)] = (
                    claim_binding
                )

        for result in verdict.requirements:
            requirement_binding = requirement_bindings[result.requirement_id]
            if result.state is not requirement_binding.state:
                raise ValueError("CompletionVerdict requirement state does not match closure")
            if result.evidence_ids != requirement_binding.proof_evidence_ids:
                raise ValueError("CompletionVerdict requirement evidence does not match closure")
            expected_claims = requirement_binding.research_claims
            if tuple(claim.claim_id for claim in result.research_claims) != tuple(
                claim.claim_id for claim in expected_claims
            ):
                raise ValueError("CompletionVerdict research claims do not match closure")
            for claim_result, claim_binding in zip(
                result.research_claims,
                expected_claims,
                strict=True,
            ):
                if (
                    claim_result.proposition_digest != claim_binding.proposition_digest
                    or claim_result.state != claim_binding.state
                    or claim_result.evidence_ids != claim_binding.proof_evidence_ids
                ):
                    raise ValueError(
                        f"CompletionVerdict {claim_binding.claim_id} evidence does not match closure"
                    )

        manifest_claims = request.claim_manifest.segments
        if tuple(binding.response_claim_id for binding in self.response_claim_bindings) != tuple(
            claim.claim_id for claim in manifest_claims
        ):
            raise ValueError("responseClaimBindings must exactly cover the response manifest")
        for segment, response_binding in zip(
            manifest_claims,
            self.response_claim_bindings,
            strict=True,
        ):
            attribution_ids = tuple(
                attribution.evidence_id for attribution in response_binding.attributions
            )
            if attribution_ids != segment.evidence_ids:
                raise ValueError("response claim attribution does not match segment evidenceIds")
            if segment.claim_class == "limitation":
                continue
            for attribution in response_binding.attributions:
                attributed_requirement = requirement_bindings.get(attribution.requirement_id)
                if attributed_requirement is None:
                    raise ValueError("response evidence attribution names an unknown requirement")
                if attributed_requirement.state is not RequirementState.SATISFIED:
                    raise ValueError(
                        "response evidence attribution uses an unsatisfied requirement"
                    )
                if attribution.evidence_id not in attributed_requirement.proof_evidence_ids:
                    raise ValueError("response evidence is outside attributed requirement proof")
                attributed_node = nodes_by_id.get(attribution.evidence_id)
                if attributed_node is None:
                    raise ValueError("response evidence does not exist in evidence closure")
                if attribution.requirement_id not in attributed_node.requirement_ids:
                    raise ValueError("response evidence node belongs to another requirement")
                if attribution.research_claim_id is None:
                    if attributed_node.claim_ids:
                        raise ValueError(
                            "research evidence requires claim-local response attribution"
                        )
                else:
                    attributed_claim = claim_bindings.get(
                        (attribution.requirement_id, attribution.research_claim_id)
                    )
                    if attributed_claim is None:
                        raise ValueError(
                            f"response attribution names unknown claim "
                            f"{attribution.research_claim_id}"
                        )
                    if attribution.evidence_id not in attributed_claim.proof_evidence_ids:
                        raise ValueError(
                            f"response attribution launders evidence across "
                            f"{attribution.research_claim_id}"
                        )
                    if attribution.research_claim_id not in attributed_node.claim_ids:
                        raise ValueError(
                            f"evidence node does not name claim {attribution.research_claim_id}"
                        )

        action_ids = tuple(binding.action_snapshot.action_id for binding in self.action_bindings)
        if action_ids != tuple(sorted(action_ids)) or len(action_ids) != len(set(action_ids)):
            raise ValueError("actionBindings must be unique and sorted by actionId")
        if action_ids != verdict.included_action_ids:
            raise ValueError("actionBindings must exactly cover includedActionIds")
        for action_binding in self.action_bindings:
            action = action_binding.action_snapshot
            attempt = action_binding.attempt_snapshot
            if (
                action.task_contract_digest != request.task_contract_digest
                or action.completion_epoch_id != request.completion_epoch_id
            ):
                raise ValueError("completion action uses another Task Contract or epoch")
            if action.admission_sequence > request.barrier_admission_sequence:
                raise ValueError("completion action was admitted after the finalization barrier")
            verification_nodes: list[EvidenceNode] = []
            for evidence_id in action_binding.terminal_verification_evidence_ids:
                verification_node = nodes_by_id.get(evidence_id)
                if verification_node is None:
                    raise ValueError("completion action verification evidence does not exist")
                if (
                    verification_node.action_id != action.action_id
                    or verification_node.attempt_id != attempt.attempt_id
                ):
                    raise ValueError("completion action verification evidence has wrong identity")
                if verification_node.state_root != request.state_root:
                    raise ValueError("completion action verification evidence uses stale stateRoot")
                if verification_node.admission_sequence > request.barrier_admission_sequence:
                    raise ValueError("completion action evidence is beyond the barrier")
                verification_nodes.append(verification_node)
            if action_binding.verification_mode == "snapshot_verified":
                assert attempt.verification is not None
                node = verification_nodes[0]
                if attempt.verification.evidence_digest != canonical_evidence_node_digest(node):
                    raise ValueError("AttemptSnapshot verification digest does not match evidence")
                if attempt.verification.verified_state_root != request.state_root:
                    raise ValueError("AttemptSnapshot verification uses stale stateRoot")
            elif (
                action.resolution is not None
                and action.resolution.logical_state is ActionState.VERIFIED
            ):
                if any(
                    node.kind
                    not in {
                        EvidenceKind.POSTCONDITION_VERDICT,
                        EvidenceKind.WORKSPACE_POSTCONDITION,
                        EvidenceKind.REQUIREMENT_VERDICT,
                    }
                    for node in verification_nodes
                ):
                    raise ValueError("successful terminal action lacks postcondition evidence")
            elif any(node.kind is not EvidenceKind.ACTION_RECEIPT for node in verification_nodes):
                raise ValueError("non-success terminal action requires action receipt evidence")

        if verdict.status is CompletionStatus.COMPLETE:
            if any(
                binding.action_snapshot.resolution is None
                or binding.action_snapshot.resolution.logical_state is not ActionState.VERIFIED
                for binding in self.action_bindings
            ):
                raise ValueError("complete verdict cannot include an unverified action")
            if any(
                binding.state is not RequirementState.SATISFIED for binding in closure.requirements
            ):
                raise ValueError("complete verdict requires every closure requirement satisfied")

        expected_digest = _model_digest(self, exclude=frozenset({"evaluation_digest"}))
        if self.evaluation_digest is not None and self.evaluation_digest != expected_digest:
            raise ValueError("evaluationDigest does not match CompletionEvaluationBinding")
        object.__setattr__(self, "evaluation_digest", expected_digest)
        return self


__all__ = [
    "CompletionActionBinding",
    "CompletionDependencyBinding",
    "CompletionEvaluationBinding",
    "EvidenceAttribution",
    "EvidenceClosureBinding",
    "EvidenceJournalAnchor",
    "EvidenceProjectionSnapshot",
    "RequirementEvidenceBinding",
    "ResearchClaimEvidenceBinding",
    "ResponseClaimEvidenceBinding",
    "canonical_evidence_projection_root",
]
