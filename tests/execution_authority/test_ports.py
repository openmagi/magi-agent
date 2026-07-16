from __future__ import annotations

from inspect import Parameter, iscoroutinefunction, signature
from typing import get_args, get_type_hints

import pytest

from magi_agent.execution_authority.contracts import (
    CompletionVerdict,
    EvidenceEdge,
    FinalizationEvaluationRequest,
    GenericJournalEventDraft,
    JournalEvent,
    JournalEventDraft,
    NormalizedInputDraft,
    NormalizedInputSnapshot,
    RequiredProjection,
)

from magi_agent.execution_authority.ports import (
    AuthoritativeClockPort,
    AuthorityPort,
    CompletionEvaluatorPort,
    EffectExecutorPort,
    EvidencePayloadResolverPort,
    EvidenceQueryPort,
    JournalPort,
    NormalizedInputNormalizerPort,
    NormalizedInputSnapshotPort,
    RecoveryAdapterPort,
    ResumeBindingVerifierPort,
    SourceSnapshotResolverPort,
    UserDecisionKeyPort,
    UserDecisionVerifierPort,
    WorkspacePublicationPort,
)


def _parameters(protocol: type[object], method: str) -> tuple[str, ...]:
    return tuple(signature(getattr(protocol, method)).parameters)


def test_epoch_creation_requires_the_immutable_snapshot_reference() -> None:
    assert _parameters(JournalPort, "create_epoch") == (
        "self",
        "task_contract",
        "task_contract_snapshot_ref",
    )
    parameters = signature(JournalPort.create_epoch).parameters
    assert parameters["task_contract"].kind.name == "KEYWORD_ONLY"
    assert parameters["task_contract_snapshot_ref"].default is Parameter.empty


def test_named_lifecycle_units_of_work_do_not_accept_caller_built_events() -> None:
    forbidden_types = {
        JournalEvent,
        JournalEventDraft,
        GenericJournalEventDraft,
    }

    def contains_caller_built_event(annotation: object) -> bool:
        return annotation in forbidden_types or any(
            contains_caller_built_event(argument) for argument in get_args(annotation)
        )

    for method_name, method in JournalPort.__dict__.items():
        if method_name.startswith("_") or method_name in {"append", "append_with_outbox"}:
            continue
        if not callable(method):
            continue
        hints = get_type_hints(method)
        for parameter_name in signature(method).parameters:
            if parameter_name == "self":
                continue
            annotation = hints.get(parameter_name)
            assert not contains_caller_built_event(annotation), (
                method_name,
                parameter_name,
                annotation,
            )


def test_epoch_seal_derives_registry_digest_from_typed_projection_keys() -> None:
    assert _parameters(JournalPort, "seal_epoch") == (
        "self",
        "completion_epoch_id",
        "expected_compare_version",
        "required_projections",
    )
    hints = get_type_hints(JournalPort.seal_epoch)
    assert hints["required_projections"] == tuple[RequiredProjection, ...]


def test_completion_evaluator_accepts_only_the_bound_evaluation_envelope() -> None:
    assert _parameters(CompletionEvaluatorPort, "evaluate") == (
        "self",
        "evaluation",
    )
    hints = get_type_hints(CompletionEvaluatorPort.evaluate)
    assert hints["evaluation"] is FinalizationEvaluationRequest
    assert hints["return"] is CompletionVerdict


def test_generic_append_accepts_only_non_reserved_draft_type() -> None:
    assert get_type_hints(JournalPort.append)["draft"] is GenericJournalEventDraft
    assert get_type_hints(JournalPort.append_with_outbox)["draft"] is GenericJournalEventDraft


def test_recovery_unit_of_work_accepts_frozen_decision_and_its_proof_context() -> None:
    assert _parameters(JournalPort, "begin_recovery") == (
        "self",
        "decision",
        "context",
    )


def test_execution_start_accepts_one_frozen_request_and_store_cas_values() -> None:
    assert _parameters(JournalPort, "mark_executing") == (
        "self",
        "start",
        "expected_action_compare_version",
        "expected_attempt_compare_version",
        "expected_partition_compare_version",
    )


def test_production_lease_and_outbox_methods_use_store_clock_not_caller_time() -> None:
    methods = (
        "acquire_lease",
        "renew_lease",
        "release_lease",
        "take_over_recovery_lease",
        "claim_outbox_item",
        "record_outbox_attempt",
        "acknowledge_outbox_item",
    )
    for method in methods:
        assert "now" not in _parameters(JournalPort, method)


def test_journal_port_exposes_replay_complete_snapshots_and_unfinished_queries() -> None:
    expected = {
        "ensure_partition",
        "get_journal_head",
        "read_partition",
        "get_action",
        "get_action_intent",
        "get_attempt",
        "get_task_contract",
        "list_unfinished_actions",
        "list_unfinished_attempts",
        "get_epoch",
        "get_user_decision",
        "get_lease",
        "get_projection_cursor",
        "get_recovery_session",
        "get_outbox_item",
        "list_pending_outbox",
        "get_workspace",
        "get_workspace_commit",
        "scan_integrity",
    }
    assert expected.issubset(set(dir(JournalPort)))
    assert _parameters(JournalPort, "get_attempt") == (
        "self",
        "action_id",
        "attempt_id",
    )


