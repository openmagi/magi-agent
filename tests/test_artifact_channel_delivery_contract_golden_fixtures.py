from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from openmagi_core_agent.shadow.artifact_channel_delivery_contract import (
    ArtifactChannelDeliveryAttachmentFlags,
    ArtifactChannelDeliveryContractFixture,
    ArtifactChannelTsMetadataParityFixture,
    TsOutputArtifactMetadata,
    load_artifact_channel_delivery_contract_fixture,
    load_artifact_channel_ts_metadata_parity_fixture,
    project_artifact_channel_delivery_contract_fixture,
    project_artifact_channel_ts_metadata_parity_fixture,
)


FIXTURES = Path(__file__).parent / "fixtures" / "artifact_channel_delivery"


def test_artifact_channel_delivery_fixture_covers_artifact_and_delivery_boundaries() -> None:
    fixture = load_artifact_channel_delivery_contract_fixture(
        "policy_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_artifact_channel_delivery_contract_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "artifact_channel_delivery_matrix_0001"
    assert projection.local_diagnostic is True
    assert projection.case_order == (
        "artifact_index_l0_declared_output",
        "artifact_index_l1_rendered_metadata",
        "artifact_index_l2_delivery_receipt_metadata",
        "document_render_pending_blocks_delivery_claim",
        "spreadsheet_output_validation_metadata",
        "web_file_delivery_pending_request",
        "app_delivery_retrying_transient_failure",
        "discord_delivery_failed_explicit_error",
        "telegram_delivery_metadata_excluded",
        "child_artifact_handoff_metadata",
    )
    assert projection.by_index_level == {"L0": 2, "L1": 3, "L2": 5}
    assert projection.by_delivery_state == {
        "not_requested": 5,
        "pending": 1,
        "retrying": 1,
        "sent": 1,
        "failed": 1,
        "skipped": 1,
    }
    assert projection.by_channel == {"none": 5, "web": 2, "app": 1, "discord": 1, "telegram": 1}
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}
    assert projection.no_live_delivery is True

    declared = cases["artifact_index_l0_declared_output"]
    assert declared.artifact_index_level == "L0"
    assert declared.artifact_service_owner == "adk-artifact-service"
    assert declared.openmagi_index_owner == "openmagi-artifact-index"
    assert declared.artifact_written is False
    assert declared.output_path_preview == "outputs/report-draft.md"

    rendered = cases["artifact_index_l1_rendered_metadata"]
    assert rendered.artifact_index_level == "L1"
    assert rendered.render_verification_required is True
    assert rendered.render_verification_passed is True
    assert rendered.delivery_claim_allowed is False

    delivered = cases["artifact_index_l2_delivery_receipt_metadata"]
    assert delivered.artifact_index_level == "L2"
    assert delivered.delivery_state == "sent"
    assert delivered.receipt is not None
    assert delivered.receipt.status == "sent"
    assert delivered.provider_receipt_required is True
    assert projection.receipts[delivered.case_id] == {
        "receiptId": "receipt-artifact-report-1",
        "requestId": "deliver-artifact-report-1",
        "status": "sent",
        "channelType": "web",
    }

    render_pending = cases["document_render_pending_blocks_delivery_claim"]
    assert render_pending.render_verification_required is True
    assert render_pending.render_verification_passed is False
    assert render_pending.delivery_claim_allowed is False
    assert render_pending.reason_codes == ("render_verification_missing",)

    spreadsheet = cases["spreadsheet_output_validation_metadata"]
    assert spreadsheet.artifact_kind == "spreadsheet"
    assert spreadsheet.evidence_refs == ("evidence:spreadsheet-formula-recalc",)
    assert spreadsheet.delivery_state == "not_requested"

    pending_web = cases["web_file_delivery_pending_request"]
    assert pending_web.delivery_request is not None
    assert pending_web.delivery_request.channel.type == "web"
    assert pending_web.delivery_state == "pending"
    assert pending_web.provider_receipt_required is True
    assert pending_web.delivery_claim_allowed is False

    retrying_app = cases["app_delivery_retrying_transient_failure"]
    assert retrying_app.delivery_state == "retrying"
    assert retrying_app.retry_count == 1
    assert retrying_app.transient_retry_allowed is True
    assert retrying_app.delivery_claim_allowed is False

    failed_discord = cases["discord_delivery_failed_explicit_error"]
    assert failed_discord.delivery_state == "failed"
    assert failed_discord.receipt is not None
    assert failed_discord.receipt.status == "failed"
    assert failed_discord.reason_codes == ("provider_delivery_failed",)

    telegram = cases["telegram_delivery_metadata_excluded"]
    assert telegram.channel_type == "telegram"
    assert telegram.delivery_state == "skipped"
    assert telegram.telegram_polling_attached is False
    assert telegram.delivery_claim_allowed is False

    child_handoff = cases["child_artifact_handoff_metadata"]
    assert child_handoff.child_execution_id == "child-artifact-1"
    assert child_handoff.parent_execution_id == "root-artifact-1"
    assert child_handoff.delivery_state == "not_requested"
    assert child_handoff.delivery_claim_allowed is False

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "/var/lib/kubelet",
        "Bearer unsafe",
        "ghp_artifactsecret",
        "sk-artifact-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "private delivery token",
        "raw artifact bytes",
        "hidden reasoning",
        "adkArtifactServiceCalled\": true",
        "artifactWritten\": true",
        "channelDeliveryPerformed\": true",
        "telegramPollingAttached\": true",
        "productionStorageWritten\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


