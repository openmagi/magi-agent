from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from magi_agent.shadow.gate3b_bundle import (
    Gate3BLiveDuplicateBundle,
    Gate3BProductionAuthorityFlags,
    load_gate3b_live_duplicate_bundle,
)
from magi_agent.shadow.gate3b_ingest import (
    convert_gate3b_live_duplicate_to_gate3a_recorded_bundle,
)


FIXTURES = Path(__file__).parent / "fixtures" / "gate3b"


def test_live_duplicate_bundle_converts_to_gate3a_recorded_bundle_without_live_attachment() -> None:
    gate3b_bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )

    handoff = convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(gate3b_bundle)

    assert handoff.recorded_bundle_payload["schemaVersion"] == "gate3a.recordedBundle.v1"
    assert handoff.recorded_bundle_payload["recordingMode"] == "recorded_redacted"
    assert handoff.recorded_bundle_payload["sourceProvenance"]["liveCaptureIncluded"] is False
    assert handoff.recorded_bundle_payload["sourceProvenance"]["productionPathIncluded"] is False
    assert handoff.recorded_bundle_payload["turn"]["channel"] == "local_replay"
    assert handoff.handoff_metadata == {
        "sourceSchemaVersion": "gate3b.liveDuplicateBundle.v1",
        "sourceBundleId": "bundle_live_duplicate_fixture_0001",
        "handoffMode": "gate3b_schema_only_to_gate3a_local_replay_input",
        "gate3aPayloadValidated": False,
        "liveCaptureConsumed": False,
        "adkRunnerInvoked": False,
        "toolsExecuted": False,
        "storageWritten": False,
        "queueEnqueued": False,
    }


def test_handoff_accepts_validated_payload_and_revalidates_gate3b_input() -> None:
    gate3b_bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )
    payload = gate3b_bundle.model_dump(mode="json", by_alias=True)

    handoff = convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(payload)

    assert handoff.recorded_bundle_payload["schemaVersion"] == "gate3a.recordedBundle.v1"


def test_handoff_preserves_safe_evidence_audit_metadata() -> None:
    gate3b_bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )

    handoff = convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(gate3b_bundle)

    assert handoff.recorded_bundle_payload["evidenceRecords"] == [
        {
            "recordId": "audit_redacted_0001",
            "auditId": "audit_redacted_0001",
            "redactionReview": "verified",
            "externalAckIncluded": False,
        }
    ]


def test_handoff_rejects_tampered_constructed_gate3b_bundle() -> None:
    gate3b_bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )
    tampered_payload = gate3b_bundle.model_dump(mode="python", by_alias=False)
    tampered_payload["attachment_flags"] = {"productionRouteAttached": True}
    tampered = Gate3BLiveDuplicateBundle.model_construct(
        **tampered_payload,
    )

    with pytest.raises(ValidationError):
        convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(tampered)


def test_handoff_rejects_constructed_gate3b_bundle_with_raw_true_flag_object() -> None:
    gate3b_bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )
    tampered_payload = gate3b_bundle.model_dump(mode="python", by_alias=False)
    tampered_payload["attachment_flags"] = gate3b_bundle.attachment_flags.model_construct(
        production_route_attached=True,
    )
    tampered = Gate3BLiveDuplicateBundle.model_construct(**tampered_payload)

    with pytest.raises(ValidationError):
        convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(tampered)


def test_handoff_rejects_constructed_gate3b_bundle_with_raw_true_authority_object() -> None:
    gate3b_bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )
    tampered_payload = gate3b_bundle.model_dump(mode="python", by_alias=False)
    tampered_payload["production_authority_flags"] = (
        Gate3BProductionAuthorityFlags.model_construct(
            can_delay_typescript_response=True,
            can_alter_typescript_response=False,
            can_block_typescript_response=False,
            can_influence_user_output=False,
            python_response_authority=False,
            typescript_response_authority_only=True,
        )
    )
    tampered = Gate3BLiveDuplicateBundle.model_construct(**tampered_payload)

    with pytest.raises(ValidationError):
        convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(tampered)


def test_handoff_rejects_constructed_gate3b_bundle_with_raw_false_invalid_authority_object() -> None:
    gate3b_bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )
    tampered_payload = gate3b_bundle.model_dump(mode="python", by_alias=False)
    tampered_payload["production_authority_flags"] = (
        Gate3BProductionAuthorityFlags.model_construct(
            can_delay_typescript_response=False,
            can_alter_typescript_response=False,
            can_block_typescript_response=False,
            can_influence_user_output=False,
            python_response_authority=False,
            typescript_response_authority_only=False,
        )
    )
    tampered = Gate3BLiveDuplicateBundle.model_construct(**tampered_payload)

    with pytest.raises(ValidationError):
        convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(tampered)
