from __future__ import annotations

import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.harness.verifier_bus import (
    ApprovalRequestMetadata,
    FailureRoutingMetadata,
    VerifierBusMetadata,
    VerifierInputDeclaration,
    VerifierMetadata,
    VerifierResultMetadata,
    build_default_verifier_bus_metadata,
)


EXPECTED_STAGE_ORDER = (
    "schema_structured_output",
    "tool_evidence_contract",
    "file_artifact_delivery",
    "source_claim_link",
    "task_plan_completion",
    "security_policy",
    "llm_critic",
)


def test_default_bus_represents_deterministic_to_semantic_stage_order_without_attachments() -> None:
    bus = build_default_verifier_bus_metadata()
    dumped = bus.model_dump(by_alias=True)

    assert tuple(stage["stage"] for stage in dumped["stages"]) == EXPECTED_STAGE_ORDER
    assert tuple(stage["order"] for stage in dumped["stages"]) == tuple(range(1, 8))
    assert tuple(stage["phase"] for stage in dumped["stages"][:-1]) == ("deterministic",) * 6
    assert dumped["stages"][-1]["phase"] == "semantic_critic"
    assert dumped["metadataOnly"] is True
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["runnerAttached"] is False
    assert dumped["routeAttached"] is False
    assert dumped["canaryAttached"] is False


def test_default_verifiers_are_priority_sorted_and_default_off_except_hard_safety() -> None:
    bus = build_default_verifier_bus_metadata()
    verifiers = bus.effective_verifiers(deterministic_prerequisites_satisfied=True)

    ordered_pairs = tuple((verifier.stage, verifier.priority) for verifier in verifiers)
    assert ordered_pairs == tuple(sorted(ordered_pairs, key=lambda pair: (pair[0], pair[1])))

    by_id = {verifier.verifier_id: verifier for verifier in bus.verifiers}
    assert by_id["security-policy-hard-safety"].default_enabled is True
    assert by_id["security-policy-hard-safety"].disabled is False
    assert by_id["security-policy-hard-safety"].hard_safety is True
    assert by_id["llm-critic-fuzzy-quality"].default_enabled is False
    assert by_id["llm-critic-fuzzy-quality"].disabled is True


def test_verifier_input_declarations_are_metadata_only_refs_without_live_extraction() -> None:
    verifier = VerifierMetadata(
        verifierId="artifact-delivery",
        stage="file_artifact_delivery",
        phase="deterministic",
        priority=30,
        inputDeclarations=(
            VerifierInputDeclaration(
                evidenceTypes=("ArtifactVerify", "FileDeliver"),
                ledgerRefs=("ledger:artifact-delivery",),
                artifactRefs=("artifact:final-report",),
                sessionRefs=("session:active",),
                transcriptRefs=("transcript:turn-tail",),
                controlRefs=("control:approval-state",),
            ),
        ),
        failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
    )
    dumped = verifier.model_dump(by_alias=True)

    assert dumped["inputDeclarations"][0] == {
        "evidenceTypes": ("ArtifactVerify", "FileDeliver"),
        "ledgerRefs": ("ledger:artifact-delivery",),
        "artifactRefs": ("artifact:final-report",),
        "sessionRefs": ("session:active",),
        "transcriptRefs": ("transcript:turn-tail",),
        "controlRefs": ("control:approval-state",),
        "metadataOnly": True,
    }
    assert dumped["extractionAttached"] is False
    assert dumped["executionAttached"] is False


def test_verifier_id_rejects_empty_or_blank_values_while_description_may_be_empty() -> None:
    verifier = VerifierMetadata(
        verifierId="schema",
        stage="schema_structured_output",
        phase="deterministic",
        priority=10,
        description="",
    )

    assert verifier.description == ""

    for verifier_id in ("", "   "):
        with pytest.raises(ValidationError):
            VerifierMetadata(
                verifierId=verifier_id,
                stage="schema_structured_output",
                phase="deterministic",
                priority=10,
            )


def test_failure_routing_supports_retry_terminal_block_and_approval_metadata_only() -> None:
    routing = FailureRoutingMetadata(
        actions=("audit", "retry", "terminal", "block_final_answer", "approval_required"),
        retryable=True,
        terminal=True,
        blockFinalAnswer=True,
        approvalRequired=True,
        failClosed=True,
        approvalRequest=ApprovalRequestMetadata(
            kind="plan_approval",
            source="system",
            reason="High-impact action requires human approval",
            publicPreview="approve token=sk-test-secret Authorization: Bearer secret",
        ),
    )
    dumped = routing.model_dump(by_alias=True)

    assert dumped["approvalRequired"] is True
    assert dumped["approvalRequest"]["kind"] == "plan_approval"
    assert dumped["approvalRequest"]["source"] == "system"
    assert dumped["approvalRequest"]["controlRequestAttached"] is False
    assert "sk-test-secret" not in dumped["approvalRequest"]["publicPreview"]
    assert "Bearer secret" not in dumped["approvalRequest"]["publicPreview"]


