from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.runtime.governed_projection import (
    GovernedClaim,
    GovernedDraft,
    ProjectionDecision,
    ProjectionPolicy,
    ProjectionRenderer,
)


def test_governed_recipe_blocks_raw_text_projection() -> None:
    policy = ProjectionPolicy.model_validate(
        {
            "policyId": "research.strict",
            "mode": "structured_claims_then_render",
            "governed": True,
        }
    )
    draft = GovernedDraft.model_validate(
        {
            "requestId": "req_abc",
            "rawDraft": "Revenue grew 25% with no citation.",
            "claims": [],
            "artifacts": [],
        }
    )

    decision = ProjectionRenderer(policy).project(draft)

    assert decision.status == "blocked"
    assert "governed_raw_draft_projection_forbidden" in decision.reason_codes
    assert decision.user_visible_text is None


def test_validated_claims_render_without_raw_draft_leakage() -> None:
    policy = ProjectionPolicy.model_validate(
        {
            "policyId": "research.strict",
            "mode": "structured_claims_then_render",
            "governed": True,
        }
    )
    claim = GovernedClaim.model_validate(
        {
            "claimId": "claim_1",
            "text": "Revenue grew 18% in 2025.",
            "claimType": "numeric_claim",
            "supportStatus": "supported",
            "citationRefs": ["source_12_span_4"],
            "calculationRefs": [],
        }
    )
    draft = GovernedDraft.model_validate(
        {
            "requestId": "req_abc",
            "rawDraft": "Raw draft should not be used.",
            "claims": [claim.model_dump(by_alias=True)],
            "artifacts": [],
        }
    )

    decision = ProjectionRenderer(policy).project(draft)

    assert decision.status == "projected"
    assert decision.user_visible_text == "- Revenue grew 18% in 2025. [source_12_span_4]"
    assert decision.public_projection()["userVisibleText"] == decision.user_visible_text
    assert "Raw draft" not in decision.user_visible_text
    assert "Raw draft" not in str(decision.public_projection())


def test_weak_claim_for_governed_recipe_requires_abstain_or_block() -> None:
    policy = ProjectionPolicy.model_validate(
        {
            "policyId": "research.strict",
            "mode": "structured_claims_then_render",
            "governed": True,
        }
    )
    draft = GovernedDraft.model_validate(
        {
            "requestId": "req_abc",
            "rawDraft": "",
            "claims": [
                {
                    "claimId": "claim_weak",
                    "text": "The market is definitely accelerating.",
                    "claimType": "factual_claim",
                    "supportStatus": "weak",
                    "citationRefs": ["source_1_span_1"],
                    "calculationRefs": [],
                }
            ],
            "artifacts": [],
        }
    )

    decision = ProjectionRenderer(policy).project(draft)

    assert decision.status == "blocked"
    assert "claim_support_not_sufficient" in decision.reason_codes


def test_numeric_claim_requires_citation_or_calculation_ref() -> None:
    policy = ProjectionPolicy(policyId="backoffice.strict", mode="structured_claims_then_render")
    draft = GovernedDraft.model_validate(
        {
            "requestId": "req_numeric",
            "claims": [
                {
                    "claimId": "claim_number",
                    "text": "Gross margin was 34.2%.",
                    "claimType": "numeric_claim",
                    "supportStatus": "supported",
                    "citationRefs": [],
                    "calculationRefs": [],
                }
            ],
        }
    )

    decision = ProjectionRenderer(policy).project(draft)

    assert decision.status == "blocked"
    assert "numeric_claim_missing_evidence_ref" in decision.reason_codes


def test_projection_rejects_private_or_secret_claim_material() -> None:
    private_header = "Authorization: " + "Bearer " + "x" * 12
    with pytest.raises(ValidationError):
        GovernedClaim.model_validate(
            {
                "claimId": "claim_secret",
                "text": private_header,
                "claimType": "factual_claim",
                "supportStatus": "supported",
                "citationRefs": ["source_1_span_1"],
            }
        )

    with pytest.raises(ValidationError):
        GovernedClaim.model_validate(
            {
                "claimId": "claim_path",
                "text": "Safe text",
                "claimType": "factual_claim",
                "supportStatus": "supported",
                "citationRefs": ["/Users/kevin/.kube/config"],
            }
        )


def test_projection_decision_public_projection_rejects_direct_private_text() -> None:
    with pytest.raises(ValidationError):
        ProjectionDecision.model_validate(
            {
                "status": "projected",
                "policyId": "research.strict",
                "requestId": "req_private",
                "reasonCodes": ["unsafe_direct_decision"],
                "userVisibleText": "Authorization: " + "Bearer " + "x" * 12,
            }
        )


def test_projection_decision_public_projection_sanitizes_constructed_private_text() -> None:
    decision = ProjectionDecision.model_construct(
        status="projected",
        policy_id="research.strict",
        request_id="req_private",
        reason_codes=("unsafe_direct_decision",),
        user_visible_text="Authorization: " + "Bearer " + "x" * 12,
        claim_refs=("/Users/private",),
        artifact_refs=(),
    )

    public = decision.public_projection()

    assert "userVisibleText" not in public
    assert "Bearer" not in str(public)
    assert public["claimRefs"] == ["redacted_ref"]
    assert "projection_public_text_redacted" in public["reasonCodes"]


def test_projection_rejects_jwt_like_private_material() -> None:
    jwt_like = "aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc"
    with pytest.raises(ValidationError):
        GovernedClaim.model_validate(
            {
                "claimId": "claim_jwt",
                "text": f"Leaked token {jwt_like}",
                "claimType": "factual_claim",
                "supportStatus": "supported",
                "citationRefs": ["source_1_span_1"],
            }
        )
    decision = ProjectionDecision.model_construct(
        status="projected",
        policy_id="research.strict",
        request_id="req_private",
        reason_codes=("unsafe_direct_decision",),
        user_visible_text=f"Leaked token {jwt_like}",
        claim_refs=(),
        artifact_refs=(),
    )

    public = decision.public_projection()

    assert "userVisibleText" not in public
    assert "projection_public_text_redacted" in public["reasonCodes"]


def test_projection_decision_public_projection_sanitizes_constructed_private_status() -> None:
    decision = ProjectionDecision.model_construct(
        status="aaaaaaaaaaaa.bbbbbbbbbbbb.cccccccccccc",
        policy_id="research.strict",
        request_id="req_private",
        reason_codes=("unsafe_direct_decision",),
        user_visible_text=None,
        claim_refs=(),
        artifact_refs=(),
    )

    public = decision.public_projection()

    assert public["status"] == "blocked"
    assert "aaaaaaaaaaaa" not in str(public)


def test_governed_projection_import_boundary_is_schema_only() -> None:
    code = (
        "import sys;"
        "import openmagi_core_agent.runtime.governed_projection;"
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
        "kubernetes",
        "fastapi",
        "supabase",
    )
    for fragment in forbidden_fragments:
        assert fragment not in completed.stdout


def test_runtime_package_lazy_exports_projection_contracts() -> None:
    from openmagi_core_agent.runtime import ProjectionPolicy as ExportedPolicy
    from openmagi_core_agent.runtime import ProjectionRenderer as ExportedRenderer

    assert ExportedPolicy is ProjectionPolicy
    assert ExportedRenderer is ProjectionRenderer