def test_journal_port_covers_system_decisions_outbox_and_recovery_takeover() -> None:
    required = {
        "expire_user_decision",
        "append_with_outbox",
        "take_over_partition_recovery",
    }
    assert required.issubset(set(dir(JournalPort)))
    assert "now" not in _parameters(JournalPort, "expire_user_decision")
    assert "now" not in _parameters(JournalPort, "append_with_outbox")


def test_user_decision_ingress_accepts_only_a_trusted_verified_receipt() -> None:
    assert _parameters(JournalPort, "record_user_decision") == (
        "self",
        "verified_receipt",
    )
    hints = get_type_hints(JournalPort.record_user_decision)
    assert hints["verified_receipt"].__name__ == "VerifiedUserDecisionReceipt"
    assert hints["return"].__name__ == "UserDecisionRecording"


def test_resume_verifier_returns_a_typed_current_state_attestation() -> None:
    hints = get_type_hints(ResumeBindingVerifierPort.verify_current)
    assert hints["return"].__name__ == "VerifiedAuthorityResumeBinding"


def test_approval_consumption_accepts_only_the_verified_resume_attestation() -> None:
    parameters = _parameters(JournalPort, "consume_user_approval")
    assert "verified_resume_binding" in parameters
    assert "resume_binding" not in parameters
    assert "current_policy_digest" not in parameters
    assert "current_capabilities_digest" not in parameters
    assert get_type_hints(JournalPort.consume_user_approval)[
        "verified_resume_binding"
    ].__name__ == "VerifiedAuthorityResumeBinding"


def test_request_deny_and_resolve_uows_return_persistence_receipts() -> None:
    hints = get_type_hints
    assert hints(JournalPort.request_user_decision)["return"].__name__ == (
        "UserDecisionRequestRecording"
    )
    assert hints(JournalPort.deny_action)["return"].__name__ == "ActionDenialRecording"
    assert hints(JournalPort.resolve_action)["return"].__name__ == (
        "ActionResolutionRecording"
    )


def test_projection_outbox_and_completion_uows_are_compare_and_swap_bound() -> None:
    assert "expected_compare_version" in _parameters(JournalPort, "advance_projection_cursor")
    assert "expected_compare_version" in _parameters(JournalPort, "claim_outbox_item")
    assert _parameters(JournalPort, "persist_completion") == (
        "self",
        "seal",
        "request",
        "verdict",
    )


def test_dependency_inversion_ports_cover_normalization_evidence_and_workspace_io() -> None:
    assert get_type_hints(NormalizedInputNormalizerPort.normalize)["return"] is (
        NormalizedInputDraft
    )
    assert get_type_hints(NormalizedInputSnapshotPort.persist)["return"] is (
        NormalizedInputSnapshot
    )
    assert getattr(NormalizedInputSnapshotPort, "resolve")
    assert getattr(EvidencePayloadResolverPort, "resolve_payload")
    assert getattr(EvidenceQueryPort, "resolve_node")
    assert getattr(EvidenceQueryPort, "edges_for_node")
    assert getattr(SourceSnapshotResolverPort, "resolve_snapshot")
    assert getattr(EffectExecutorPort, "execute")
    assert getattr(WorkspacePublicationPort, "publish")
    assert getattr(RecoveryAdapterPort, "reconcile")
    assert getattr(AuthoritativeClockPort, "now")


def test_evidence_query_port_exposes_typed_incident_edge_lookup() -> None:
    assert _parameters(EvidenceQueryPort, "edges_for_node") == (
        "self",
        "evidence_id",
    )
    parameters = signature(EvidenceQueryPort.edges_for_node).parameters
    assert parameters["evidence_id"].kind is Parameter.KEYWORD_ONLY
    assert get_type_hints(EvidenceQueryPort.edges_for_node)["evidence_id"] is str
    assert get_type_hints(EvidenceQueryPort.edges_for_node)["return"] == tuple[EvidenceEdge, ...]


@pytest.mark.parametrize(
    "protocol",
    (
        AuthoritativeClockPort,
        JournalPort,
        AuthorityPort,
        UserDecisionVerifierPort,
        UserDecisionKeyPort,
        ResumeBindingVerifierPort,
        NormalizedInputNormalizerPort,
        NormalizedInputSnapshotPort,
        EffectExecutorPort,
        RecoveryAdapterPort,
        EvidencePayloadResolverPort,
        EvidenceQueryPort,
        SourceSnapshotResolverPort,
        WorkspacePublicationPort,
    ),
)
def test_public_ports_are_runtime_checkable_protocols(protocol: type[object]) -> None:
    assert getattr(protocol, "_is_protocol", False)
    assert getattr(protocol, "_is_runtime_protocol", False)


def test_external_effect_and_recovery_boundaries_are_async() -> None:
    assert iscoroutinefunction(EffectExecutorPort.execute)
    assert iscoroutinefunction(RecoveryAdapterPort.reconcile)
    assert _parameters(EffectExecutorPort, "execute") == (
        "self",
        "execution_token",
        "start",
    )
    assert _parameters(RecoveryAdapterPort, "reconcile") == (
        "self",
        "decision",
        "context",
    )
