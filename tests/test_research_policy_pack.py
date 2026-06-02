from __future__ import annotations

import importlib
import inspect
import json

import pytest

from openmagi_core_agent.research.policy_pack import (
    DEFAULT_RESEARCH_POLICY_PACK_KEY,
    ResearchClaimSupportPolicy,
    ResearchCriteriaTemplateRef,
    ResearchPolicyPack,
    ResearchVerifierStage,
    build_default_research_policy_pack,
    select_research_policy_pack,
)


def test_default_research_policy_pack_composes_prior_contracts_and_is_default_off() -> None:
    pack = build_default_research_policy_pack()
    projection = pack.public_projection()

    assert pack.key == DEFAULT_RESEARCH_POLICY_PACK_KEY
    assert pack.owner == "openmagi_first_party_research_harness"
    assert pack.default_off is True
    assert pack.local_only is True
    assert pack.fake_provider_only is True
    assert projection["activation"] == {
        "defaultOff": True,
        "localOnly": True,
        "fakeProviderOnly": True,
        "liveExecutionAllowed": False,
        "liveWebAllowed": False,
        "browserExecutionAllowed": False,
        "providerCallsAllowed": False,
        "toolExecutionAllowed": False,
        "modelCallsAllowed": False,
        "memoryWritesAllowed": False,
        "channelDeliveryAllowed": False,
        "userVisiblePythonActivationAllowed": False,
    }
    assert set(pack.execution_verbs) == {
        "searched",
        "read",
        "reviewed",
        "compared",
        "checked",
        "confirmed",
        "verified",
        "analyzed",
        "summarized",
        "inspected",
    }
    assert pack.required_source_proof == (
        "runtime_source_ref",
        "opened_snapshot_or_document_read",
        "content_digest",
        "inspected_timestamp",
        "source_kind",
        "span_refs",
        "redaction_status",
        "freshness_window",
    )
    assert tuple(template.criteria_set_id for template in pack.criteria_templates) == (
        "research-acceptance-positioning",
        "research-acceptance-pricing",
        "research-acceptance-recent-events",
    )
    assert pack.repair_actions == (
        "inspect_missing_source",
        "refresh_stale_source",
        "extract_missing_span",
        "downgrade_weak_claim",
        "omit_unsupported_claim",
        "request_user_clarification",
        "return_partial_with_missing_work_report",
    )
    assert tuple(stage.stage_id for stage in pack.verifier_stages) == (
        "stage:action-proof",
        "stage:source-proof",
        "stage:claim-proof",
        "stage:task-proof",
        "stage:intermediate-boundary",
        "stage:repair",
        "stage:final-projection",
    )


def test_research_policy_pack_rejects_live_authority_and_live_tools() -> None:
    pack = build_default_research_policy_pack()

    with pytest.raises(ValueError, match="liveExecutionAllowed"):
        pack.model_copy(update={"liveExecutionAllowed": True})
    with pytest.raises(ValueError, match="toolExecutionAllowed"):
        pack.model_copy(update={"toolExecutionAllowed": True})
    with pytest.raises(ValueError, match="providerCallsAllowed"):
        pack.model_copy(update={"providerCallsAllowed": True})
    with pytest.raises(ValueError, match="liveWebAllowed"):
        pack.model_copy(update={"liveWebAllowed": True})


def test_research_policy_pack_projection_is_digest_safe_and_rejects_raw_private_text() -> None:
    pack = build_default_research_policy_pack()
    projection = pack.public_projection()
    dumped = json.dumps(projection, sort_keys=True)

    assert projection["digest"].startswith("sha256:")
    forbidden_fragments = (
        "http://",
        "https://",
        "/Users/",
        "/workspace/",
        "raw_source",
        "raw output",
        "authorization",
        "secret",
        "token",
        "cookie",
    )
    assert all(fragment not in dumped for fragment in forbidden_fragments)

    with pytest.raises(ValueError, match="raw, private"):
        pack.model_copy(update={"activationGates": ("raw_source_dump",)})


def test_research_policy_pack_selection_uses_explicit_recipe_metadata_not_category() -> None:
    pack = build_default_research_policy_pack()

    selected = select_research_policy_pack(
        {"policyPackRef": DEFAULT_RESEARCH_POLICY_PACK_KEY},
        registry=(pack,),
    )

    assert selected is pack
    with pytest.raises(ValueError, match="policyPackRef"):
        select_research_policy_pack({"category": "research"}, registry=(pack,))
    with pytest.raises(ValueError, match="unknown research policy pack"):
        select_research_policy_pack({"policyPackRef": "research"}, registry=(pack,))


def test_policy_pack_selection_rejects_malformed_canonical_metadata_even_with_valid_alias() -> None:
    pack = build_default_research_policy_pack()

    malformed_requests = (
        {"policyPackRef": 0, "policy_pack_ref": DEFAULT_RESEARCH_POLICY_PACK_KEY},
        {"policyPackRef": False, "policy_pack_ref": DEFAULT_RESEARCH_POLICY_PACK_KEY},
        {"policyPackRef": "", "policy_pack_ref": DEFAULT_RESEARCH_POLICY_PACK_KEY},
    )
    for metadata in malformed_requests:
        with pytest.raises((TypeError, ValueError), match="policyPackRef"):
            select_research_policy_pack(metadata, registry=(pack,))

    with pytest.raises(ValueError, match="policyPackRef"):
        select_research_policy_pack(
            {
                "policyPackRef": DEFAULT_RESEARCH_POLICY_PACK_KEY,
                "policy_pack_ref": "research.determinism.other",
            },
            registry=(pack,),
        )


