from __future__ import annotations

from datetime import UTC, datetime
import json
import subprocess
import sys

import pytest
from pydantic import ValidationError

from openmagi_core_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64
POLICY_DIGEST = "sha256:" + "d" * 64
ARTIFACT_REF = "artifact:" + "a" * 16


class ForgeableRenderer:
    openmagi_local_fake_provider = True

    def verify_render(self, request: object) -> dict[str, object]:
        _ = request
        return {
            "status": "ok",
            "renderOutputDigest": DIGEST_B,
            "renderPreviewRef": "render-preview:summary",
            "rendererVersionDigest": DIGEST_C,
        }


def _fake_renderer(**overrides: object) -> object:
    from openmagi_core_agent.artifacts.render_verification import (
        LocalFakeRenderVerificationProvider,
    )

    data = {
        "status": "ok",
        "renderOutputDigest": DIGEST_B,
        "renderPreviewRef": "render-preview:summary",
        "rendererVersionDigest": DIGEST_C,
        "raw" + "Output": "/Users/private " + "Bearer unsafe",
    }
    data.update(overrides)
    return LocalFakeRenderVerificationProvider(data)


def _render_request(**overrides: object) -> object:
    from openmagi_core_agent.artifacts.render_verification import ArtifactRenderRequest

    data = {
        "requestId": "render-request:1",
        "artifactId": "artifact:report",
        "artifactRef": "artifact:report-ref",
        "contentDigest": DIGEST_A,
        "renderFormat": "pdf",
        "rendererRef": "renderer:local-fake",
        "policySnapshotDigest": POLICY_DIGEST,
        "metadata": {"safeRef": "artifact:report"},
    }
    data.update(overrides)
    return ArtifactRenderRequest.model_validate(data)


def _delivery_request(**overrides: object) -> object:
    from openmagi_core_agent.artifacts.delivery_receipts import ArtifactDeliveryReceiptRequest

    data = {
        "requestId": "delivery-request:1",
        "artifactId": "artifact:report",
        "artifactRef": ARTIFACT_REF,
        "contentDigest": DIGEST_A,
        "operation": "file.deliver",
        "channel": {"type": "web", "channelId": "channel:web-1"},
        "renderReceiptDigest": DIGEST_B,
        "policySnapshotDigest": POLICY_DIGEST,
        "metadata": {"safeRef": "delivery:report"},
    }
    data.update(overrides)
    return ArtifactDeliveryReceiptRequest.model_validate(data)


def _channel_receipt(
    *,
    request_id: str = "delivery-request:1",
    artifact_refs: tuple[str, ...] = (ARTIFACT_REF,),
    provider_message_id: str | None = "message:1",
    status: str = "sent",
    channel: ChannelRef | None = None,
) -> ChannelDeliveryReceipt:
    return ChannelDeliveryReceipt(
        receiptId="channel-receipt:1",
        requestId=request_id,
        channel=channel or ChannelRef(type="web", channelId="channel:web-1"),
        status=status,
        providerMessageId=provider_message_id,
        artifactRefs=artifact_refs,
        fileRefs=(),
    )


class FakeFileArtifactProvider:
    openmagi_local_fake_provider = True

    def write_artifact(self, request: object) -> dict[str, object]:
        _ = request
        return {
            "status": "ok",
            "artifactRef": ARTIFACT_REF,
            "contentDigest": DIGEST_A,
            "receiptId": "provider-receipt:local-fake",
        }


class FakeFileChannelProvider:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *,
        provider_message_id: str | None = "message:1",
        status: str = "sent",
        artifact_refs: tuple[str, ...] | None = None,
        channel: ChannelRef | None = None,
    ) -> None:
        self.provider_message_id = provider_message_id
        self.status = status
        self.artifact_refs = artifact_refs
        self.channel = channel

    def deliver(self, request: object) -> ChannelDeliveryReceipt:
        request_id = getattr(request, "request_id")
        channel = self.channel or getattr(request, "channel")
        artifact_refs = self.artifact_refs or getattr(request, "artifact_refs")
        return _channel_receipt(
            request_id=str(request_id),
            artifact_refs=tuple(str(item) for item in artifact_refs),
            provider_message_id=self.provider_message_id,
            status=self.status,
            channel=channel,
        )


