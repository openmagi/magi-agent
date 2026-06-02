from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from openmagi_core_agent.runtime.deterministic_policy import (
    DeterministicPolicy,
    RuntimeInvariantSet,
    evaluate_runtime_invariants,
)


def test_hard_invariants_cannot_be_disabled_by_customer_policy() -> None:
    policy = DeterministicPolicy.model_validate(
        {
            "policyId": "openmagi.research.strict",
            "customerConfigurable": {
                "requireCitations": False,
                "maxRepairAttempts": 2,
            },
            "runtimeInvariants": {
                "governedRawDraftStreamingForbidden": False,
                "toolHostOnlyExecution": False,
                "minimumReceiptSchemaRequired": False,
                "validatorBeforeProjectionRequired": False,
            },
        }
    )

    decision = evaluate_runtime_invariants(policy)

    assert decision.allowed is False
    assert "runtime_invariant_forgery" in decision.reason_codes
    assert decision.effective_invariants.governed_raw_draft_streaming_forbidden is True
    assert decision.effective_invariants.tool_host_only_execution is True
    assert decision.effective_invariants.minimum_receipt_schema_required is True
    assert decision.effective_invariants.validator_before_projection_required is True


def test_default_invariants_are_all_strict() -> None:
    invariants = RuntimeInvariantSet()

    assert invariants.governed_raw_draft_streaming_forbidden is True
    assert invariants.tool_host_only_execution is True
    assert invariants.minimum_receipt_schema_required is True
    assert invariants.source_snapshot_digest_span_required is True
    assert invariants.authority_anti_forgery_required is True
    assert invariants.secret_redaction_required is True
    assert invariants.validator_before_projection_required is True


def test_constructed_or_copied_invariants_are_still_strict() -> None:
    constructed = RuntimeInvariantSet.model_construct(
        governed_raw_draft_streaming_forbidden=False,
        tool_host_only_execution=False,
    )
    copied = RuntimeInvariantSet().model_copy(
        update={
            "minimum_receipt_schema_required": False,
            "secret_redaction_required": False,
        }
    )

    assert constructed.governed_raw_draft_streaming_forbidden is True
    assert constructed.tool_host_only_execution is True
    assert copied.minimum_receipt_schema_required is True
    assert copied.secret_redaction_required is True


def test_example_policy_fixtures_keep_runtime_invariants_strict() -> None:
    fixture_dir = Path(__file__).parent / "fixtures" / "deterministic_runtime"
    for path in sorted(fixture_dir.glob("*_policy.json")):
        payload = json.loads(path.read_text())
        if "policyId" not in payload:
            continue
        policy = DeterministicPolicy.model_validate(payload)
        decision = evaluate_runtime_invariants(policy)
        assert decision.allowed is True, path.name
        assert decision.reason_codes == ("runtime_invariants_strict",)


def test_deterministic_policy_import_boundary_is_schema_only() -> None:
    code = (
        "import sys;"
        "import openmagi_core_agent.runtime.deterministic_policy;"
        "print('\\n'.join(sorted(sys.modules)))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )
    forbidden_fragments = (
        "google.adk",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.tools.dispatcher",
        "openmagi_core_agent.memory",
        "openmagi_core_agent.channels",
        "openmagi_core_agent.web_acquisition",
        "kubernetes",
        "fastapi",
        "supabase",
    )
    for fragment in forbidden_fragments:
        assert fragment not in completed.stdout


def test_runtime_package_lazy_exports_deterministic_contracts() -> None:
    from openmagi_core_agent.runtime import DeterministicPolicy as ExportedPolicy
    from openmagi_core_agent.runtime import RuntimeInvariantSet as ExportedInvariants

    assert ExportedPolicy is DeterministicPolicy
    assert ExportedInvariants is RuntimeInvariantSet
