from __future__ import annotations

import json
import sys

import pytest
from pydantic import ValidationError

from magi_agent.recipes.first_party.self_improvement import (
    build_self_improvement_proposal_recipe_manifest,
)
from magi_agent.self_improvement.proposals import (
    SelfImprovementProposal,
    SelfImprovementProposalConfig,
    SelfImprovementProposalRequest,
    SelfImprovementProposalResult,
    SelfImprovementProposalService,
)


def _request(**overrides: object) -> SelfImprovementProposalRequest:
    payload: dict[str, object] = {
        "requestId": "self-improvement-proposal:req-1",
        "proposalType": "recipe_change",
        "policySnapshotDigest": "sha256:" + "a" * 64,
        "evalObservationDigestRefs": ("sha256:" + "b" * 64,),
        "failureClusterRefs": ("failure-cluster:" + "c" * 32,),
        "title": "Tighten citation final gate",
        "summary": "Require citation evidence before projecting final answer claims.",
        "changeRefs": ("recipe:self-improvement.research-citation-gate",),
        "reasonCodes": ("unsupported_claim_regression",),
        "rawPrompt": "private user prompt",
        "rawOutput": "private model output",
        "rawPrivatePath": "/Users/kevin/private/patch.diff",
        "toolLogs": "Authorization: Bearer self-improvement-opaque-token",
        "hiddenReasoning": "private chain of thought",
    }
    payload.update(overrides)
    return SelfImprovementProposalRequest.model_validate(payload)


def test_proposal_recipe_manifest_is_default_off_and_attachment_free() -> None:
    manifest = build_self_improvement_proposal_recipe_manifest()

    assert manifest.recipe_id == "recipe:self-improvement.proposal@1"
    assert manifest.status == "disabled"
    assert manifest.governed is True
    assert manifest.proposal_only is True
    assert manifest.required_policy_refs == (
        "policy:self-improvement.eval-observation-required@1",
        "policy:self-improvement.no-direct-mutation@1",
    )
    flags = manifest.attachment_flags.model_dump(by_alias=True)
    assert set(flags.values()) == {False}
    assert manifest.live_tool_refs == ()
    assert manifest.live_callback_refs == ()
    assert manifest.live_runner_route_refs == ()

    copied = manifest.model_copy(
        update={
            "status": "enabled",
            "requiredPolicyRefs": (),
            "liveToolRefs": ("tool:unsafe",),
            "attachmentFlags": {"trafficAttached": True},
        }
    )
    deprecated_copy = manifest.copy(
        update={
            "live_tool_refs": ("tool:unsafe",),
            "required_policy_refs": (),
            "attachment_flags": {"runnerAttached": True},
        }
    )
    constructed = type(manifest).model_construct(
        status="enabled",
        requiredPolicyRefs=(),
        liveToolRefs=("tool:unsafe",),
        attachmentFlags={"modelCallEnabled": True},
    )
    for value in (copied, deprecated_copy, constructed):
        assert value.status == "disabled"
        assert value.required_policy_refs == manifest.required_policy_refs
        assert value.live_tool_refs == ()
        assert value.live_callback_refs == ()
        assert value.live_runner_route_refs == ()
        assert set(value.attachment_flags.model_dump(by_alias=True).values()) == {False}