@pytest.mark.parametrize(
    "approval_flag",
    (
        {"approvalRequired": True},
        {"approval_required": True},
    ),
)
def test_failure_routing_rejects_approval_required_without_matching_action(
    approval_flag: dict[str, bool],
) -> None:
    with pytest.raises(ValidationError):
        FailureRoutingMetadata(actions=("audit",), **approval_flag)


def test_failure_routing_rejects_approval_action_without_matching_flag() -> None:
    with pytest.raises(ValidationError):
        FailureRoutingMetadata(actions=("audit", "approval_required"), approvalRequired=False)


def test_hard_safety_verifiers_are_blocking_fail_closed_and_cannot_be_downgraded() -> None:
    hard = VerifierMetadata(
        verifierId="hard-security",
        stage="security_policy",
        phase="deterministic",
        priority=60,
        hardSafety=True,
        securityCritical=True,
    )

    assert hard.blocking is True
    assert hard.fail_closed is True
    assert hard.fail_open is False
    assert hard.opt_out is False
    assert hard.disabled is False
    assert hard.default_enabled is True

    for update in (
        {"blocking": False},
        {"failClosed": False},
        {"failOpen": True},
        {"optOut": True},
        {"disabled": True},
        {"defaultEnabled": False},
        {"securityCritical": False},
    ):
        with pytest.raises(ValidationError):
            hard.model_copy(update=update)


def test_non_hard_verifiers_may_be_audit_fail_open_and_default_off() -> None:
    verifier = VerifierMetadata(
        verifierId="claim-links-audit",
        stage="source_claim_link",
        phase="deterministic",
        priority=40,
        defaultEnabled=False,
        disabled=True,
        failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
    )

    assert verifier.hard_safety is False
    assert verifier.default_enabled is False
    assert verifier.disabled is True
    assert verifier.fail_open is True
    assert verifier.fail_closed is False


def test_default_bus_includes_dev_coding_verification_audit_metadata_default_off() -> None:
    bus = build_default_verifier_bus_metadata()
    verifier = by_verifier_id(bus, "dev-coding-verification-audit")

    assert verifier.stage == "tool_evidence_contract"
    assert verifier.phase == "deterministic"
    assert verifier.default_enabled is False
    assert verifier.disabled is True
    assert verifier.blocking is False
    assert verifier.fail_open is True
    assert verifier.fail_closed is False
    assert verifier.failure_routing.actions == ("audit",)
    assert verifier.failure_routing.block_final_answer is False
    assert verifier.input_declarations[0].evidence_types == (
        "GitDiff",
        "TestRun",
        "CodeDiagnostics",
        "CommitCheckpoint",
        "DeterministicEvidenceVerifier",
    )
    assert verifier.extraction_attached is False
    assert verifier.execution_attached is False
    assert verifier.runner_attached is False


def test_llm_critic_is_not_effective_until_deterministic_prerequisites_or_escalation() -> None:
    bus = build_default_verifier_bus_metadata()

    without_prereqs = bus.effective_verifiers(deterministic_prerequisites_satisfied=False)
    assert "llm_critic" not in {verifier.stage for verifier in without_prereqs}

    with_prereqs = bus.effective_verifiers(deterministic_prerequisites_satisfied=True)
    assert "llm_critic" not in {verifier.stage for verifier in with_prereqs}

    escalated = bus.effective_verifiers(
        deterministic_prerequisites_satisfied=False,
        escalationReason="synthesis_quality",
    )
    assert tuple(verifier.stage for verifier in escalated)[-1] == "llm_critic"

    critic_enabled = by_verifier_id(bus, "llm-critic-fuzzy-quality").model_copy(
        update={"disabled": False, "defaultEnabled": True}
    )
    enabled_bus = bus.model_copy(
        update={
            "verifiers": tuple(
                critic_enabled if verifier.verifier_id == critic_enabled.verifier_id else verifier
                for verifier in bus.verifiers
            )
        }
    )
    assert "llm_critic" not in {
        verifier.stage
        for verifier in enabled_bus.effective_verifiers(
            deterministic_prerequisites_satisfied=False
        )
    }
    assert tuple(
        verifier.stage
        for verifier in enabled_bus.effective_verifiers(
            deterministic_prerequisites_satisfied=True
        )
    )[-1] == "llm_critic"


