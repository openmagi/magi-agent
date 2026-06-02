from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.gate3a_bundle import Gate3ARecordedBundle
from openmagi_core_agent.shadow.gate3b_bundle import (
    Gate3BAttachmentFlags,
    Gate3BJsonRecord,
    Gate3BLiveDuplicateBundle,
    Gate3BProductionAuthorityFlags,
    Gate3BRecordedToolResult,
    Gate3BSourceProvenance,
    load_gate3b_live_duplicate_bundle,
)


FIXTURES = Path(__file__).parent / "fixtures" / "gate3b"


def _valid_bundle_payload() -> dict[str, object]:
    return {
        "schemaVersion": "gate3b.liveDuplicateBundle.v1",
        "bundleId": "bundle_live_duplicate_fixture_0001",
        "captureSurface": "selected_bot_post_turn_bundle",
        "sourceRuntime": "typescript-core-agent",
        "responseAuthority": "typescript-only",
        "recordingMode": "live_duplicate_redacted",
        "redactionStatus": "verified",
        "createdAt": "2026-05-16T12:00:00Z",
        "sourceProvenance": {
            "sourceKind": "live_duplicate_validation_metadata",
            "captureId": "capture_selected_post_turn_0001",
            "captureSurface": "selected_bot_post_turn_bundle",
            "capturePoint": "post_turn_redacted_duplicate",
            "sourcePath": "local-fixtures/gate3b/redacted-live-duplicate.json",
            "productionPathIncluded": False,
            "liveTrafficConsumed": False,
        },
        "recipe": {
            "recipeSnapshotId": "recipe_snapshot_gate3b_redacted_v1",
            "immutableSnapshotId": "recipe_snapshot_gate3b_redacted_v1",
            "packIds": ["openmagi.research"],
            "hardSafetyEnabled": True,
        },
        "transcriptEntries": [
            {
                "entryId": "transcript_redacted_0001",
                "role": "assistant",
                "publicText": "Redacted public answer.",
            }
        ],
        "agentEvents": [
            {
                "eventId": "agent_event_redacted_0001",
                "eventType": "text",
                "publicText": "Redacted public answer.",
            }
        ],
        "controlEvents": [
            {
                "eventId": "control_event_redacted_0001",
                "eventType": "none",
                "summary": "No control action.",
            }
        ],
        "recordedToolResults": [
            {
                "toolCallId": "tool_call_redacted_0001",
                "toolName": "search.readonly",
                "status": "recorded",
                "outputMetadata": {
                    "resultCount": 1,
                    "executionSurface": {
                        "declaredSurface": "recorded_metadata_only",
                        "recordedOnly": True,
                        "shellExecuted": False,
                        "codeExecuted": False,
                        "packageManagerExecuted": False,
                        "externalSideEffects": False,
                    },
                },
                "dispatchedLive": False,
            }
        ],
        "evidenceAuditMetadata": {
            "auditId": "audit_redacted_0001",
            "redactionReview": "verified",
            "externalAckIncluded": False,
        },
        "attachmentFlags": {
            "productionRouteAttached": False,
            "productionTranscriptAttached": False,
            "productionSseAttached": False,
            "userOutputAttached": False,
            "telegramAttached": False,
            "liveToolAttached": False,
            "liveRunnerAttached": False,
            "productionStorageAttached": False,
            "productionQueueAttached": False,
            "evidenceBlockAttached": False,
        },
        "productionAuthorityFlags": {
            "canDelayTypescriptResponse": False,
            "canAlterTypescriptResponse": False,
            "canBlockTypescriptResponse": False,
            "canInfluenceUserOutput": False,
            "pythonResponseAuthority": False,
            "typescriptResponseAuthorityOnly": True,
        },
    }


def test_valid_redacted_live_duplicate_bundle_validates() -> None:
    bundle = Gate3BLiveDuplicateBundle.model_validate(_valid_bundle_payload())

    assert bundle.schema_version == "gate3b.liveDuplicateBundle.v1"
    assert bundle.capture_surface == "selected_bot_post_turn_bundle"
    assert bundle.response_authority == "typescript-only"
    assert bundle.attachment_flags.production_transcript_attached is False
    assert bundle.production_authority_flags.can_influence_user_output is False
    assert bundle.recorded_tool_results[0].status == "recorded"
    assert bundle.recorded_tool_results[0].dispatched_live is False


