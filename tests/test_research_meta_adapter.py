from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.evidence.child_runtime_envelope import ChildRuntimeEnvelope
from runtime_issuance_support import issue_test_runtime_authority
from openmagi_core_agent.evidence.subagent import OPENMAGI_RUNTIME_ENVELOPE_ISSUER
from openmagi_core_agent.meta_orchestration.child_acceptance import issue_runtime_child_result
from openmagi_core_agent.meta_orchestration.child_roles import MetaChildRoleRegistry
from openmagi_core_agent.research.meta_adapter import (
    RESEARCH_META_ROLE_NAMES,
    ResearchMetaEvidencePolicy,
    ResearchMetaHarnessPlan,
    accept_research_child_result,
    build_research_meta_harness_plan,
)


def _runtime_authority(*scopes: str):
    return issue_test_runtime_authority(
        authority_id="authority:test-research-meta-adapter",
        scopes=scopes,
    )


def _policy(**updates: object) -> ResearchMetaEvidencePolicy:
    payload: dict[str, object] = {
        "parentExecutionId": "parent:research",
        "childExecutionId": "child:research-searcher",
        "taskId": "task:research-searcher",
        "parentPolicySnapshotId": "policy:research",
        "childPolicySnapshotId": "policy:research",
        "runtimeReceiptRef": "receipt:research-child",
        "sourceEvidenceRefs": ("audit:source-proof",),
        "claimEvidenceRefs": ("audit:claim-map",),
        "taskEvidenceRefs": ("ledger:research-searcher",),
        "maxRetryBudget": 1,
        "currentAttempt": 0,
    }
    payload.update(updates)
    return ResearchMetaEvidencePolicy.model_validate(payload)


def _child_envelope(
    *,
    status: str = "accepted",
    ledger_id: str = "ledger:research-searcher",
    audit_event_refs: tuple[str, ...] = ("audit:source-proof", "audit:claim-map"),
) -> ChildRuntimeEnvelope:
    mode = "blocked" if status == "blocked" else "return"
    return ChildRuntimeEnvelope.issue_runtime_envelope(
        runtime_authority=_runtime_authority("child_runtime_envelope"),
        **{
            "issuer": OPENMAGI_RUNTIME_ENVELOPE_ISSUER,
            "mode": mode,
            "status": status,
            "parentBoundary": {
                "executionId": "parent:research",
                "agentId": "agent:parent",
                "turnId": "turn:research",
                "policyScope": "research",
                "policySnapshotId": "policy:research",
                "agentRole": "research",
                "runOn": "main",
                "spawnDepth": 0,
            },
            "childBoundary": {
                "executionId": "child:research-searcher",
                "agentId": "agent:child",
                "parentExecutionId": "parent:research",
                "taskId": "task:research-searcher",
                "turnId": "turn:research",
                "policyScope": "research",
                "policySnapshotId": "policy:research",
                "agentRole": "research",
                "runOn": "child",
                "spawnDepth": 1,
            },
            "task": {
                "taskId": "task:research-searcher",
                "persona": "persona:research-child",
                "role": "research",
                "spawnDepth": 1,
                "deliver": "return",
                "promptRef": "prompt:research-child",
            },
            "policySnapshot": {
                "parentPolicySnapshotId": "policy:research",
                "childPolicySnapshotId": "policy:research",
                "allowedToolNames": (),
                "permissionRefs": (),
                "callbackHookRefs": (),
            },
            "ledgerRef": {
                "ledgerId": ledger_id,
                "executionId": "child:research-searcher",
                "agentId": "agent:child",
                "parentExecutionId": "parent:research",
                "taskId": "task:research-searcher",
                "policySnapshotId": "policy:research",
            },
            "delegatedEvidenceRequirements": (),
            "workspaceIsolation": {
                "workspacePolicy": "isolated",
                "isolationRef": "isolation:research",
                "parentWorkspaceRef": "workspace:parent",
                "childWorkspaceRef": "workspace:child",
                "descriptiveOnly": True,
            },
            "completionContract": {
                "requiredEvidence": "tool_call",
                "requiredFiles": (),
                "requireNonEmptyResult": True,
                "summaryIsEvidence": False,
                "acceptedEvidenceMetadataOnly": True,
            },
            "auditEventRefs": audit_event_refs,
            "adkPrimitiveOwnership": {
                "agentOwner": "adk_future_agent",
                "runnerOwner": "adk_future_runner",
                "eventOwner": "adk_event_bridge",
                "toolOwner": "adk_function_tool_future",
                "callbackOwner": "adk_callbacks_future",
                "allowedToolNames": (),
                "callbackHookRefs": (),
            },
            "authorityFlags": {},
        },
    )