@pytest.mark.parametrize(
    "kwargs",
    (
        {"escalationReason": "not-a-real-reason"},
        {"escalation_reason": "not-a-real-reason"},
    ),
)
def test_invalid_escalation_reason_does_not_enable_llm_critic(kwargs: dict[str, str]) -> None:
    bus = build_default_verifier_bus_metadata()

    with pytest.raises(ValueError):
        bus.effective_verifiers(
            deterministic_prerequisites_satisfied=False,
            **kwargs,
        )


def test_invalid_escalation_alias_is_rejected_even_when_other_alias_is_valid() -> None:
    bus = build_default_verifier_bus_metadata()

    with pytest.raises(ValueError):
        bus.effective_verifiers(
            deterministic_prerequisites_satisfied=False,
            escalationReason="not-a-real-reason",
            escalation_reason="synthesis_quality",
        )


def test_conflicting_valid_escalation_aliases_are_rejected() -> None:
    bus = build_default_verifier_bus_metadata()

    with pytest.raises(ValueError):
        bus.effective_verifiers(
            deterministic_prerequisites_satisfied=False,
            escalationReason="ambiguity",
            escalation_reason="synthesis_quality",
        )


def test_matching_valid_escalation_aliases_enable_llm_critic() -> None:
    bus = build_default_verifier_bus_metadata()

    escalated = bus.effective_verifiers(
        deterministic_prerequisites_satisfied=False,
        escalationReason="synthesis_quality",
        escalation_reason="synthesis_quality",
    )

    assert tuple(verifier.stage for verifier in escalated)[-1] == "llm_critic"


def test_bus_rejects_removing_protected_hard_safety_verifier() -> None:
    bus = build_default_verifier_bus_metadata()

    with pytest.raises(ValidationError):
        bus.model_copy(
            update={
                "verifiers": tuple(
                    verifier
                    for verifier in bus.verifiers
                    if verifier.verifier_id != "security-policy-hard-safety"
                )
            }
        )


def test_bus_rejects_replacing_protected_hard_safety_verifier_with_downgraded_verifier() -> None:
    bus = build_default_verifier_bus_metadata()
    downgraded = VerifierMetadata(
        verifierId="security-policy-hard-safety",
        stage="security_policy",
        phase="deterministic",
        priority=60,
        failureRouting=FailureRoutingMetadata(actions=("audit",), failOpen=True),
        defaultEnabled=False,
        disabled=True,
    )

    with pytest.raises(ValidationError):
        bus.model_copy(
            update={
                "verifiers": tuple(
                    downgraded
                    if verifier.verifier_id == "security-policy-hard-safety"
                    else verifier
                    for verifier in bus.verifiers
                )
            }
        )


@pytest.mark.parametrize(
    "replacement",
    (
        VerifierMetadata(
            verifierId="security-policy-hard-safety",
            stage="llm_critic",
            phase="semantic_critic",
            priority=60,
            failureRouting=FailureRoutingMetadata(
                actions=("audit", "terminal", "block_final_answer"),
                terminal=True,
                blockFinalAnswer=True,
                failClosed=True,
            ),
            hardSafety=True,
            securityCritical=True,
        ),
        VerifierMetadata(
            verifierId="security-policy-hard-safety",
            stage="task_plan_completion",
            phase="deterministic",
            priority=60,
            failureRouting=FailureRoutingMetadata(
                actions=("audit", "terminal", "block_final_answer"),
                terminal=True,
                blockFinalAnswer=True,
                failClosed=True,
            ),
            hardSafety=True,
            securityCritical=True,
        ),
        VerifierMetadata(
            verifierId="security-policy-hard-safety",
            stage="security_policy",
            phase="deterministic",
            priority=60,
            failureRouting=FailureRoutingMetadata(actions=("audit",), failClosed=True),
            hardSafety=True,
            securityCritical=True,
        ),
        VerifierMetadata(
            verifierId="security-policy-hard-safety",
            stage="security_policy",
            phase="deterministic",
            priority=61,
            failureRouting=FailureRoutingMetadata(
                actions=("audit", "terminal", "block_final_answer"),
                terminal=True,
                blockFinalAnswer=True,
                failClosed=True,
            ),
            hardSafety=True,
            securityCritical=True,
        ),
    ),
)
def test_bus_rejects_protected_hard_safety_replacements_that_weaken_bus_invariants(
    replacement: VerifierMetadata,
) -> None:
    bus = build_default_verifier_bus_metadata()

    with pytest.raises(ValidationError):
        bus.model_copy(
            update={
                "verifiers": tuple(
                    replacement
                    if verifier.verifier_id == "security-policy-hard-safety"
                    else verifier
                    for verifier in bus.verifiers
                )
            }
        )