def test_valid_live_duplicate_fixture_loads() -> None:
    bundle = load_gate3b_live_duplicate_bundle(
        "redacted_live_duplicate_bundle.json",
        bundle_root=FIXTURES,
    )

    assert bundle.bundle_id == "bundle_live_duplicate_fixture_0001"
    assert bundle.redaction_status == "verified"


def test_gate3b_bundle_is_distinguishable_from_gate3a() -> None:
    payload = _valid_bundle_payload()

    with pytest.raises(ValidationError):
        Gate3ARecordedBundle.model_validate(payload)

    bundle = Gate3BLiveDuplicateBundle.model_validate(payload)
    assert bundle.schema_version != "gate3a.recordedBundle.v1"
    assert bundle.recording_mode == "live_duplicate_redacted"


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload.update({"authorization": "Bearer abcdefghijklmnop"}),
            id="raw-auth-header",
        ),
        pytest.param(
            lambda payload: payload.update({"authorization": "Basic abcdefghijklmnop"}),
            id="raw-non-bearer-auth-header",
        ),
        pytest.param(
            lambda payload: payload.update({"OPENAI_API_KEY": "sk-abcdefghijklmnop"}),
            id="env-style-secret",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"][0].update(  # type: ignore[index, union-attr]
                {"publicText": "STRIPE_SECRET_KEY=stripe_live_secret"}
            ),
            id="env-style-secret-key",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"][0].update(  # type: ignore[index, union-attr]
                {"publicText": "SUPABASE_SERVICE_ROLE_KEY=supabase_secret"}
            ),
            id="service-role-key",
        ),
        pytest.param(
            lambda payload: payload.update({"telegramToken": "123456:ABCDEFabcdef123456"}),
            id="telegram-token",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"].append(  # type: ignore[index, union-attr]
                {
                    "eventId": "agent_event_redacted_0002",
                    "publicText": "-----BEGIN OPENSSH PRIVATE KEY-----",
                }
            ),
            id="openssh-private-key",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"].append(  # type: ignore[index, union-attr]
                {
                    "eventId": "agent_event_redacted_0003",
                    "publicText": "-----BEGIN PRIVATE KEY-----\nredacted\n-----END PRIVATE KEY-----",
                }
            ),
            id="pem-private-key",
        ),
        pytest.param(
            lambda payload: payload["sourceProvenance"].update(  # type: ignore[index, union-attr]
                {"sourcePath": "/data/bots/bot-123/workspace/transcript.jsonl"}
            ),
            id="production-workspace-path",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"].append(  # type: ignore[index, union-attr]
                {"hiddenReasoning": "private chain of thought"}
            ),
            id="hidden-reasoning",
        ),
        pytest.param(
            lambda payload: payload["recordedToolResults"].append(  # type: ignore[index, union-attr]
                {"privateToolPreview": "raw private preview"}
            ),
            id="private-tool-preview",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(  # type: ignore[index, union-attr]
                {"productionTranscriptAttached": True}
            ),
            id="production-transcript-write",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(  # type: ignore[index, union-attr]
                {"productionSseAttached": True}
            ),
            id="production-sse-write",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(  # type: ignore[index, union-attr]
                {"liveToolAttached": True}
            ),
            id="live-tool-side-effect",
        ),
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(  # type: ignore[index, union-attr]
                {"evidenceBlockAttached": True}
            ),
            id="evidence-block",
        ),
        pytest.param(
            lambda payload: payload["evidenceAuditMetadata"].update(  # type: ignore[index, union-attr]
                {"signedExternalAck": "raw signed acknowledgement payload"}
            ),
            id="signed-ack",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"][0].update(  # type: ignore[index, union-attr]
                {"liveShadowExecuted": 1}
            ),
            id="truthy-live-shadow-flex-record",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"][0].update(  # type: ignore[index, union-attr]
                {"liveShadowExecutedNow": True}
            ),
            id="truthy-live-shadow-flex-record-variant",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"][0].update(  # type: ignore[index, union-attr]
                {"storageWritten": "true"}
            ),
            id="truthy-storage-flex-record",
        ),
        pytest.param(
            lambda payload: payload["agentEvents"][0].update(  # type: ignore[index, union-attr]
                {"storageWrittenAt": True}
            ),
            id="truthy-storage-flex-record-variant",
        ),
        pytest.param(
            lambda payload: payload["evidenceAuditMetadata"].update(  # type: ignore[index, union-attr]
                {"queueEnqueued": True}
            ),
            id="truthy-queue-flex-record",
        ),
        pytest.param(
            lambda payload: payload["evidenceAuditMetadata"].update(  # type: ignore[index, union-attr]
                {"queueEnqueuedNow": True}
            ),
            id="truthy-queue-flex-record-variant",
        ),
    ),
)
def test_invalid_bundle_rejects_redaction_and_attachment_violations(
    mutation: object,
) -> None:
    payload = _valid_bundle_payload()
    mutation(payload)  # type: ignore[operator]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