def _file_delivery_decision(
    *,
    request_id: str = "delivery-request:1",
    channel: ChannelRef | None = None,
    artifact_refs: tuple[str, ...] = (ARTIFACT_REF,),
    provider_message_id: str | None = "message:1",
    status: str = "sent",
) -> object:
    from openmagi_core_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
        FileDeliveryRequest,
    )

    request = FileDeliveryRequest(
        operation="file.deliver",
        requestId=request_id,
        sessionKey="session:delivery",
        channel=channel or ChannelRef(type="web", channelId="channel:web-1"),
        artifactRefs=artifact_refs,
        fileRefs=(),
        filename="report.pdf",
        mimeType="application/pdf",
        contentDigest=DIGEST_A,
        metadata={"safeRef": "delivery:report"},
    )
    return FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        request,
        artifact_provider=FakeFileArtifactProvider(),
        channel_provider=FakeFileChannelProvider(
            provider_message_id=provider_message_id,
            status=status,
            artifact_refs=artifact_refs,
            channel=channel,
        ),
    )


def test_render_verification_default_off_calls_no_renderer() -> None:
    from openmagi_core_agent.artifacts.render_verification import (
        RenderVerificationBoundary,
        RenderVerificationConfig,
    )

    renderer = _fake_renderer()
    receipt = RenderVerificationBoundary(RenderVerificationConfig()).verify(
        _render_request(),
        renderer=renderer,
    )

    assert receipt.status == "blocked"
    assert receipt.reason_codes == ("render_verification_disabled",)
    assert renderer.calls == []
    assert receipt.authority_flags.model_dump(by_alias=True) == {
        "adkArtifactServiceAttached": False,
        "rendererExecuted": False,
        "productionStorageWritten": False,
        "userVisibleRenderAllowed": False,
        "routeAttached": False,
    }


def test_local_fake_render_verification_records_digest_only_receipt() -> None:
    from openmagi_core_agent.artifacts.render_verification import (
        RenderVerificationBoundary,
        RenderVerificationConfig,
    )

    receipt = RenderVerificationBoundary(
        RenderVerificationConfig(enabled=True, localFakeRendererEnabled=True)
    ).verify(
        _render_request(),
        renderer=_fake_renderer(),
        now=datetime(2026, 5, 26, tzinfo=UTC),
    )
    projection = receipt.public_projection()
    encoded = json.dumps(projection, sort_keys=True)
    artifact_index = receipt.to_artifact_index_record(
        blob_ref="artifact://filesystem/" + DIGEST_A,
        size_bytes=1234,
    )

    assert receipt.status == "verified_local_fake"
    assert projection["renderReceiptDigest"].startswith("sha256:")
    assert projection["renderOutputDigest"] == DIGEST_B
    assert projection["verifiedAt"] == "2026-05-26T00:00:00Z"
    assert artifact_index.render_receipt_digest == receipt.render_receipt_digest
    assert artifact_index.blob_ref == "artifact://filesystem/" + DIGEST_A
    assert "/Users" not in encoded
    assert "Bearer" not in encoded
    assert "raw" + "Output" not in encoded
    assert projection["authorityFlags"]["rendererExecuted"] is False


def test_render_verification_blocks_untrusted_or_missing_renderer() -> None:
    from openmagi_core_agent.artifacts.render_verification import (
        RenderVerificationBoundary,
        RenderVerificationConfig,
    )

    boundary = RenderVerificationBoundary(
        RenderVerificationConfig(enabled=True, localFakeRendererEnabled=True)
    )

    missing = boundary.verify(_render_request())
    untrusted = boundary.verify(_render_request(), renderer=ForgeableRenderer())

    assert missing.status == "blocked"
    assert "local_fake_renderer_required" in missing.reason_codes
    assert untrusted.status == "blocked"
    assert "local_fake_renderer_untrusted" in untrusted.reason_codes


def test_render_verification_blocks_malformed_local_fake_receipts() -> None:
    from openmagi_core_agent.artifacts.render_verification import (
        RenderVerificationBoundary,
        RenderVerificationConfig,
    )

    receipt = RenderVerificationBoundary(
        RenderVerificationConfig(enabled=True, localFakeRendererEnabled=True)
    ).verify(
        _render_request(),
        renderer=_fake_renderer(
            renderOutputDigest="not-a-digest",
            renderPreviewRef="/Users/private/preview",
        ),
    )

    assert receipt.status == "blocked"
    assert receipt.reason_codes == ("renderer_receipt_invalid",)
    with pytest.raises(ValueError, match="artifact index requires verified render receipt"):
        receipt.to_artifact_index_record(
            blob_ref="artifact://filesystem/" + DIGEST_A,
            size_bytes=1234,
        )