def test_ts_metadata_parity_fixture_covers_registry_delivery_and_channel_gaps() -> None:
    fixture = load_artifact_channel_ts_metadata_parity_fixture(
        "ts_metadata_parity_matrix.json",
        fixture_root=FIXTURES,
    )

    projection = project_artifact_channel_ts_metadata_parity_fixture(fixture)
    cases = {case.case_id: case for case in fixture.cases}

    assert projection.fixture_id == "artifact_channel_ts_metadata_parity_0001"
    assert projection.local_diagnostic is True
    assert projection.no_live_delivery is True
    assert projection.case_order == (
        "output_registry_document_metadata",
        "file_deliver_chat_attachment_marker",
        "file_send_direct_provider_receipt",
        "file_deliver_kb_write_receipt",
        "file_deliver_path_escape_rejected",
        "file_send_missing_provider_receipt",
        "source_delivery_channel_identity",
        "web_app_file_send_unsupported_metadata",
        "file_deliver_both_chat_and_kb_receipts",
    )
    assert projection.by_tool == {
        "ChannelDispatcher": 1,
        "FileDeliver": 4,
        "FileSend": 3,
        "OutputArtifactRegistry": 1,
    }
    assert projection.by_delivery_ack == {
        "attachment_marker": 2,
        "kb_write_receipt": 2,
        "provider_message_receipt": 1,
    }
    assert projection.by_delivery_record_target == {"chat": 5, "kb": 2}
    assert projection.unsupported_file_send_channels == {"app": 1, "web": 1}
    assert set(projection.attachment_flags.model_dump(by_alias=True).values()) == {False}

    registry = cases["output_registry_document_metadata"]
    assert registry.output_artifact is not None
    assert registry.tool_name == "OutputArtifactRegistry"
    assert registry.output_artifact.session_key == "agent:main:app:general:metadata"
    assert registry.output_artifact.turn_id == "turn-ts-metadata-001"
    assert registry.output_artifact.title == "Market Brief"
    assert registry.output_artifact.format == "pdf"
    assert registry.output_artifact.filename == "market-brief.pdf"
    assert registry.output_artifact.mime_type == "application/pdf"
    assert registry.output_artifact.workspace_path_preview == "outputs/market-brief.pdf"
    assert registry.output_artifact.preview_kind == "download-only"
    assert registry.output_artifact.created_by_tool == "DocumentWrite"
    assert registry.output_artifact.source_kind == "tool"
    assert registry.artifact_provenance is not None
    assert registry.artifact_provenance.title == "Market Brief"
    assert registry.artifact_provenance.slug == "market-brief"
    assert registry.artifact_provenance.produced_by == "DocumentWrite"
    assert registry.artifact_provenance.sources == ("src_1", "artifact:prior-analysis")
    assert registry.artifact_provenance.spawn_task_id == "spawn_task_market_research"
    assert registry.artifact_provenance.imported_from_artifact_id == "child_artifact_market_brief"

    marker = cases["file_deliver_chat_attachment_marker"]
    assert marker.delivery_record is not None
    assert marker.delivery_record.target == "chat"
    assert marker.delivery_record.marker == "[attachment:00000000-0000-4000-8000-000000000001:market-brief.pdf]"
    assert marker.delivery_record.delivery_ack == "attachment_marker"
    assert marker.delivery_record.attempt_count == 1

    provider_receipt = cases["file_send_direct_provider_receipt"]
    assert provider_receipt.delivery_record is not None
    assert provider_receipt.delivery_record.external_id == "telegram:1234:987"
    assert provider_receipt.delivery_record.provider_message_id == "987"
    assert provider_receipt.delivery_record.delivery_ack == "provider_message_receipt"

    kb = cases["file_deliver_kb_write_receipt"]
    assert kb.delivery_record is not None
    assert kb.delivery_record.target == "kb"
    assert kb.delivery_record.external_id == "artifacts/market-brief.pdf"
    assert kb.delivery_record.delivery_ack == "kb_write_receipt"

    escaped = cases["file_deliver_path_escape_rejected"]
    assert escaped.delivery_record is not None
    assert escaped.delivery_record.status == "failed"
    assert escaped.delivery_record.error_message == "path escape rejected before delivery"
    assert escaped.output_artifact is not None
    assert escaped.output_artifact.workspace_path_preview == "outputs/rejected-path-escape.txt"

    missing_provider = cases["file_send_missing_provider_receipt"]
    assert missing_provider.delivery_record is not None
    assert missing_provider.delivery_record.delivery_ack is None
    assert missing_provider.delivery_record.provider_message_id is None
    assert missing_provider.delivery_claim_allowed is False

    identity = cases["source_delivery_channel_identity"]
    assert identity.source_channel is not None
    assert identity.delivery_channel is not None
    assert identity.source_channel.model_dump(by_alias=True) == {
        "type": "telegram",
        "channelId": "telegram-chat-1234",
        "messageId": "upstream-message-55",
        "userId": "telegram-user-77",
    }
    assert identity.delivery_channel.channel_id == "telegram-chat-1234"

    unsupported = cases["web_app_file_send_unsupported_metadata"]
    assert unsupported.unsupported_file_send_channels == ("web", "app")
    assert unsupported.delivery_record is None
    assert unsupported.delivery_claim_allowed is False

    both = cases["file_deliver_both_chat_and_kb_receipts"]
    assert both.requested_target == "both"
    assert both.delivery_record is None
    assert tuple(record.target for record in both.delivery_records) == ("chat", "kb")
    assert tuple(record.delivery_ack for record in both.delivery_records) == (
        "attachment_marker",
        "kb_write_receipt",
    )
    assert both.delivery_records[0].marker == (
        "[attachment:00000000-0000-4000-8000-000000000002:market-brief.pdf]"
    )
    assert both.delivery_records[1].external_id == "artifacts/market-brief-both.pdf"
    assert both.delivery_claim_allowed is True

    both_snapshot = projection.case_snapshots["file_deliver_both_chat_and_kb_receipts"]
    assert both_snapshot["requestedTarget"] == "both"
    assert both_snapshot["deliveryRecord"] is None
    assert both_snapshot["deliveryRecords"] == [
        record.model_dump(by_alias=True) for record in both.delivery_records
    ]

    projection_json = json.dumps(
        projection.model_dump(by_alias=True),
        sort_keys=True,
    )
    unsafe_fragments = (
        "/data/bots",
        "/workspace",
        "/var/lib/kubelet",
        "Bearer unsafe",
        "ghp_artifactsecret",
        "sk-artifact-secret",
        "SUPABASE_SERVICE_ROLE_KEY",
        "private delivery token",
        "raw artifact bytes",
        "hidden reasoning",
        "adkArtifactServiceCalled\": true",
        "artifactWritten\": true",
        "channelDeliveryPerformed\": true",
        "telegramPollingAttached\": true",
        "liveToolDispatched\": true",
        "routeOrApiAttached\": true",
        "productionStorageWritten\": true",
        "canaryTrafficAttached\": true",
    )
    for fragment in unsafe_fragments:
        assert fragment not in projection_json