def test_false_local_diagnostic_metadata_aliases_are_allowed() -> None:
    payload = _valid_bundle_payload()
    payload["agentEvents"][0].update(  # type: ignore[index, union-attr]
        {
            "liveShadowExecuted": False,
            "storageWritten": False,
            "queueEnqueued": False,
        }
    )

    bundle = Gate3BLiveDuplicateBundle.model_validate(payload)

    assert bundle.agent_events[0].model_extra["liveShadowExecuted"] is False
    assert bundle.agent_events[0].model_extra["storageWritten"] is False
    assert bundle.agent_events[0].model_extra["queueEnqueued"] is False


def test_invalid_redaction_violation_fixture_fails() -> None:
    with pytest.raises(ValidationError):
        load_gate3b_live_duplicate_bundle(
            "redaction_violation_live_duplicate_bundle.json",
            bundle_root=FIXTURES,
        )


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "private reasoning",
        "reasoning_trace",
        "privateToolPreview",
        "signed external ack",
        "signed acknowledgement payload",
        "productionRouteAttached",
        "telegram attached",
        "liveRunnerAttached",
        "production_queue_attached",
        "evidence block mode enabled",
        "customExtractorOutput",
        "workspace adoption requested",
        "schedulerResumeRequested",
        "canary traffic enabled",
        "liveCaptureIncluded",
        "liveTrafficConsumed",
        "live capture consumed",
        "raw connector credentials included",
        "canaryTrafficEnabled",
        "pvcMounted",
        "workspaceAttached",
        "workspaceMounted",
        "adkRunnerInvoked",
        "adkRunnerAttached",
    ),
)
def test_generic_string_values_reject_forbidden_surface_claims(unsafe_value: str) -> None:
    payload = _valid_bundle_payload()
    payload["agentEvents"][0]["publicText"] = unsafe_value  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "signed ack",
        "SIGNED_ACK",
        "signedAck",
        "signedack",
        "production route",
        "PRODUCTION_QUEUE",
        "productionStorage",
        "productionstorage",
        "Telegram",
        "telegram",
        "live tool",
        "LIVE_TOOL",
        "liveRunner",
        "liverunner",
        "evidence block",
        "EVIDENCE_BLOCK",
        "customExtractor",
        "customextractor",
        "workspace adoption",
        "WORKSPACE_ADOPTION",
        "schedulerResume",
        "schedulerresume",
        "canary traffic",
        "CANARY_TRAFFIC",
        "live capture consumed",
        "raw connector credentials included",
        "pvc mounted",
        "workspace attached",
        "workspace mounted",
        "adk runner invoked",
        "adk runner attached",
    ),
)
def test_generic_string_values_reject_base_forbidden_surface_claims(
    unsafe_value: str,
) -> None:
    payload = _valid_bundle_payload()
    payload["agentEvents"][0]["publicText"] = unsafe_value  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_value",
    (
        "hidden_reasoning",
        "hiddenReasoning",
        "hiddenreasoning",
        "chain_of_thought",
        "chainOfThought",
        "chainofthought",
        "private_tool_input",
        "privateToolInput",
        "privatetoolinput",
        "raw_tool_preview",
        "rawToolPreview",
        "rawtoolpreview",
        "raw_connector_credentials",
        "rawConnectorCredentials",
        "rawconnectorcredentials",
        "workspace_mutation",
        "workspaceMutation",
        "workspacemutation",
        "child_execution",
        "childExecution",
        "childexecution",
        "scheduler_run",
        "schedulerRun",
        "schedulerrun",
        "background_resume",
        "backgroundResume",
        "backgroundresume",
        "production_transcript_write",
        "productionTranscriptWrite",
        "productiontranscriptwrite",
        "production_sse_append",
        "productionSseAppend",
        "productionsseappend",
    ),
)
def test_generic_string_values_reject_forbidden_claim_key_aliases(
    unsafe_value: str,
) -> None:
    payload = _valid_bundle_payload()
    payload["agentEvents"][0]["publicText"] = unsafe_value  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


