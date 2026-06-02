from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.workflows.compiler import (
    WorkflowCompileInput,
    compile_governed_workflow,
    validate_compiled_workflow,
)
from openmagi_core_agent.workflows.dry_run import dry_run_governed_workflow
from openmagi_core_agent.workflows.registry import (
    WorkflowRegistryEntry,
    WorkflowStatus,
    build_workflow_registry,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "programmable_determinism"


def test_workflow_registry_entry_requires_version_owner_digest_and_contract_version() -> None:
    entry = WorkflowRegistryEntry(
        workflowId="openmagi.research.cited-market-brief",
        version="1.0.0",
        ownerRef="team-digest:research",
        status="staging",
        sourceDigest="sha256:" + "1" * 64,
        promotionHistory=("draft:2026-05-22", "staging:2026-05-22"),
        compatibleRuntimeContractVersion="programmable-determinism.v1",
    )

    assert entry.workflow_id == "openmagi.research.cited-market-brief"
    assert entry.status == "staging"
    assert entry.source_digest == "sha256:" + "1" * 64


def test_registry_rejects_duplicate_workflow_version() -> None:
    entry = WorkflowRegistryEntry(
        workflowId="openmagi.research.cited-market-brief",
        version="1.0.0",
        ownerRef="team-digest:research",
        status="draft",
        sourceDigest="sha256:" + "2" * 64,
        promotionHistory=("draft:2026-05-22",),
        compatibleRuntimeContractVersion="programmable-determinism.v1",
    )

    with pytest.raises(ValueError, match="duplicate workflow version"):
        build_workflow_registry((entry, entry))


def test_workflow_status_values_are_closed() -> None:
    assert set(WorkflowStatus.__args__) == {
        "draft",
        "staging",
        "active",
        "deprecated",
        "disabled",
    }


def test_registry_rejects_invalid_source_digest() -> None:
    with pytest.raises(ValidationError, match="sourceDigest"):
        WorkflowRegistryEntry(
            workflowId="openmagi.bad",
            version="1.0.0",
            ownerRef="team-digest:bad",
            status="draft",
            sourceDigest="raw-config",
            promotionHistory=("draft:2026-05-22",),
            compatibleRuntimeContractVersion="programmable-determinism.v1",
        )


def test_registry_rejects_scalar_promotion_history() -> None:
    with pytest.raises(ValidationError, match="promotionHistory"):
        WorkflowRegistryEntry(
            workflowId="openmagi.bad",
            version="1.0.0",
            ownerRef="team-digest:bad",
            status="draft",
            sourceDigest="sha256:" + "2" * 64,
            promotionHistory="draft:2026-05-22",
            compatibleRuntimeContractVersion="programmable-determinism.v1",
        )


def _compile_input(**overrides: object) -> WorkflowCompileInput:
    payload: dict[str, object] = {
        "workflowId": "openmagi.research.cited-market-brief",
        "version": "1.0.0",
        "selectedRecipes": ("openmagi.research.cited-market-brief.v1.0.0",),
        "registeredWorkflows": (
            {
                "workflowId": "openmagi.research.cited-market-brief",
                "version": "1.0.0",
                "ownerRef": "team-digest:research",
                "status": "staging",
                "sourceDigest": "sha256:" + "4" * 64,
                "promotionHistory": ("draft:2026-05-22", "staging:2026-05-22"),
                "compatibleRuntimeContractVersion": "programmable-determinism.v1",
            },
        ),
        "toolAllowlist": ("SourceOpen", "CitationVerify"),
        "toolDenylist": ("Bash", "FileWrite"),
        "evidenceRequirements": ("openedSourceSnapshot", "spanRef", "quoteDigest"),
        "validatorRefs": ("validator:sourceOpened@1", "validator:quoteExactMatch@1"),
        "projectionPolicy": "structured_claims_only",
        "repairPolicy": "bounded_terminal",
        "approvalPolicy": "readonly_no_external_write",
        "contextProjectionPolicy": "explicit",
        "budgets": {"maxIterations": 3, "wallClockTimeoutMs": 30000},
        "hardInvariants": {
            "rawDraftStreamingForbidden": True,
            "toolhostOnlyExecution": True,
            "validatorBeforeProjection": True,
        },
        "effectivePolicySnapshotDigest": "sha256:" + "3" * 64,
        "availableTools": ("SourceOpen", "CitationVerify"),
        "availableValidators": ("validator:sourceOpened@1", "validator:quoteExactMatch@1"),
        "availableRenderers": ("structured_claims_only",),
        "evidenceProducers": ("openedSourceSnapshot", "spanRef", "quoteDigest"),
        "routePrecedence": ("research", "general"),
        "noMatchTerminalState": "ask_user",
    }
    payload.update(overrides)
    return WorkflowCompileInput.model_validate(payload)


def test_compile_governed_workflow_produces_effective_contract() -> None:
    compiled = compile_governed_workflow(_compile_input())

    assert compiled.workflow_id == "openmagi.research.cited-market-brief"
    assert compiled.registered_workflows[0].workflow_id == "openmagi.research.cited-market-brief"
    assert compiled.context_projection_policy == "explicit"
    assert compiled.output_projection_mode == "structured_claims_only"
    assert compiled.effective_policy_snapshot_digest == "sha256:" + "3" * 64
    assert compiled.hard_invariants["rawDraftStreamingForbidden"] is True
    assert compiled.traffic_attached is False
    assert compiled.execution_attached is False


def test_validation_fails_before_execution_for_unknown_tool_validator_or_renderer() -> None:
    bad_tool = compile_governed_workflow(_compile_input(toolAllowlist=("MissingTool",)))
    bad_validator = compile_governed_workflow(_compile_input(validatorRefs=("validator:missing@1",)))
    bad_renderer = compile_governed_workflow(_compile_input(projectionPolicy="raw_text_allowed"))

    assert validate_compiled_workflow(bad_tool).ok is False
    assert "unknown_tool_ref" in validate_compiled_workflow(bad_tool).reason_codes
    assert validate_compiled_workflow(bad_validator).ok is False
    assert "unknown_validator_ref" in validate_compiled_workflow(bad_validator).reason_codes
    assert validate_compiled_workflow(bad_renderer).ok is False
    assert "governed_raw_text_projection_forbidden" in validate_compiled_workflow(bad_renderer).reason_codes


def test_validation_fails_for_conflicts_missing_terminal_or_missing_limits() -> None:
    allow_deny_conflict = compile_governed_workflow(
        _compile_input(toolAllowlist=("SourceOpen",), toolDenylist=("SourceOpen",))
    )
    no_terminal = compile_governed_workflow(_compile_input(noMatchTerminalState=None))
    no_wall_clock = compile_governed_workflow(_compile_input(budgets={"maxIterations": 3}))

    assert "allow_deny_conflict" in validate_compiled_workflow(allow_deny_conflict).reason_codes
    assert "no_match_terminal_state_missing" in validate_compiled_workflow(no_terminal).reason_codes
    assert "wall_clock_timeout_missing" in validate_compiled_workflow(no_wall_clock).reason_codes


def test_validation_fails_when_required_evidence_has_no_producer() -> None:
    compiled = compile_governed_workflow(
        _compile_input(
            evidenceRequirements=("openedSourceSnapshot", "spreadsheetCalculationReceipt"),
            evidenceProducers=("openedSourceSnapshot",),
        )
    )

    verdict = validate_compiled_workflow(compiled)
    assert verdict.ok is False
    assert "required_evidence_has_no_producer" in verdict.reason_codes


def test_validation_fails_when_selected_workflow_is_not_registered_or_runnable() -> None:
    missing = compile_governed_workflow(_compile_input(selectedRecipes=("openmagi.research.missing.v1",)))
    disabled = compile_governed_workflow(
        _compile_input(
            registeredWorkflows=(
                {
                    "workflowId": "openmagi.research.cited-market-brief",
                    "version": "1.0.0",
                    "ownerRef": "team-digest:research",
                    "status": "disabled",
                    "sourceDigest": "sha256:" + "4" * 64,
                    "promotionHistory": ("draft:2026-05-22", "disabled:2026-05-22"),
                    "compatibleRuntimeContractVersion": "programmable-determinism.v1",
                },
            )
        )
    )
    draft = compile_governed_workflow(
        _compile_input(
            registeredWorkflows=(
                {
                    "workflowId": "openmagi.research.cited-market-brief",
                    "version": "1.0.0",
                    "ownerRef": "team-digest:research",
                    "status": "draft",
                    "sourceDigest": "sha256:" + "4" * 64,
                    "promotionHistory": ("draft:2026-05-22",),
                    "compatibleRuntimeContractVersion": "programmable-determinism.v1",
                },
            )
        )
    )
    loose_prefix = compile_governed_workflow(
        _compile_input(selectedRecipes=("openmagi.research.cited-market-brief.extra-unregistered",))
    )
    version_mismatch = compile_governed_workflow(
        _compile_input(
            selectedRecipes=("openmagi.research.cited-market-brief.v1.0.0",),
            registeredWorkflows=(
                {
                    "workflowId": "openmagi.research.cited-market-brief",
                    "version": "1.1.0",
                    "ownerRef": "team-digest:research",
                    "status": "staging",
                    "sourceDigest": "sha256:" + "4" * 64,
                    "promotionHistory": ("draft:2026-05-22", "staging:2026-05-22"),
                    "compatibleRuntimeContractVersion": "programmable-determinism.v1",
                },
            ),
        )
    )
    disabled_masked_by_newer = compile_governed_workflow(
        _compile_input(
            selectedRecipes=("openmagi.research.cited-market-brief.v1.0.0",),
            registeredWorkflows=(
                {
                    "workflowId": "openmagi.research.cited-market-brief",
                    "version": "1.0.0",
                    "ownerRef": "team-digest:research",
                    "status": "disabled",
                    "sourceDigest": "sha256:" + "4" * 64,
                    "promotionHistory": ("draft:2026-05-22", "disabled:2026-05-22"),
                    "compatibleRuntimeContractVersion": "programmable-determinism.v1",
                },
                {
                    "workflowId": "openmagi.research.cited-market-brief",
                    "version": "1.1.0",
                    "ownerRef": "team-digest:research",
                    "status": "staging",
                    "sourceDigest": "sha256:" + "5" * 64,
                    "promotionHistory": ("draft:2026-05-22", "staging:2026-05-22"),
                    "compatibleRuntimeContractVersion": "programmable-determinism.v1",
                },
            ),
        )
    )
    duplicate_exact_version = compile_governed_workflow(
        _compile_input(
            selectedRecipes=("openmagi.research.cited-market-brief.v1.0.0",),
            registeredWorkflows=(
                {
                    "workflowId": "openmagi.research.cited-market-brief",
                    "version": "1.0.0",
                    "ownerRef": "team-digest:research",
                    "status": "disabled",
                    "sourceDigest": "sha256:" + "4" * 64,
                    "promotionHistory": ("draft:2026-05-22", "disabled:2026-05-22"),
                    "compatibleRuntimeContractVersion": "programmable-determinism.v1",
                },
                {
                    "workflowId": "openmagi.research.cited-market-brief",
                    "version": "1.0.0",
                    "ownerRef": "team-digest:research",
                    "status": "active",
                    "sourceDigest": "sha256:" + "5" * 64,
                    "promotionHistory": ("draft:2026-05-22", "active:2026-05-22"),
                    "compatibleRuntimeContractVersion": "programmable-determinism.v1",
                },
            ),
        )
    )

    assert "selected_workflow_not_registered" in validate_compiled_workflow(missing).reason_codes
    assert "selected_workflow_not_runnable" in validate_compiled_workflow(disabled).reason_codes
    assert "selected_workflow_not_runnable" in validate_compiled_workflow(draft).reason_codes
    assert "selected_workflow_not_registered" in validate_compiled_workflow(loose_prefix).reason_codes
    assert "selected_workflow_not_registered" in validate_compiled_workflow(version_mismatch).reason_codes
    assert "selected_workflow_not_runnable" in validate_compiled_workflow(disabled_masked_by_newer).reason_codes
    assert "duplicate_registered_workflow_version" in validate_compiled_workflow(duplicate_exact_version).reason_codes


def test_validation_fails_for_hard_denied_tools_even_if_declared_available() -> None:
    compiled = compile_governed_workflow(
        _compile_input(toolAllowlist=("Bash", "FileWrite"), availableTools=("Bash", "FileWrite"))
    )

    verdict = validate_compiled_workflow(compiled)
    assert verdict.ok is False
    assert "hard_denied_tool_allowlisted" in verdict.reason_codes


def test_validation_fails_when_hard_invariants_are_missing_or_false() -> None:
    missing = compile_governed_workflow(_compile_input(hardInvariants={"rawDraftStreamingForbidden": True}))
    weakened = compile_governed_workflow(
        _compile_input(
            hardInvariants={
                "rawDraftStreamingForbidden": True,
                "toolhostOnlyExecution": False,
                "validatorBeforeProjection": True,
            }
        )
    )
    extra_weakened = compile_governed_workflow(
        _compile_input(
            hardInvariants={
                "rawDraftStreamingForbidden": True,
                "toolhostOnlyExecution": True,
                "validatorBeforeProjection": True,
                "customerCanStreamRawDrafts": False,
            }
        )
    )

    assert "hard_invariant_missing" in validate_compiled_workflow(missing).reason_codes
    assert "hard_invariant_weakened" in validate_compiled_workflow(weakened).reason_codes
    assert "hard_invariant_weakened" in validate_compiled_workflow(extra_weakened).reason_codes


def test_validation_fails_for_unsafe_budget_values() -> None:
    negative_timeout = compile_governed_workflow(
        _compile_input(budgets={"maxIterations": 3, "wallClockTimeoutMs": -1})
    )
    non_integer_loop = compile_governed_workflow(
        _compile_input(budgets={"maxIterations": "unbounded", "wallClockTimeoutMs": 30000})
    )
    excessive_loop = compile_governed_workflow(
        _compile_input(budgets={"maxIterations": 9999, "wallClockTimeoutMs": 30000})
    )
    unknown_budget = compile_governed_workflow(
        _compile_input(budgets={"maxIterations": 3, "wallClockTimeoutMs": 30000, "extraBudget": 1})
    )

    assert "wall_clock_timeout_invalid" in validate_compiled_workflow(negative_timeout).reason_codes
    assert "loop_limit_invalid" in validate_compiled_workflow(non_integer_loop).reason_codes
    assert "loop_limit_invalid" in validate_compiled_workflow(excessive_loop).reason_codes
    assert "unknown_budget_key" in validate_compiled_workflow(unknown_budget).reason_codes


def test_compile_input_rejects_scalar_string_tuple_fields() -> None:
    with pytest.raises(ValidationError, match="selectedRecipes"):
        _compile_input(selectedRecipes="openmagi.research.cited-market-brief.v1")


def test_compile_input_rejects_protected_fragments_in_identifiers() -> None:
    with pytest.raises(ValidationError, match="protected"):
        _compile_input(selectedRecipes=("session-token-ref",))


def test_compiled_mapping_fields_are_immutable() -> None:
    compiled = compile_governed_workflow(_compile_input())

    with pytest.raises(TypeError):
        compiled.hard_invariants["toolhostOnlyExecution"] = False  # type: ignore[index]
    with pytest.raises(TypeError):
        compiled.budgets["maxIterations"] = 99  # type: ignore[index]


def test_model_copy_cannot_enable_live_flags_or_replace_frozen_mappings() -> None:
    compiled = compile_governed_workflow(_compile_input())

    with pytest.raises(ValueError, match="model_copy update"):
        compiled.model_copy(update={"traffic_attached": True})
    with pytest.raises(ValueError, match="model_copy update"):
        compiled.model_copy(update={"budgets": {"maxIterations": 9999, "wallClockTimeoutMs": 30000}})
    with pytest.raises(ValueError, match="model_copy update"):
        dry_run_governed_workflow(compiled).model_copy(update={"model_call_attempted": True})


def test_dry_run_returns_effective_policy_without_live_execution() -> None:
    compiled = compile_governed_workflow(_compile_input())
    report = dry_run_governed_workflow(compiled)

    assert report.model_call_attempted is False
    assert report.tool_call_attempted is False
    assert report.network_attempted is False
    assert report.filesystem_attempted is False
    assert report.selected_recipe_ids == ("openmagi.research.cited-market-brief.v1.0.0",)
    assert report.effective_policy_snapshot_digest == "sha256:" + "3" * 64
    assert report.context_projection_mode == "explicit"
    assert report.output_projection_mode == "structured_claims_only"
    assert report.predicted_terminal_states == ("ask_user",)


def test_dry_run_surfaces_validation_failures_without_execution() -> None:
    compiled = compile_governed_workflow(_compile_input(projectionPolicy="raw_text_allowed"))
    report = dry_run_governed_workflow(compiled)

    assert report.ok is False
    assert "governed_raw_text_projection_forbidden" in report.reason_codes
    assert report.model_call_attempted is False
    assert report.tool_call_attempted is False


def test_dry_run_reports_available_tools_from_registry_not_requested_missing_tools() -> None:
    compiled = compile_governed_workflow(_compile_input(toolAllowlist=("MissingTool",), availableTools=()))
    report = dry_run_governed_workflow(compiled)

    assert report.ok is False
    assert "unknown_tool_ref" in report.reason_codes
    assert report.available_tools == ()


def test_workflow_compile_fixtures_exclude_model_visible_and_protected_values() -> None:
    for fixture_name in ("workflow_valid_research.json", "workflow_invalid_raw_text.json"):
        payload = json.loads((FIXTURE_DIR / fixture_name).read_text())
        encoded = json.dumps(payload, sort_keys=True).lower()

        forbidden_fragments = (
            "pro" + "mpt",
            "author" + "ization",
            "coo" + "kie",
            "to" + "ken",
            "sess" + "ion",
            "priv" + "ate",
        )
        assert all(fragment not in encoded for fragment in forbidden_fragments)


def test_workflow_compile_fixtures_validate_and_dry_run() -> None:
    valid = WorkflowCompileInput.model_validate_json((FIXTURE_DIR / "workflow_valid_research.json").read_text())
    invalid = WorkflowCompileInput.model_validate_json((FIXTURE_DIR / "workflow_invalid_raw_text.json").read_text())

    valid_report = dry_run_governed_workflow(compile_governed_workflow(valid))
    invalid_report = dry_run_governed_workflow(compile_governed_workflow(invalid))

    assert valid_report.ok is True
    assert invalid_report.ok is False
    assert "governed_raw_text_projection_forbidden" in invalid_report.reason_codes