def test_render_verification_rejects_private_refs_and_inline_blob_metadata() -> None:
    from openmagi_core_agent.artifacts.render_verification import (
        RenderVerificationReceipt,
    )

    with pytest.raises(ValidationError):
        _render_request(artifactRef="/Users/private/report.pdf")
    with pytest.raises(ValidationError):
        _render_request(metadata={"raw" + "Blob": "unsafe"})
    with pytest.raises(ValidationError):
        RenderVerificationReceipt(
            requestId="render-receipt:forged",
            artifactId="artifact:report",
            artifactRef="artifact:report-ref",
            contentDigest=DIGEST_A,
            renderFormat="pdf",
            rendererRef="renderer:local-fake",
            rendererVersionDigest=DIGEST_C,
            renderOutputDigest=DIGEST_B,
            renderPreviewRef="render-preview:summary",
            status="verified_local_fake",
            reasonCodes=("render_verified_local_fake",),
            verifiedAt=datetime(2026, 5, 26, tzinfo=UTC),
            policySnapshotDigest=POLICY_DIGEST,
            rawRenderOutput="unsafe",
        )
    with pytest.raises(ValidationError, match="verified render receipt requires concrete digests"):
        RenderVerificationReceipt(
            requestId="render-receipt:forged-zero-request",
            artifactId="artifact:report",
            artifactRef="artifact:report-ref",
            contentDigest=DIGEST_A,
            renderFormat="pdf",
            rendererRef="renderer:local-fake",
            rendererVersionDigest=DIGEST_C,
            renderOutputDigest=DIGEST_B,
            renderPreviewRef="render-preview:summary",
            status="verified_local_fake",
            reasonCodes=("render_verified_local_fake",),
            verifiedAt=datetime(2026, 5, 26, tzinfo=UTC),
            policySnapshotDigest=POLICY_DIGEST,
        )


def test_delivery_receipt_default_off_blocks_delivery_claim_without_receipt() -> None:
    from openmagi_core_agent.artifacts.delivery_receipts import (
        ArtifactDeliveryReceiptBoundary,
        ArtifactDeliveryReceiptConfig,
    )

    receipt = ArtifactDeliveryReceiptBoundary(ArtifactDeliveryReceiptConfig()).record(
        _delivery_request(),
        channel_receipt=_channel_receipt(),
    )

    assert receipt.status == "blocked"
    assert receipt.delivery_claim_allowed is False
    assert receipt.reason_codes == ("artifact_delivery_receipts_disabled",)
    assert receipt.authority_flags.model_dump(by_alias=True) == {
        "adkArtifactServiceAttached": False,
        "channelDeliveryPerformed": False,
        "productionStorageWritten": False,
        "productionChannelWrite": False,
        "userVisibleDeliveryAllowed": False,
        "routeAttached": False,
    }


def test_local_fake_delivery_receipt_allows_claim_only_with_matching_channel_receipt() -> None:
    from openmagi_core_agent.artifacts.delivery_receipts import (
        ArtifactDeliveryReceiptBoundary,
        ArtifactDeliveryReceiptConfig,
    )

    boundary = ArtifactDeliveryReceiptBoundary(
        ArtifactDeliveryReceiptConfig(enabled=True, localFakeReceiptIndexEnabled=True)
    )
    receipt = boundary.record(
        _delivery_request(),
        delivery_decision=_file_delivery_decision(),
        now=datetime(2026, 5, 26, tzinfo=UTC),
    )
    projection = receipt.public_projection()
    durable = receipt.to_durable_metadata_record(record_id="semantic-delivery")
    encoded = json.dumps(durable.storage_payload(), sort_keys=True)

    assert receipt.status == "recorded_local_fake"
    assert receipt.delivery_claim_allowed is True
    assert projection["deliveryReceiptDigest"].startswith("sha256:")
    assert projection["channelReceiptDigest"].startswith("sha256:")
    assert projection["deliveredAt"] == "2026-05-26T00:00:00Z"
    assert durable.record_id == "delivery-ref:" + receipt.delivery_receipt_digest
    assert "semantic-delivery" not in encoded
    assert "providerMessageId" not in encoded
    assert "message:1" not in encoded
    assert ARTIFACT_REF not in encoded
    assert projection["authorityFlags"]["channelDeliveryPerformed"] is False