@pytest.mark.parametrize(
    "unsafe_key",
    (
        "liveCaptureIncluded",
        "liveTrafficConsumed",
        "live capture consumed",
        "raw connector credentials included",
        "canaryTrafficEnabled",
        "pvcMounted",
        "workspaceAttached",
        "workspaceMounted",
        "adkRunnerInvoked",
        "adkRunnerAttached",
    ),
)
def test_flexible_boolean_claim_keys_reject_forbidden_surface_claims(
    unsafe_key: str,
) -> None:
    payload = _valid_bundle_payload()
    payload["agentEvents"][0][unsafe_key] = False  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


def test_model_copy_cannot_weaken_false_attachment_or_authority_flags() -> None:
    flags = Gate3BAttachmentFlags()
    authority = Gate3BProductionAuthorityFlags()
    bundle = Gate3BLiveDuplicateBundle.model_validate(_valid_bundle_payload())

    with pytest.raises(ValidationError):
        flags.model_copy(update={"productionTranscriptAttached": True})

    with pytest.raises(ValidationError):
        authority.model_copy(update={"canInfluenceUserOutput": True})

    with pytest.raises(ValidationError):
        bundle.model_copy(update={"attachmentFlags": {"productionRouteAttached": True}})


def test_model_construct_dump_forces_false_attachment_and_authority_flags() -> None:
    flags = Gate3BAttachmentFlags.model_construct(
        production_transcript_attached=True,
        production_sse_attached=True,
        live_tool_attached=True,
        evidence_block_attached=True,
    )
    authority = Gate3BProductionAuthorityFlags.model_construct(
        can_delay_typescript_response=True,
        can_alter_typescript_response=True,
        can_block_typescript_response=True,
        can_influence_user_output=True,
        python_response_authority=True,
        typescript_response_authority_only=False,
    )

    flag_dump = flags.model_dump(mode="json", by_alias=True)
    authority_dump = authority.model_dump(mode="json", by_alias=True)

    assert all(value is False for value in flag_dump.values())
    assert authority_dump == {
        "canDelayTypescriptResponse": False,
        "canAlterTypescriptResponse": False,
        "canBlockTypescriptResponse": False,
        "canInfluenceUserOutput": False,
        "pythonResponseAuthority": False,
        "typescriptResponseAuthorityOnly": True,
    }


def test_model_construct_attribute_access_cannot_expose_true_attachment_or_authority_flags() -> None:
    flags = Gate3BAttachmentFlags.model_construct(
        production_route_attached=True,
        production_transcript_attached=True,
        telegram_attached=True,
        live_runner_attached=True,
        production_queue_attached=True,
    )
    authority = Gate3BProductionAuthorityFlags.model_construct(
        can_delay_typescript_response=True,
        can_alter_typescript_response=True,
        can_block_typescript_response=True,
        can_influence_user_output=True,
        python_response_authority=True,
        typescript_response_authority_only=False,
    )

    assert flags.production_route_attached is False
    assert flags.production_transcript_attached is False
    assert flags.telegram_attached is False
    assert flags.live_runner_attached is False
    assert flags.production_queue_attached is False
    assert authority.can_delay_typescript_response is False
    assert authority.can_alter_typescript_response is False
    assert authority.can_block_typescript_response is False
    assert authority.can_influence_user_output is False
    assert authority.python_response_authority is False
    assert authority.typescript_response_authority_only is True