def test_research_adapter_composes_roles_child_specs_and_default_off_plan() -> None:
    plan = build_research_meta_harness_plan(
        plan_id="plan:research-meta",
        parent_execution_id="parent:research",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Compare public product claims using accepted research evidence.",
        evidence_policy=_policy(),
    )
    registry = MetaChildRoleRegistry(plan.role_definitions)

    assert tuple(plan.role_names) == RESEARCH_META_ROLE_NAMES
    assert set(registry.role_refs()) == {child.role_ref for child in plan.task_plan.child_task_specs}
    assert len(plan.task_plan.child_task_specs) == 5
    assert plan.default_off is True
    assert plan.task_plan.default_off is True
    assert set(plan.task_plan.authority_flags.model_dump(by_alias=True).values()) == {False}
    assert all(child.requires_evidence_envelope for child in plan.task_plan.child_task_specs)
    assert all(child.allowed_tool_refs == registry.allowed_tool_refs_for(child.role_ref) for child in plan.task_plan.child_task_specs)
    assert "verifier:research-evidence-policy" in plan.task_plan.verifier_chain_refs

    projection = plan.public_projection()
    assert projection["roleCount"] == 5
    assert projection["childTaskCount"] == 5
    assert projection["sourceEvidenceRefCount"] == 1
    assert projection["claimEvidenceRefCount"] == 1
    assert projection["taskEvidenceRefCount"] == 1
    assert projection["defaultOff"] is True
    assert "allowedToolRefs" not in projection


def test_research_harness_rejects_policy_for_task_outside_composed_children() -> None:
    with pytest.raises(ValidationError):
        build_research_meta_harness_plan(
            plan_id="plan:research-meta",
            parent_execution_id="parent:research",
            objective_digest="sha256:" + "a" * 64,
            objective_preview="Compare public product claims using accepted research evidence.",
            evidence_policy=_policy(taskId="task:outside-research-plan"),
        )


def test_research_harness_rejects_arbitrary_role_definitions() -> None:
    plan = build_research_meta_harness_plan(
        plan_id="plan:research-meta",
        parent_execution_id="parent:research",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Compare public product claims using accepted research evidence.",
        evidence_policy=_policy(),
    )
    forged_roles = (
        plan.role_definitions[0].model_copy(
            update={"completionContractRef": "contract:forged-research-role"},
        ),
        *plan.role_definitions[1:],
    )

    with pytest.raises(ValidationError):
        ResearchMetaHarnessPlan.model_validate(
            {
                **plan.model_dump(by_alias=True, mode="python", warnings=False),
                "roleDefinitions": forged_roles,
            }
        )


def test_research_harness_rejects_extra_smuggled_child_tasks() -> None:
    plan = build_research_meta_harness_plan(
        plan_id="plan:research-meta",
        parent_execution_id="parent:research",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Compare public product claims using accepted research evidence.",
        evidence_policy=_policy(),
    )
    smuggled_child = plan.task_plan.child_task_specs[0].model_copy(
        update={"taskId": "task:smuggled-research-searcher"},
    )
    forged_task_plan = plan.task_plan.model_copy(
        update={"childTaskSpecs": (*plan.task_plan.child_task_specs, smuggled_child)},
    )

    with pytest.raises(ValidationError):
        ResearchMetaHarnessPlan.model_validate(
            {
                **plan.model_dump(by_alias=True, mode="python", warnings=False),
                "taskPlan": forged_task_plan,
                "evidencePolicy": _policy(taskId="task:smuggled-research-searcher"),
            }
        )