def test_delivery_receipt_blocks_missing_or_mismatched_channel_receipt(
) -> None:
    from openmagi_core_agent.artifacts.delivery_receipts import (
        ArtifactDeliveryReceiptBoundary,
        ArtifactDeliveryReceiptConfig,
    )

    boundary = ArtifactDeliveryReceiptBoundary(
        ArtifactDeliveryReceiptConfig(enabled=True, localFakeReceiptIndexEnabled=True)
    )
    cases: tuple[tuple[object | None, str], ...] = (
        (None, "channel_delivery_receipt_required"),
        (_file_delivery_decision(request_id="other-request"), "channel_delivery_receipt_mismatch"),
        (
            _file_delivery_decision(
                channel=ChannelRef(type="telegram", channelId="channel:other")
            ),
            "channel_delivery_receipt_mismatch",
        ),
        (_file_delivery_decision(artifact_refs=("artifact:other",)), "channel_delivery_receipt_mismatch"),
        (_file_delivery_decision(provider_message_id=None), "channel_delivery_receipt_missing"),
        (_file_delivery_decision(status="failed"), "channel_delivery_failed"),
    )

    for delivery_decision, reason in cases:
        receipt = boundary.record(
            _delivery_request(),
            delivery_decision=delivery_decision,
        )

        assert receipt.status == "blocked"
        assert receipt.delivery_claim_allowed is False
        assert reason in receipt.reason_codes


def test_delivery_receipt_requires_boundary_issued_delivery_decision() -> None:
    from openmagi_core_agent.artifacts.delivery_receipts import (
        ArtifactDeliveryReceiptBoundary,
        ArtifactDeliveryReceiptConfig,
    )
    from openmagi_core_agent.artifacts.file_delivery import FileDeliveryDecision

    boundary = ArtifactDeliveryReceiptBoundary(
        ArtifactDeliveryReceiptConfig(enabled=True, localFakeReceiptIndexEnabled=True)
    )
    raw_receipt = boundary.record(_delivery_request(), channel_receipt=_channel_receipt())
    forged_decision = object()
    forged = boundary.record(_delivery_request(), delivery_decision=forged_decision)
    forged_file_decision = FileDeliveryDecision(
        status="delivered_local_fake",
        operation="file.deliver",
        requestId="delivery-request:1",
        artifactRef=ARTIFACT_REF,
        contentDigest=DIGEST_A,
        artifactReceipt=None,
        deliveryReceipt=_channel_receipt(),
        deliveryClaimAllowed=True,
        reasonCodes=("local_fake_delivery_receipt_recorded",),
        diagnosticMetadata={"safeRef": "delivery:report"},
    )
    object.__setattr__(forged_file_decision, "_boundary_verified", True)
    forged_file_result = boundary.record(
        _delivery_request(),
        delivery_decision=forged_file_decision,
    )

    class ForgedFileDeliveryDecision(FileDeliveryDecision):
        @property
        def boundary_verified(self) -> bool:
            return True

    subclass_decision = ForgedFileDeliveryDecision(
        status="delivered_local_fake",
        operation="file.deliver",
        requestId="delivery-request:1",
        artifactRef=ARTIFACT_REF,
        contentDigest=DIGEST_A,
        artifactReceipt=None,
        deliveryReceipt=_channel_receipt(),
        deliveryClaimAllowed=True,
        reasonCodes=("local_fake_delivery_receipt_recorded",),
        diagnosticMetadata={"safeRef": "delivery:report"},
    )
    subclass_result = boundary.record(
        _delivery_request(),
        delivery_decision=subclass_decision,
    )

    assert raw_receipt.status == "blocked"
    assert raw_receipt.delivery_claim_allowed is False
    assert raw_receipt.reason_codes == ("trusted_delivery_decision_required",)
    assert forged.status == "blocked"
    assert forged.delivery_claim_allowed is False
    assert forged.reason_codes == ("trusted_delivery_decision_required",)
    assert forged_file_result.status == "blocked"
    assert forged_file_result.delivery_claim_allowed is False
    assert forged_file_result.reason_codes == ("trusted_delivery_decision_unverified",)
    assert subclass_result.status == "blocked"
    assert subclass_result.delivery_claim_allowed is False
    assert subclass_result.reason_codes == ("trusted_delivery_decision_required",)