def test_json_record_constructed_model_dump_rejects_unsafe_extra_values() -> None:
    record = Gate3BJsonRecord.model_construct(
        eventId="agent_event_redacted_0001",
        publicText="safe public text",
        hiddenReasoning="private chain of thought",
    )

    with pytest.raises(ValueError):
        record.model_dump(mode="json", by_alias=True)


def test_json_record_constructed_model_dump_json_rejects_unsafe_extra_values() -> None:
    record = Gate3BJsonRecord.model_construct(
        eventId="agent_event_redacted_0001",
        publicText="safe public text",
        hiddenReasoning="private chain of thought",
    )

    with pytest.raises(ValueError):
        record.model_dump_json(by_alias=True)


def test_json_record_tampered_model_extra_model_dump_rejects_unsafe_values() -> None:
    record = Gate3BJsonRecord.model_validate(
        {"eventId": "agent_event_redacted_0001", "publicText": "safe public text"}
    )
    object.__setattr__(
        record,
        "__pydantic_extra__",
        {"eventId": "agent_event_redacted_0001", "publicText": "live tool"},
    )

    with pytest.raises(ValueError):
        record.model_dump(mode="json", by_alias=True)


def test_json_record_tampered_model_extra_model_dump_json_rejects_unsafe_values() -> None:
    record = Gate3BJsonRecord.model_validate(
        {"eventId": "agent_event_redacted_0001", "publicText": "safe public text"}
    )
    object.__setattr__(
        record,
        "__pydantic_extra__",
        {"eventId": "agent_event_redacted_0001", "publicText": "live tool"},
    )

    with pytest.raises(ValueError):
        record.model_dump_json(by_alias=True)


def test_constructed_structured_model_dump_revalidates_unsafe_scalar_fields() -> None:
    provenance = Gate3BSourceProvenance.model_construct(
        source_kind="live_duplicate_validation_metadata",
        capture_id="capture_selected_post_turn_0001",
        capture_surface="selected_bot_post_turn_bundle",
        capture_point="post_turn_redacted_duplicate",
        source_path="/data/bots/bot-123/workspace/transcript.jsonl",
        production_path_included=False,
        live_traffic_consumed=False,
    )

    with pytest.raises(ValueError):
        provenance.model_dump(mode="json", by_alias=True)

    with pytest.raises(ValueError):
        provenance.model_dump_json(by_alias=True)


def test_constructed_bundle_model_dump_rejects_nested_constructed_unsafe_records() -> None:
    valid_bundle = Gate3BLiveDuplicateBundle.model_validate(_valid_bundle_payload())
    tampered_payload = dict(object.__getattribute__(valid_bundle, "__dict__"))
    tampered_payload["transcript_entries"] = (
        Gate3BJsonRecord.model_construct(
            entryId="transcript_redacted_0001",
            role="assistant",
            publicText="safe public text",
            hiddenReasoning="private chain of thought",
        ),
    )
    tampered = Gate3BLiveDuplicateBundle.model_construct(**tampered_payload)

    with pytest.raises(ValueError):
        tampered.model_dump(mode="json", by_alias=True)

    with pytest.raises(ValueError):
        tampered.model_dump_json(by_alias=True)


def test_tool_result_copy_construct_and_dump_remain_recorded_metadata_only() -> None:
    tool_result = Gate3BRecordedToolResult.model_validate(
        _valid_bundle_payload()["recordedToolResults"][0]  # type: ignore[index]
    )

    with pytest.raises(ValidationError):
        tool_result.model_copy(update={"status": "live"})

    with pytest.raises(ValidationError):
        tool_result.model_copy(update={"dispatchedLive": True})

    constructed = Gate3BRecordedToolResult.model_construct(
        tool_call_id="tool_call_redacted_0001",
        tool_name="search.readonly",
        status="live",
        output_metadata={},
        dispatched_live=True,
    )

    dumped = constructed.model_dump(mode="json", by_alias=True)
    assert dumped["status"] == "recorded"
    assert dumped["dispatchedLive"] is False