def test_proposal_service_is_disabled_by_default_and_authority_free() -> None:
    service = SelfImprovementProposalService(SelfImprovementProposalConfig())

    result = service.generate(_request())

    assert result.status == "disabled"
    assert result.proposal is None
    assert result.blocked_reason == "self_improvement_proposal_disabled"
    assert result.direct_change_decision == "denied"
    assert set(result.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_proposal_config_cannot_enable_live_runner_or_automatic_mutation() -> None:
    config = SelfImprovementProposalConfig.model_construct(
        enabled=True,
        localFakeProposalEnabled=True,
        liveAdkRunnerEnabled=True,
        automaticMutationEnabled=True,
    )
    copied = config.model_copy(
        update={"liveAdkRunnerEnabled": True, "automaticMutationEnabled": True}
    )
    deprecated_copy = config.copy(
        update={"live_adk_runner_enabled": True, "automatic_mutation_enabled": True}
    )

    for value in (config, copied, deprecated_copy):
        dumped = value.model_dump(by_alias=True)
        assert dumped["liveAdkRunnerEnabled"] is False
        assert dumped["automaticMutationEnabled"] is False


def test_local_fake_structured_proposal_requires_eval_observation_and_policy_snapshot() -> None:
    service = SelfImprovementProposalService(
        SelfImprovementProposalConfig(enabled=True, localFakeProposalEnabled=True)
    )

    result = service.generate(_request())

    assert result.status == "proposed_local_fake"
    assert result.proposal is not None
    assert result.direct_change_decision == "denied"
    assert result.adk_primitive == "ADK Runner boundary"

    proposal = result.proposal
    assert proposal.proposal_type == "recipe_change"
    assert proposal.execution_default == "denied"
    assert proposal.policy_snapshot_digest == "sha256:" + "a" * 64
    assert proposal.eval_observation_digest_refs == ("sha256:" + "b" * 64,)
    assert proposal.failure_cluster_refs == ("failure-cluster:" + "c" * 32,)
    assert proposal.change_refs == ("recipe:self-improvement.research-citation-gate",)
    assert proposal.proposal_id.startswith("self-improvement-proposal:")
    assert proposal.proposal_digest.startswith("sha256:")
    assert proposal.denied_direct_change_refs == ()

    encoded = json.dumps(result.model_dump(by_alias=True), sort_keys=True)
    for fragment in (
        "private user prompt",
        "private model output",
        "/Users/kevin",
        "Authorization: Bearer",
        "private chain of thought",
        "rawPrompt",
        "rawOutput",
        "toolLogs",
    ):
        assert fragment not in encoded


@pytest.mark.parametrize(
    "proposal_type",
    (
        "recipe_change",
        "harness_config_change",
        "plugin_config_change",
        "test_fixture_addition",
        "docs_note",
        "blocked",
    ),
)
def test_allowed_proposal_types_are_structured_and_deterministic(proposal_type: str) -> None:
    service = SelfImprovementProposalService(
        SelfImprovementProposalConfig(enabled=True, localFakeProposalEnabled=True)
    )

    first = service.generate(_request(proposalType=proposal_type))
    second = service.generate(_request(proposalType=proposal_type))

    assert first.proposal is not None
    assert second.proposal is not None
    assert first.proposal.proposal_digest == second.proposal.proposal_digest
    if proposal_type == "blocked":
        assert first.status == "blocked"
        assert first.blocked_reason == "proposal_type_blocked"
        assert first.proposal.status == "blocked"
    else:
        assert first.status == "proposed_local_fake"
        assert first.proposal.status == "proposal_only"


def test_proposal_request_requires_observation_refs_and_rejects_unsafe_fields() -> None:
    with pytest.raises(ValidationError, match="evalObservationDigestRefs"):
        _request(evalObservationDigestRefs=())

    with pytest.raises(ValidationError, match="policySnapshotDigest"):
        _request(policySnapshotDigest="policy:raw")

    with pytest.raises(ValidationError, match="proposalType"):
        _request(proposalType="deploy_change")

    with pytest.raises(ValidationError, match="changeRefs"):
        _request(changeRefs=("deploy:/k8s/prod",))

    with pytest.raises(ValidationError, match="title"):
        _request(title="raw output: /Users/kevin/private")


def test_direct_production_secret_db_deploy_and_sealed_changes_are_denied() -> None:
    service = SelfImprovementProposalService(
        SelfImprovementProposalConfig(enabled=True, localFakeProposalEnabled=True)
    )

    result = service.generate(
        _request(
            requestedDirectChanges=(
                "production_code_patch",
                "deploy_change",
                "secret_change",
                "db_migration",
                "sealed_file_hotpatch",
            )
        )
    )

    assert result.status == "proposed_local_fake"
    assert result.proposal is not None
    assert result.direct_change_decision == "denied"
    assert result.proposal.denied_direct_change_refs == (
        "direct-change:production_code_patch",
        "direct-change:deploy_change",
        "direct-change:secret_change",
        "direct-change:db_migration",
        "direct-change:sealed_file_hotpatch",
    )
    flags = result.authority_flags.model_dump(by_alias=True)
    assert flags["codeMutationEnabled"] is False
    assert flags["deployMutationEnabled"] is False
    assert flags["secretMutationEnabled"] is False
    assert flags["dbMutationEnabled"] is False


def test_bounded_repair_blocks_unsupported_or_unsafe_proposals() -> None:
    service = SelfImprovementProposalService(
        SelfImprovementProposalConfig(enabled=True, localFakeProposalEnabled=True)
    )

    result = service.generate(
        {
            "requestId": "self-improvement-proposal:req-unsafe",
            "proposalType": "unsupported_runtime_mutation",
            "policySnapshotDigest": "sha256:" + "a" * 64,
            "evalObservationDigestRefs": ("sha256:" + "b" * 64,),
            "title": "Deploy directly",
            "summary": "Activate runtime and patch production now.",
            "changeRefs": ("deploy:k8s-prod",),
        }
    )

    assert result.status == "blocked"
    assert result.blocked_reason == "unsupported_proposal_type"
    assert result.proposal is not None
    assert result.proposal.proposal_type == "blocked"
    assert result.proposal.status == "blocked"
    assert result.proposal.change_refs == ()
    assert result.proposal.reason_codes == ("unsupported_proposal_type",)


def test_proposal_trust_boundary_rejects_copy_construct_and_raw_projection_spoofing() -> None:
    service = SelfImprovementProposalService(
        SelfImprovementProposalConfig(enabled=True, localFakeProposalEnabled=True)
    )
    result = service.generate(_request(requestedDirectChanges=("secret_change",)))
    assert result.proposal is not None
    proposal = result.proposal

    with pytest.raises(ValueError, match="model_copy"):
        proposal.model_copy(update={"summary": "/Users/kevin/private"})
    with pytest.raises(ValueError, match="copy is disabled"):
        proposal.copy(update={"proposal_digest": "sha256:" + "d" * 64})
    with pytest.raises(ValidationError, match="proposalDigest"):
        SelfImprovementProposal.model_validate(
            proposal.model_dump(by_alias=True) | {"proposalDigest": "sha256:" + "d" * 64}
        )
    with pytest.raises(ValidationError, match="title"):
        SelfImprovementProposal.model_construct(
            **(proposal.model_dump(by_alias=True) | {"title": "raw output: private"})
        )

    copied_result = result.model_copy(
        update={
            "directChangeDecision": "allowed",
            "authorityFlags": {"codeMutationEnabled": True},
        }
    )
    deprecated_copy = result.copy(
        update={
            "direct_change_decision": "allowed",
            "authority_flags": {"codeMutationEnabled": True},
        }
    )
    constructed_result = SelfImprovementProposalResult.model_construct(
        status="proposed_local_fake",
        proposal=proposal,
        directChangeDecision="allowed",
        authorityFlags={"codeMutationEnabled": True},
    )
    for value in (copied_result, deprecated_copy, constructed_result):
        assert value.direct_change_decision == "denied"
        assert set(value.authority_flags.model_dump(by_alias=True).values()) == {False}

    object.__setattr__(result, "direct_change_decision", "allowed")
    object.__setattr__(proposal, "execution_default", "allowed")
    assert result.model_dump(by_alias=True)["directChangeDecision"] == "denied"
    assert proposal.model_dump(by_alias=True)["executionDefault"] == "denied"


def test_proposal_import_boundary_does_not_initialize_live_runner_or_toolhost() -> None:
    forbidden = {
        "google.adk.runners",
        "google.adk.agents",
        "magi_agent.tools.host",
        "magi_agent.transport.chat",
        "magi_agent.memory.hipocampus",
        "magi_agent.memory.qmd",
    }
    for module_name in forbidden:
        sys.modules.pop(module_name, None)

    __import__("magi_agent.self_improvement.proposals")
    __import__("magi_agent.recipes.first_party.self_improvement")

    assert forbidden.isdisjoint(sys.modules)