def test_public_result_messages_are_redacted_and_truncated_without_chat_or_tool_routes() -> None:
    result = VerifierResultMetadata(
        verifierId="tool-contract",
        status="failed",
        publicSummary="x" * 260 + " token=sk-test-secret",
        retryMessage="retry with Authorization: Bearer secret",
        failureMessage="failed with cookie=session-secret",
    )
    dumped = result.model_dump(by_alias=True)

    assert len(dumped["publicSummary"]) <= 200
    assert dumped["publicSummary"].endswith("...")
    serialized = json.dumps(dumped)
    assert "sk-test-secret" not in serialized
    assert "Bearer secret" not in serialized
    assert "session-secret" not in serialized


@pytest.mark.parametrize(
    "flag",
    (
        "trafficAttached",
        "executionAttached",
        "runnerAttached",
        "routeAttached",
        "canaryAttached",
    ),
)
def test_bus_model_copy_rejects_runtime_attachment_flags(flag: str) -> None:
    bus = build_default_verifier_bus_metadata()

    with pytest.raises(ValidationError):
        bus.model_copy(update={flag: True})


def test_verifier_metadata_model_construct_cannot_forge_runtime_attachment_flags() -> None:
    verifier = VerifierMetadata.model_construct(
        verifier_id="constructed-verifier",
        stage="schema_structured_output",
        phase="deterministic",
        priority=10,
        extraction_attached=True,
        traffic_attached=True,
        execution_attached=True,
        runner_attached=True,
        route_attached=True,
        canary_attached=True,
    )

    assert verifier.extraction_attached is False
    assert verifier.traffic_attached is False
    assert verifier.execution_attached is False
    assert verifier.runner_attached is False
    assert verifier.route_attached is False
    assert verifier.canary_attached is False
    dumped = verifier.model_dump(by_alias=True)
    assert dumped["extractionAttached"] is False
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["runnerAttached"] is False
    assert dumped["routeAttached"] is False
    assert dumped["canaryAttached"] is False


def test_verifier_result_model_construct_cannot_forge_runtime_attachment_flags() -> None:
    result = VerifierResultMetadata.model_construct(
        verifier_id="constructed-result",
        status="audit",
        traffic_attached=True,
        execution_attached=True,
    )

    assert result.traffic_attached is False
    assert result.execution_attached is False
    dumped = result.model_dump(by_alias=True)
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False


def test_verifier_bus_model_construct_cannot_forge_runtime_attachment_flags() -> None:
    default_bus = build_default_verifier_bus_metadata()
    bus = VerifierBusMetadata.model_construct(
        stages=default_bus.stages,
        verifiers=default_bus.verifiers,
        traffic_attached=True,
        execution_attached=True,
        runner_attached=True,
        route_attached=True,
        canary_attached=True,
    )

    assert bus.traffic_attached is False
    assert bus.execution_attached is False
    assert bus.runner_attached is False
    assert bus.route_attached is False
    assert bus.canary_attached is False
    dumped = bus.model_dump(by_alias=True)
    assert dumped["trafficAttached"] is False
    assert dumped["executionAttached"] is False
    assert dumped["runnerAttached"] is False
    assert dumped["routeAttached"] is False
    assert dumped["canaryAttached"] is False


def test_models_accept_snake_input_dump_camel_aliases_and_forbid_extra_fields() -> None:
    verifier = VerifierMetadata(
        verifier_id="schema",
        stage="schema_structured_output",
        phase="deterministic",
        priority=10,
        input_declarations=(VerifierInputDeclaration(evidence_types=("DeterministicEvidenceVerifier",)),),
        failure_routing=FailureRoutingMetadata(actions=("audit",), fail_open=True),
    )
    dumped = verifier.model_dump(by_alias=True)

    assert dumped["verifierId"] == "schema"
    assert dumped["inputDeclarations"][0]["evidenceTypes"] == ("DeterministicEvidenceVerifier",)
    assert dumped["failureRouting"]["failOpen"] is True

    with pytest.raises(ValidationError):
        VerifierInputDeclaration(evidenceTypes=("TestRun",), unexpected=True)


def test_verifier_bus_import_stays_adk_runner_runtime_and_route_free() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

module = importlib.import_module("magi_agent.harness.verifier_bus")
assert hasattr(module, "build_default_verifier_bus_metadata")

forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport.chat",
    "magi_agent.transport.tools",
)
loaded = [
    module_name
    for module_name in sys.modules
    if any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in forbidden_prefixes
    )
]
if loaded:
    raise AssertionError(f"verifier_bus import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def by_verifier_id(bus: VerifierBusMetadata, verifier_id: str) -> VerifierMetadata:
    return {verifier.verifier_id: verifier for verifier in bus.verifiers}[verifier_id]