@pytest.mark.parametrize(
    "output_metadata",
    (
        {"executionSurface": {"shellExecuted": True}},
        {"executionSurface": {"codeExecuted": True}},
        {"executionSurface": {"liveToolExecuted": True}},
        {"executionSurface": {"packageManagerExecuted": True}},
        {"executionSurface": {"scriptExecuted": True}},
        {"executionSurface": {"externalSideEffects": True}},
        {"commandEvidence": "shell executed package manager command"},
        {"scriptEvidence": "code runner executed a script"},
    ),
)
def test_execution_surface_rejects_positive_execution_or_side_effect_claims(
    output_metadata: dict[str, object],
) -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = output_metadata  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


@pytest.mark.parametrize(
    "output_metadata",
    (
        {"shellRan": True},
        {"commandRan": True},
        {"scriptRan": True},
        {"liveToolDispatched": True},
        {"toolSideEffect": True},
        {"toolSideEffects": True},
        {"commandRun": True},
        {"scriptRun": True},
        {"invoked": True},
        {"dispatched": True},
        {"shellInvoked": True},
        {"codeInvoked": True},
        {"packageManagerRan": True},
        {"packageManagerInvoked": True},
        {"scriptInvoked": True},
        {"scriptDispatched": True},
        {"commandInvoked": True},
        {"commandDispatched": True},
        {"toolInvoked": True},
        {"toolDispatched": True},
        {"commandEvidence": "shell command ran successfully"},
        {"toolEvidence": "live tool dispatched"},
    ),
)
def test_execution_surface_rejects_positive_execution_aliases(
    output_metadata: dict[str, object],
) -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = output_metadata  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


def test_execution_surface_allows_recorded_false_execution_aliases() -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = {  # type: ignore[index]
        "shellRan": False,
        "commandRan": False,
        "scriptRan": False,
        "liveToolDispatched": False,
        "toolSideEffect": False,
        "toolSideEffects": False,
        "commandRun": False,
        "scriptRun": False,
        "invoked": False,
        "dispatched": False,
        "shellInvoked": False,
        "codeInvoked": False,
        "packageManagerRan": False,
        "packageManagerInvoked": False,
        "scriptInvoked": False,
        "scriptDispatched": False,
        "commandInvoked": False,
        "commandDispatched": False,
        "toolInvoked": False,
        "toolDispatched": False,
    }

    bundle = Gate3BLiveDuplicateBundle.model_validate(payload)

    assert bundle.recorded_tool_results[0].output_metadata["shellRan"] is False


def test_typed_false_schema_fields_remain_allowed_in_parent_scopes() -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = {  # type: ignore[index]
        "executionSurface": {
            "declaredSurface": "recorded_metadata_only",
            "shellExecuted": False,
            "shellInvoked": False,
            "codeInvoked": False,
            "packageManagerRan": False,
            "packageManagerInvoked": False,
            "scriptInvoked": False,
            "scriptDispatched": False,
            "commandInvoked": False,
            "commandDispatched": False,
            "toolInvoked": False,
            "toolDispatched": False,
        }
    }

    bundle = Gate3BLiveDuplicateBundle.model_validate(payload)

    assert bundle.source_provenance.live_traffic_consumed is False
    assert bundle.attachment_flags.live_tool_attached is False
    assert bundle.evidence_audit_metadata.as_dict()["externalAckIncluded"] is False
    assert (
        bundle.recorded_tool_results[0].output_metadata["executionSurface"]["shellInvoked"]
        is False
    )


@pytest.mark.parametrize(
    "declared_surface",
    (
        "live_tool",
        "liveTool",
        "shell_execution",
        "shellExecution",
        "code_execution",
        "package_manager",
        "script_run",
        "command_run",
    ),
)
def test_execution_surface_declared_surface_rejects_live_execution_surfaces(
    declared_surface: str,
) -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = {  # type: ignore[index]
        "executionSurface": {"declaredSurface": declared_surface}
    }

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


@pytest.mark.parametrize(
    "output_metadata",
    (
        {"executionSurface": "shell"},
        {"declaredSurface": "shell_execution"},
        {"executionSurface": {"declaredSurface": "shell_run"}},
        {"executionSurface": {"declaredSurface": "command_run"}},
        {"executionSurface": {"declaredSurface": "liveToolRan"}},
        {"executionEvidence": "shell_run"},
        {"executionEvidence": "command_run"},
        {"executionEvidence": "liveToolRan"},
    ),
)
def test_execution_surface_rejects_malformed_and_compact_execution_values(
    output_metadata: dict[str, object],
) -> None:
    payload = _valid_bundle_payload()
    tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
    tool_result["outputMetadata"] = output_metadata  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