@pytest.mark.parametrize(
    "child_update",
    (
        {"scopeRef": "scope:smuggled"},
        {"deliveryMode": "background"},
        {
            "contextBudget": {
                "maxInputTokens": 9999,
                "maxOutputTokens": 9999,
                "reservedEvidenceTokens": 0,
            }
        },
    ),
)
def test_research_harness_rejects_mutated_first_party_child_specs(
    child_update: dict[str, object],
) -> None:
    plan = build_research_meta_harness_plan(
        plan_id="plan:research-meta",
        parent_execution_id="parent:research",
        objective_digest="sha256:" + "a" * 64,
        objective_preview="Compare public product claims using accepted research evidence.",
        evidence_policy=_policy(),
    )
    forged_child = plan.task_plan.child_task_specs[0].model_copy(update=child_update)
    forged_task_plan = plan.task_plan.model_copy(
        update={"childTaskSpecs": (forged_child, *plan.task_plan.child_task_specs[1:])},
    )

    with pytest.raises(ValidationError):
        ResearchMetaHarnessPlan.model_validate(
            {
                **plan.model_dump(by_alias=True, mode="python", warnings=False),
                "taskPlan": forged_task_plan,
            }
        )


def test_research_evidence_policy_requires_source_claim_and_task_refs() -> None:
    for field_name in ("sourceEvidenceRefs", "claimEvidenceRefs", "taskEvidenceRefs"):
        with pytest.raises(ValidationError):
            _policy(**{field_name: ()})

    with pytest.raises(ValidationError):
        _policy(sourceEvidenceRefs=("https://example.com/source",))
    with pytest.raises(ValidationError):
        _policy(sourceEvidenceRefs=("url:example.com",))
    with pytest.raises(ValidationError):
        _policy(sourceEvidenceRefs=("citation:example-only",))
    with pytest.raises(ValidationError):
        _policy(sourceEvidenceRefs=("example.com",))
    with pytest.raises(ValidationError):
        _policy(claimEvidenceRefs=("summary:child",))
    with pytest.raises(ValidationError):
        _policy(taskEvidenceRefs=("raw-source:text",))
    with pytest.raises(ValidationError):
        _policy(sourceEvidenceRefs=("source:example.com",))
    with pytest.raises(ValidationError):
        _policy(claimEvidenceRefs=("claim:summary-child",))
    with pytest.raises(ValidationError):
        _policy(taskEvidenceRefs=("ledger:raw-source",))


def test_research_child_acceptance_uses_policy_for_accept_retry_and_reject() -> None:
    accepted = accept_research_child_result(
        issue_runtime_child_result(_child_envelope(), receipt_ref="receipt:research-child"),
        _policy(),
    )
    retry = accept_research_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("audit:source-proof",)),
            receipt_ref="receipt:research-child",
        ),
        _policy(),
    )
    rejected = accept_research_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("audit:source-proof",)),
            receipt_ref="receipt:research-child",
        ),
        _policy(currentAttempt=1),
    )

    assert accepted.status == "accepted"
    assert accepted.accepted_evidence_refs == (
        "audit:source-proof",
        "audit:claim-map",
        "ledger:research-searcher",
    )
    assert retry.status == "retry"
    assert retry.missing_evidence_refs == ("audit:claim-map",)
    assert retry.retry_budget_remaining == 1
    assert rejected.status == "rejected"
    assert rejected.reason_codes == ("missing_required_evidence", "retry_budget_exhausted")
    assert rejected.missing_evidence_refs == ("audit:claim-map",)


def test_url_only_citation_in_child_audit_refs_cannot_satisfy_research_evidence() -> None:
    verdict = accept_research_child_result(
        issue_runtime_child_result(
            _child_envelope(audit_event_refs=("url:example.com",)),
            receipt_ref="receipt:research-child",
        ),
        _policy(),
    )

    assert verdict.status == "retry"
    assert verdict.accepted_evidence_refs == ("ledger:research-searcher",)
    assert verdict.missing_evidence_refs == ("audit:source-proof", "audit:claim-map")


def test_generic_meta_modules_do_not_import_research_adapter() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    meta_dir = repo_root / "openmagi_core_agent" / "meta_orchestration"

    for path in meta_dir.glob("*.py"):
        source = path.read_text()
        assert "openmagi_core_agent.research.meta_adapter" not in source, path
        assert "from openmagi_core_agent.research import meta_adapter" not in source, path
        assert "research_searcher" not in source, path
        assert "source_inspector" not in source, path
        assert "claim_mapper" not in source, path