def test_ts_output_registry_metadata_requires_title_surface() -> None:
    payload = json.loads((FIXTURES / "ts_metadata_parity_matrix.json").read_text(encoding="utf-8"))
    output_artifact = payload["cases"][0]["outputArtifact"]
    output_artifact["title"] = "Market Brief"

    parsed = TsOutputArtifactMetadata.model_validate(output_artifact)

    assert parsed.title == "Market Brief"


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(
                {"channelDeliveryPerformed": True}
            ),
            id="fixture-channel-delivery-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["attachmentFlags"].update(
                {"artifactWritten": True}
            ),
            id="case-artifact-write-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"outputPathPreview": "/data/bots/bot-secret/outputs/report.pdf"}
            ),
            id="unsafe-production-output-path",
        ),
        pytest.param(
            lambda payload: payload["cases"][3].update({"deliveryClaimAllowed": True}),
            id="delivery-claim-without-render",
        ),
        pytest.param(
            lambda payload: payload["cases"][5].pop("deliveryRequest"),
            id="pending-without-request",
        ),
        pytest.param(
            lambda payload: payload["cases"][6].update({"retryCount": 0}),
            id="retrying-without-retry-count",
        ),
        pytest.param(
            lambda payload: payload["cases"][7].pop("receipt"),
            id="failed-without-receipt",
        ),
        pytest.param(
            lambda payload: payload["cases"][8].update({"telegramPollingAttached": True}),
            id="telegram-polling-attached",
        ),
        pytest.param(
            lambda payload: payload["cases"][9].update({"parentExecutionId": None}),
            id="child-handoff-without-parent",
        ),
        pytest.param(
            lambda payload: payload["cases"][0].update(
                {"publicPreview": "safe " * 120 + "sk-live-artifactsecret"}
            ),
            id="unsafe-secret-after-preview-truncation",
        ),
    ),
)
def test_artifact_channel_delivery_contract_rejects_live_flags_and_bad_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "policy_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        ArtifactChannelDeliveryContractFixture.model_validate(payload)