def test_json_record_extra_mutation_cannot_dump_unsafe_values() -> None:
    record = Gate3BJsonRecord.model_validate(
        {"eventId": "agent_event_redacted_0001", "publicText": "safe public text"}
    )

    with pytest.raises(TypeError):
        record.model_extra["publicText"] = "private reasoning"  # type: ignore[index]

    assert record.as_dict() == {
        "eventId": "agent_event_redacted_0001",
        "publicText": "safe public text",
    }


def test_tool_result_output_metadata_mutation_cannot_dump_unsafe_values() -> None:
    tool_result = Gate3BRecordedToolResult.model_validate(
        _valid_bundle_payload()["recordedToolResults"][0]  # type: ignore[index]
    )

    with pytest.raises(TypeError):
        tool_result.output_metadata["executionSurface"] = {  # type: ignore[index]
            "declaredSurface": "shell_execution"
        }

    dumped = tool_result.model_dump(mode="json", by_alias=True)
    assert dumped["outputMetadata"]["executionSurface"]["declaredSurface"] == (
        "recorded_metadata_only"
    )


def test_loader_rejects_path_escape_before_opening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    escaped = tmp_path / "escaped.json"
    escaped.write_text(json.dumps(_valid_bundle_payload()), encoding="utf-8")

    def fail_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("loader attempted to open rejected path")

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(ValueError, match="bundle_root"):
        load_gate3b_live_duplicate_bundle("../escaped.json", bundle_root=root)


@pytest.mark.parametrize(
    "path",
    (
        "/data/bots/bot-123/live-duplicate.json",
        "/workspace/bot-123/live-duplicate.json",
        "/var/lib/kubelet/pods/bot-123/live-duplicate.json",
    ),
)
def test_loader_rejects_production_like_paths_before_opening(
    path: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("loader attempted to open rejected path")

    monkeypatch.setattr(Path, "open", fail_open)

    with pytest.raises(ValueError, match="local-only"):
        load_gate3b_live_duplicate_bundle(path)


def test_validation_errors_hide_raw_secret_values() -> None:
    payload = _valid_bundle_payload()
    raw_secret = "sk-abcdefghijklmnop"
    payload["OPENAI_API_KEY"] = raw_secret

    with pytest.raises(ValidationError) as exc_info:
        Gate3BLiveDuplicateBundle.model_validate(payload)

    assert raw_secret not in str(exc_info.value)


@pytest.mark.parametrize(
    ("location", "credential_key"),
    (
        ("agentEvents", "openaiApiKey"),
        ("agentEvents", "OPENAI_API_KEY"),
        ("agentEvents", "xApiKey"),
        ("agentEvents", "nested_api_key"),
        ("agentEvents", "nestedApiKey"),
        ("transcriptEntries", "xAccessKey"),
        ("transcriptEntries", "nested_access_key"),
        ("transcriptEntries", "nestedClientSecret"),
        ("recordedToolResults.outputMetadata", "providerKey"),
        ("recordedToolResults.outputMetadata", "service_key"),
    ),
)
def test_flexible_records_reject_prefixed_compound_credential_keys(
    location: str,
    credential_key: str,
) -> None:
    payload = _valid_bundle_payload()
    if location == "agentEvents":
        payload["agentEvents"][0][credential_key] = "redacted"  # type: ignore[index]
    elif location == "transcriptEntries":
        payload["transcriptEntries"][0][credential_key] = "redacted"  # type: ignore[index]
    else:
        tool_result = payload["recordedToolResults"][0]  # type: ignore[index]
        tool_result["outputMetadata"][credential_key] = "redacted"  # type: ignore[index]

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)


def test_positive_production_authority_flags_reject() -> None:
    payload = _valid_bundle_payload()
    authority = deepcopy(payload["productionAuthorityFlags"])
    authority["canDelayTypescriptResponse"] = True  # type: ignore[index]
    payload["productionAuthorityFlags"] = authority

    with pytest.raises(ValidationError):
        Gate3BLiveDuplicateBundle.model_validate(payload)
