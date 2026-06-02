from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from openmagi_core_agent.shadow.gate3b_bundle import (
    Gate3BAttachmentFlags,
    Gate3BLiveDuplicateBundle,
    Gate3BProductionAuthorityFlags,
    _reject_raw_attachment_flag_state,
    _reject_raw_authority_flag_state,
)


@dataclass(frozen=True)
class Gate3BToGate3AHandoff:
    recorded_bundle_payload: dict[str, object]
    handoff_metadata: dict[str, object]


def convert_gate3b_live_duplicate_to_gate3a_recorded_bundle(
    bundle: Gate3BLiveDuplicateBundle | Mapping[str, Any],
) -> Gate3BToGate3AHandoff:
    gate3b_bundle = _validated_gate3b_bundle_snapshot(bundle)
    evidence_audit_metadata = gate3b_bundle.evidence_audit_metadata.as_dict()
    evidence_record = {
        "recordId": evidence_audit_metadata.get("auditId", "gate3b_evidence_audit_metadata"),
        **evidence_audit_metadata,
    }

    gate3a_payload = {
        "schemaVersion": "gate3a.recordedBundle.v1",
        "bundleId": gate3b_bundle.bundle_id,
        "sourceRuntime": gate3b_bundle.source_runtime,
        "recordingMode": "recorded_redacted",
        "redactionStatus": gate3b_bundle.redaction_status,
        "createdAt": gate3b_bundle.created_at,
        "sourceProvenance": {
            "sourceKind": "local_fixture",
            "sourcePath": gate3b_bundle.source_provenance.source_path,
            "productionPathIncluded": False,
            "liveCaptureIncluded": False,
        },
        "turn": gate3b_bundle.turn.model_dump(
            by_alias=True,
            mode="json",
            warnings=False,
        ),
        "recipe": {
            "recipeSnapshotId": gate3b_bundle.recipe.recipe_snapshot_id,
            "packIds": gate3b_bundle.recipe.pack_ids,
            "hardSafetyEnabled": gate3b_bundle.recipe.hard_safety_enabled,
        },
        "transcriptEntries": [
            entry.as_dict() for entry in gate3b_bundle.transcript_entries
        ],
        "agentEvents": [event.as_dict() for event in gate3b_bundle.agent_events],
        "recordedToolResults": [
            tool_result.model_dump(by_alias=True, mode="json", warnings=False)
            for tool_result in gate3b_bundle.recorded_tool_results
        ],
        "controlEvents": [event.as_dict() for event in gate3b_bundle.control_events],
        "evidenceRecords": [evidence_record],
    }
    return Gate3BToGate3AHandoff(
        recorded_bundle_payload=gate3a_payload,
        handoff_metadata={
            "sourceSchemaVersion": gate3b_bundle.schema_version,
            "sourceBundleId": gate3b_bundle.bundle_id,
            "handoffMode": "gate3b_schema_only_to_gate3a_local_replay_input",
            "gate3aPayloadValidated": False,
            "liveCaptureConsumed": False,
            "adkRunnerInvoked": False,
            "toolsExecuted": False,
            "storageWritten": False,
            "queueEnqueued": False,
        },
    )


def _validated_gate3b_bundle_snapshot(
    bundle: Gate3BLiveDuplicateBundle | Mapping[str, Any],
) -> Gate3BLiveDuplicateBundle:
    if not isinstance(bundle, Gate3BLiveDuplicateBundle):
        return Gate3BLiveDuplicateBundle.model_validate(bundle)
    if not isinstance(bundle.attachment_flags, Gate3BAttachmentFlags):
        _raise_tampered_gate3b_bundle("attachmentFlags")
    if not isinstance(bundle.production_authority_flags, Gate3BProductionAuthorityFlags):
        _raise_tampered_gate3b_bundle("productionAuthorityFlags")
    try:
        _reject_raw_attachment_flag_state(bundle.attachment_flags)
    except ValueError:
        _raise_tampered_gate3b_bundle("attachmentFlags")
    try:
        _reject_raw_authority_flag_state(bundle.production_authority_flags)
    except ValueError:
        _raise_tampered_gate3b_bundle("productionAuthorityFlags")
    payload = bundle.model_dump(by_alias=True, mode="json", warnings=False)
    return Gate3BLiveDuplicateBundle.model_validate(payload)


def _raise_tampered_gate3b_bundle(location: str) -> None:
    error = ValueError("Gate 3B constructed bundle failed immutable safety revalidation")
    raise ValidationError.from_exception_data(
        "Gate3BLiveDuplicateBundle",
        [
            {
                "type": "value_error",
                "loc": (location,),
                "msg": f"Value error, {error}",
                "input": None,
                "ctx": {"error": error},
            }
        ],
    )


__all__ = [
    "Gate3BToGate3AHandoff",
    "convert_gate3b_live_duplicate_to_gate3a_recorded_bundle",
]