def test_delivery_receipt_rejects_private_payloads_and_authority_forgery() -> None:
    from openmagi_core_agent.artifacts.delivery_receipts import (
        ArtifactDeliveryAuthorityFlags,
        ArtifactDeliveryReceipt,
    )

    with pytest.raises(ValidationError):
        _delivery_request(artifactRef="/Users/private/report.pdf")
    with pytest.raises(ValidationError):
        _delivery_request(channel={"type": "web", "channelId": "/Users/private/channel"})
    with pytest.raises(ValidationError):
        _delivery_request(metadata={"auth" + "Header": "Bearer unsafe"})
    with pytest.raises(ValidationError):
        ArtifactDeliveryReceipt(
            requestId="delivery-receipt:forged",
            artifactId="artifact:report",
            artifactRef="artifact:report-ref",
            contentDigest=DIGEST_A,
            operation="file.deliver",
            channel={"type": "web", "channelId": "channel:web-1"},
            status="recorded_local_fake",
            deliveryClaimAllowed=True,
            reasonCodes=("delivery_receipt_recorded_local_fake",),
            channelReceiptDigest=DIGEST_B,
            renderReceiptDigest=DIGEST_C,
            deliveredAt=datetime(2026, 5, 26, tzinfo=UTC),
            policySnapshotDigest=POLICY_DIGEST,
            rawDeliveryPayload="unsafe",
        )

    flags = ArtifactDeliveryAuthorityFlags.model_construct(
        channelDeliveryPerformed=True,
        productionChannelWrite=True,
        userVisibleDeliveryAllowed=True,
    )
    assert set(flags.model_dump(by_alias=True).values()) == {False}
    assert set(flags.model_copy(update={"productionStorageWritten": True}).model_dump(by_alias=True).values()) == {False}


def test_delivery_claim_cannot_be_allowed_when_status_is_blocked() -> None:
    from openmagi_core_agent.artifacts.delivery_receipts import ArtifactDeliveryReceipt

    with pytest.raises(ValidationError, match="recorded receipt requires channel receipt"):
        ArtifactDeliveryReceipt(
            requestId="delivery-receipt:recorded-missing-channel",
            artifactId="artifact:report",
            artifactRef="artifact:report-ref",
            contentDigest=DIGEST_A,
            operation="file.deliver",
            channel={"type": "web", "channelId": "channel:web-1"},
            status="recorded_local_fake",
            deliveryClaimAllowed=False,
            reasonCodes=("delivery_receipt_recorded_local_fake",),
            channelReceiptDigest=None,
            renderReceiptDigest=DIGEST_C,
            deliveredAt=datetime(2026, 5, 26, tzinfo=UTC),
            policySnapshotDigest=POLICY_DIGEST,
        )
    with pytest.raises(ValidationError, match="recorded receipt requires concrete request digest"):
        ArtifactDeliveryReceipt(
            requestId="delivery-receipt:recorded-zero-request",
            artifactId="artifact:report",
            artifactRef="artifact:report-ref",
            contentDigest=DIGEST_A,
            operation="file.deliver",
            channel={"type": "web", "channelId": "channel:web-1"},
            status="recorded_local_fake",
            deliveryClaimAllowed=False,
            reasonCodes=("delivery_receipt_recorded_local_fake",),
            channelReceiptDigest=DIGEST_B,
            renderReceiptDigest=DIGEST_C,
            deliveredAt=datetime(2026, 5, 26, tzinfo=UTC),
            policySnapshotDigest=POLICY_DIGEST,
        )
    with pytest.raises(ValidationError, match="claim requires recorded receipt"):
        ArtifactDeliveryReceipt(
            requestId="delivery-receipt:blocked-claim",
            artifactId="artifact:report",
            artifactRef="artifact:report-ref",
            contentDigest=DIGEST_A,
            operation="file.deliver",
            channel={"type": "web", "channelId": "channel:web-1"},
            status="blocked",
            deliveryClaimAllowed=True,
            reasonCodes=("channel_delivery_receipt_required",),
            channelReceiptDigest=None,
            renderReceiptDigest=DIGEST_C,
            deliveredAt=datetime(2026, 5, 26, tzinfo=UTC),
            policySnapshotDigest=POLICY_DIGEST,
        )


def test_artifact_delivery_import_boundary_has_no_live_provider_imports() -> None:
    script = """
import sys
import openmagi_core_agent.artifacts.render_verification
import openmagi_core_agent.artifacts.delivery_receipts
for name in (
    'stripe',
    'supabase',
    'psycopg',
    'httpx',
    'requests',
    'kubernetes',
    'google.adk.runners',
):
    if name in sys.modules:
        raise SystemExit(name)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