def test_explicit_empty_policy_registry_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown research policy pack"):
        select_research_policy_pack(
            {"policyPackRef": DEFAULT_RESEARCH_POLICY_PACK_KEY},
            registry=(),
        )


def test_default_key_cannot_weaken_required_policy_semantics() -> None:
    base = build_default_research_policy_pack()

    with pytest.raises(ValueError, match="factRequires"):
        base.claim_support_policy.model_copy(update={"factRequires": ()})

    weakening_updates = (
        {"criteriaTemplates": ()},
        {"criteriaTemplates": base.criteria_templates[:1]},
        {"verifierStages": ()},
        {"verifierStages": base.verifier_stages[:2]},
        {
            "verifierStages": (
                ResearchVerifierStage(
                    stageId=base.verifier_stages[0].stage_id,
                    verifierRef=base.verifier_stages[0].verifier_ref,
                    boundaryRefs=("finalProjection",),
                ),
                *base.verifier_stages[1:],
            )
        },
        {"repairActions": ()},
        {"repairActions": ("omit_unsupported_claim",)},
        {"requiredSourceProof": ("runtime_source_ref", "content_digest")},
        {"activationGates": ("explicit_policy_pack_ref",)},
    )
    for update in weakening_updates:
        with pytest.raises(ValueError):
            base.model_copy(update=update)


def test_policy_pack_allows_configured_first_party_composition_without_live_authority() -> None:
    base = build_default_research_policy_pack()

    configured = base.model_copy(
        update={
            "requiredSourceProof": (
                *base.required_source_proof,
                "verifier_digest",
            ),
            "criteriaTemplates": (
                *tuple(reversed(base.criteria_templates)),
                ResearchCriteriaTemplateRef(
                    criteriaSetId="criteria-set:configured-extra",
                    templateKey="configured-extra",
                    requiredEvidenceTypes=("configured_evidence",),
                ),
            ),
            "repairActions": tuple(reversed(base.repair_actions)),
            "verifierStages": (
                *base.verifier_stages,
                ResearchVerifierStage(
                    stageId="stage:configured-extra",
                    verifierRef="configured-extra",
                    boundaryRefs=("finalProjection",),
                ),
            ),
            "activationGates": (
                *base.activation_gates,
                "manual_research_policy_ack",
            ),
        }
    )

    assert configured.required_source_proof[-1] == "verifier_digest"
    assert configured.criteria_templates[-1].template_key == "configured-extra"
    assert configured.repair_actions == tuple(reversed(base.repair_actions))
    assert configured.verifier_stages[-1].verifier_ref == "configured-extra"
    assert configured.activation_gates[-1] == "manual_research_policy_ack"
    assert configured.live_execution_allowed is False
    assert configured.tool_execution_allowed is False


def test_nested_policy_subclasses_cannot_spoof_default_policy_projection() -> None:
    with pytest.raises(TypeError, match="ResearchClaimSupportPolicy subclasses"):
        class ForgedClaimSupportPolicy(ResearchClaimSupportPolicy):
            pass

    with pytest.raises(TypeError, match="ResearchCriteriaTemplateRef subclasses"):
        class ForgedCriteriaTemplateRef(ResearchCriteriaTemplateRef):
            pass

    with pytest.raises(TypeError, match="ResearchVerifierStage subclasses"):
        class ForgedVerifierStage(ResearchVerifierStage):
            pass


def test_top_level_policy_pack_subclass_cannot_spoof_registry_projection() -> None:
    with pytest.raises(TypeError, match="ResearchPolicyPack subclasses"):
        class ForgedPolicyPack(ResearchPolicyPack):
            pass


def test_policy_pack_instance_method_shadowing_cannot_spoof_registry_selection() -> None:
    pack = build_default_research_policy_pack()

    def forged_projection() -> dict[str, object]:
        return {
            "key": DEFAULT_RESEARCH_POLICY_PACK_KEY,
            "activation": {"toolExecutionAllowed": True},
            "claimSupportPolicy": {"factRequires": ()},
        }

    pack.__dict__["public_projection"] = forged_projection

    with pytest.raises(ValueError, match="unexpected runtime attributes"):
        select_research_policy_pack(
            {"policyPackRef": DEFAULT_RESEARCH_POLICY_PACK_KEY},
            registry=(pack,),
        )


def test_policy_pack_instance_method_shadowing_cannot_spoof_direct_projection() -> None:
    pack = build_default_research_policy_pack()
    pack.__dict__["public_projection"] = lambda: {"spoofed": True}

    with pytest.raises(ValueError, match="unexpected runtime attributes"):
        pack.public_projection()


def test_research_policy_pack_projection_rejects_post_creation_mutation() -> None:
    pack = build_default_research_policy_pack()
    pack.__dict__["tool_execution_allowed"] = True
    pack.__dict__["digest"] = "sha256:" + "1" * 64
    pack.__dict__["_created_fingerprint"] = "attacker-refreshed"

    with pytest.raises(ValueError, match="policy pack"):
        pack.public_projection()


def test_research_policy_pack_import_has_no_live_adk_or_provider_attachment() -> None:
    module = importlib.import_module("openmagi_core_agent.research.policy_pack")
    source = inspect.getsource(module)

    assert "Runner(" not in source
    assert "FunctionTool(" not in source
    assert "LongRunningFunctionTool(" not in source
    assert "google.adk" not in source
    assert "requests." not in source
    assert "httpx." not in source
    assert "browser" in source
    assert "openmagi_core_agent.harness.presets" not in source