@pytest.mark.parametrize(
    "mutation",
    (
        pytest.param(
            lambda payload: payload["attachmentFlags"].update(
                {"adkArtifactServiceCalled": True}
            ),
            id="fixture-adk-artifact-service-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["attachmentFlags"].update(
                {"channelDeliveryPerformed": True}
            ),
            id="case-channel-delivery-flag",
        ),
        pytest.param(
            lambda payload: payload["cases"][0]["outputArtifact"].update(
                {"workspacePathPreview": "/workspace/output-artifacts/index.json"}
            ),
            id="unsafe-workspace-path-preview",
        ),
        pytest.param(
            lambda payload: payload["cases"][2]["deliveryRecord"].update(
                {"externalId": "telegram-token:unsafe"}
            ),
            id="unsafe-provider-token",
        ),
        pytest.param(
            lambda payload: payload["cases"][1]["deliveryRecord"].update(
                {"deliveryAck": "attachment_marker", "marker": None}
            ),
            id="attachment-ack-without-marker",
        ),
        pytest.param(
            lambda payload: payload["cases"][2]["deliveryRecord"].update(
                {"deliveryAck": "provider_message_receipt", "providerMessageId": None}
            ),
            id="provider-ack-without-provider-message-id",
        ),
        pytest.param(
            lambda payload: payload["cases"][3]["deliveryRecord"].update(
                {"deliveryAck": "kb_write_receipt", "target": "chat"}
            ),
            id="kb-ack-on-chat-target",
        ),
        pytest.param(
            lambda payload: (
                payload["cases"][5]["deliveryRecord"].update({"status": "skipped"}),
                payload["cases"][5]["deliveryRecord"].pop("deliveredAt"),
            ),
            id="delivery-record-skipped-status",
        ),
        pytest.param(
            lambda payload: payload["cases"][7].update(
                {"deliveryClaimAllowed": True}
            ),
            id="unsupported-web-app-claim",
        ),
        pytest.param(
            lambda payload: payload["cases"][8]["deliveryRecords"][0].update(
                {"target": "both"}
            ),
            id="both-target-not-valid-delivery-record-target",
        ),
    ),
)
def test_ts_metadata_parity_fixture_rejects_live_flags_and_unsafe_metadata(
    mutation: Callable[[dict[str, object]], object],
) -> None:
    payload = json.loads((FIXTURES / "ts_metadata_parity_matrix.json").read_text(encoding="utf-8"))
    mutation(payload)

    with pytest.raises(ValidationError):
        ArtifactChannelTsMetadataParityFixture.model_validate(payload)


def test_artifact_channel_delivery_flags_remain_false_under_construct_and_copy() -> None:
    constructed = ArtifactChannelDeliveryAttachmentFlags.model_construct(
        adkArtifactServiceCalled=True,
        artifactWritten=True,
        channelDeliveryPerformed=True,
        telegramPollingAttached=True,
    )
    assert set(constructed.model_dump(by_alias=True).values()) == {False}

    with pytest.raises(ValidationError):
        constructed.model_copy(update={"channelDeliveryPerformed": True})


def test_artifact_channel_delivery_import_boundary_stays_runtime_free() -> None:
    code = """
import sys
from pathlib import Path

from openmagi_core_agent.shadow.artifact_channel_delivery_contract import (
    load_artifact_channel_delivery_contract_fixture,
    project_artifact_channel_delivery_contract_fixture,
)

fixture_root = Path('tests/fixtures/artifact_channel_delivery')
fixture = load_artifact_channel_delivery_contract_fixture('policy_matrix.json', fixture_root=fixture_root)
project_artifact_channel_delivery_contract_fixture(fixture)

forbidden = (
    'google.adk.runners',
    'google.adk.artifacts',
    'openmagi_core_agent.adk_bridge.local_runner',
    'openmagi_core_agent.adk_bridge.runner_adapter',
    'openmagi_core_agent.tools.dispatcher',
    'openmagi_core_agent.tools.registry',
    'openmagi_core_agent.channels.delivery',
    'openmagi_core_agent.telegram',
    'openmagi_core_agent.routes',
    'openmagi_core_agent.proxy',
    'openmagi_core_agent.dashboard',
    'openmagi_core_agent.db',
    'openmagi_core_agent.k8s',
    'openmagi_core_agent.canary',
)
loaded = sorted(name for name in forbidden if name in sys.modules)
if loaded:
    raise SystemExit(f'forbidden imports loaded: {loaded}')
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
