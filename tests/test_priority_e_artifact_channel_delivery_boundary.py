from __future__ import annotations

import subprocess
import sys
from typing import Any

import pytest
from pydantic import ValidationError

from openmagi_core_agent.artifacts.delivery_boundary import (
    ArtifactChannelDeliveryBoundary,
    ArtifactChannelDeliveryConfig,
    ArtifactChannelDeliveryRequest,
    ArtifactRecord,
    ArtifactServiceResult,
)
from openmagi_core_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef


class FakeArtifactService:
    openmagi_local_fake_provider = True

    def __init__(self) -> None:
        self.calls: list[ArtifactChannelDeliveryRequest] = []

    def handle_artifact_request(
        self,
        request: ArtifactChannelDeliveryRequest,
    ) -> ArtifactServiceResult:
        self.calls.append(request)
        return ArtifactServiceResult(
            status="ok",
            artifact=ArtifactRecord(
                artifactId="artifact:report-1",
                kind="document",
                title="Quarterly Report",
                filename="quarterly-report.md",
                mimeType="text/markdown",
                contentDigest="sha256:" + "1" * 64,
                artifactRef="artifact:report-1",
                sourceRefs=("source:ledger-1",),
                provenanceRefs=("child-envelope:abc123",),
            ),
            receiptRef="artifact-receipt:local-fake",
            diagnosticMetadata={
                "rawWorkspacePath": "/workspace/private/report.md",
                "note": "artifact metadata only",
            },
        )


class ThrowingArtifactService(FakeArtifactService):
    def handle_artifact_request(
        self,
        request: ArtifactChannelDeliveryRequest,
    ) -> ArtifactServiceResult:
        self.calls.append(request)
        raise RuntimeError("artifact failed /Users/kevin/private ghp_artifactSecret")


class FakeChannelProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, status: str = "sent", provider_message_id: str | None = "telegram-message-123") -> None:
        self.calls: list[ArtifactChannelDeliveryRequest] = []
        self.status = status
        self.provider_message_id = provider_message_id

    def deliver(self, request: ArtifactChannelDeliveryRequest) -> ChannelDeliveryReceipt:
        self.calls.append(request)
        assert request.channel is not None
        return ChannelDeliveryReceipt(
            receiptId="delivery-receipt:local-fake",
            requestId=request.request_id,
            channel=request.channel,
            status=self.status,
            providerMessageId=self.provider_message_id,
            artifactRefs=request.artifact_refs,
            fileRefs=request.file_refs,
        )


class ThrowingChannelProvider(FakeChannelProvider):
    def deliver(self, request: ArtifactChannelDeliveryRequest) -> ChannelDeliveryReceipt:
        self.calls.append(request)
        raise RuntimeError("channel failed /data/bots/private 123456:ABC-secret-token")


def _request(
    operation: str,
    *,
    channel: ChannelRef | None = None,
    artifact_refs: tuple[str, ...] = ("artifact:report-1",),
    file_refs: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
    filename: str = "quarterly-report.md",
) -> ArtifactChannelDeliveryRequest:
    return ArtifactChannelDeliveryRequest(
        operation=operation,
        requestId="artifact-delivery-1",
        sessionKey="session:local",
        channel=channel,
        artifactRefs=artifact_refs,
        fileRefs=file_refs,
        filename=filename,
        mimeType="text/markdown",
        contentDigest="sha256:" + "0" * 64,
        metadata=metadata or {},
    )


def test_artifact_channel_delivery_boundary_is_disabled_by_default() -> None:
    artifact_service = FakeArtifactService()
    channel_provider = FakeChannelProvider()

    decision = ArtifactChannelDeliveryBoundary(ArtifactChannelDeliveryConfig()).execute(
        _request("artifact.create"),
        artifact_service=artifact_service,
        channel_provider=channel_provider,
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("artifact_channel_delivery_disabled",)
    assert artifact_service.calls == []
    assert channel_provider.calls == []
    projection = decision.public_projection()
    assert projection["authorityFlags"] == {
        "adkArtifactServiceAttached": False,
        "artifactWritten": False,
        "channelDeliveryPerformed": False,
        "productionStorageWritten": False,
        "productionChannelWrite": False,
        "routeAttached": False,
    }


def test_artifact_boundary_uses_local_fake_service_only_when_enabled() -> None:
    artifact_service = FakeArtifactService()

    decision = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
        ),
    ).execute(
        _request(
            "artifact.create",
            metadata={
                "rawPath": "/Users/kevin/private/report.md",
                "token": "ghp_artifactSecret",
            },
        ),
        artifact_service=artifact_service,
    )

    assert len(artifact_service.calls) == 1
    assert decision.status == "artifact_recorded_local_fake"
    assert decision.artifact is not None
    projection = decision.public_projection()
    assert projection["artifact"]["artifactId"] == "artifact:report-1"
    assert projection["receiptRef"] == "artifact-receipt:local-fake"
    assert "artifact metadata only" in str(projection)
    assert "/Users/kevin" not in str(projection)
    assert "/workspace/private" not in str(projection)
    assert "ghp_artifactSecret" not in str(projection)


def test_artifact_boundary_records_intent_when_fake_service_not_enabled() -> None:
    artifact_service = FakeArtifactService()

    decision = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(enabled=True),
    ).execute(_request("artifact.update"), artifact_service=artifact_service)

    assert decision.status == "artifact_intent"
    assert decision.reason_codes == ("local_artifact_service_disabled",)
    assert artifact_service.calls == []
    assert decision.public_projection()["authorityFlags"]["artifactWritten"] is False


def test_artifact_and_channel_boundary_catches_fake_provider_errors() -> None:
    artifact_service = ThrowingArtifactService()
    channel_provider = ThrowingChannelProvider()
    boundary = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        ),
    )

    artifact_decision = boundary.execute(
        _request("artifact.create"),
        artifact_service=artifact_service,
    )
    channel_decision = boundary.execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        channel_provider=channel_provider,
    )

    assert artifact_decision.status == "blocked"
    assert artifact_decision.reason_codes == ("local_fake_artifact_service_error",)
    assert channel_decision.status == "delivery_intent"
    assert channel_decision.reason_codes == ("file_delivery_boundary_required",)
    encoded = str(artifact_decision.public_projection()) + str(
        channel_decision.public_projection()
    )
    assert "/Users/kevin" not in encoded
    assert "/data/bots" not in encoded
    assert "ghp_artifactSecret" not in encoded
    assert "123456:ABC-secret-token" not in encoded


def test_artifact_and_channel_boundary_reject_unmarked_local_fake_ports() -> None:
    class UnmarkedArtifactService(FakeArtifactService):
        openmagi_local_fake_provider = False

    class UnmarkedChannelProvider(FakeChannelProvider):
        openmagi_local_fake_provider = False

    artifact_service = UnmarkedArtifactService()
    channel_provider = UnmarkedChannelProvider()
    boundary = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        ),
    )

    artifact_decision = boundary.execute(
        _request("artifact.create"),
        artifact_service=artifact_service,
    )
    channel_decision = boundary.execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        channel_provider=channel_provider,
    )

    assert artifact_decision.status == "blocked"
    assert artifact_decision.reason_codes == ("local_fake_artifact_service_untrusted",)
    assert channel_decision.status == "delivery_intent"
    assert channel_decision.reason_codes == ("file_delivery_boundary_required",)
    assert artifact_service.calls == []
    assert channel_provider.calls == []


def test_channel_delivery_blocks_absent_or_unsupported_channel_before_provider() -> None:
    channel_provider = FakeChannelProvider()
    boundary = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(enabled=True, localFakeChannelDeliveryEnabled=True),
    )

    absent = boundary.execute(_request("file.send"), channel_provider=channel_provider)
    assert absent.status == "channel_absent"
    assert absent.reason_codes == ("channel_required_for_delivery",)

    unsupported = boundary.execute(
        _request("file.send", channel=ChannelRef(type="web", channelId="web-session-1")),
        channel_provider=channel_provider,
    )
    assert unsupported.status == "unsupported_channel"
    assert unsupported.reason_codes == ("file_send_channel_unsupported",)
    assert channel_provider.calls == []


def test_channel_delivery_uses_local_fake_provider_and_sanitizes_receipt() -> None:
    channel_provider = FakeChannelProvider()

    decision = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(enabled=True, localFakeChannelDeliveryEnabled=True),
    ).execute(
        _request(
            "file.send",
            channel=ChannelRef(type="telegram", channelId="telegram-chat-1"),
            file_refs=("file:report-1",),
            metadata={
                "telegramToken": "123456:ABC-secret-token",
                "rawPath": "/data/bots/bot-1/workspace/report.md",
            },
        ),
        channel_provider=channel_provider,
    )

    assert channel_provider.calls == []
    assert decision.status == "delivery_intent"
    assert decision.reason_codes == ("file_delivery_boundary_required",)
    assert decision.delivery_receipt is None
    projection = decision.public_projection()
    assert projection["deliveryReceipt"] is None
    assert projection["authorityFlags"]["channelDeliveryPerformed"] is False
    assert "123456:ABC-secret-token" not in str(projection)
    assert "/data/bots" not in str(projection)


def test_channel_delivery_blocks_failed_or_missing_provider_ack() -> None:
    boundary = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(enabled=True, localFakeChannelDeliveryEnabled=True),
    )

    failed = boundary.execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        channel_provider=FakeChannelProvider(status="failed"),
    )
    missing_ack = boundary.execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        channel_provider=FakeChannelProvider(provider_message_id=None),
    )

    assert failed.status == "delivery_intent"
    assert failed.reason_codes == ("file_delivery_boundary_required",)
    assert missing_ack.status == "delivery_intent"
    assert missing_ack.reason_codes == ("file_delivery_boundary_required",)
    assert missing_ack.public_projection()["authorityFlags"]["channelDeliveryPerformed"] is False


def test_child_artifact_import_records_sanitized_provenance_without_raw_paths() -> None:
    decision = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
        ),
    ).execute(
        _request(
            "artifact.import_child",
            metadata={
                "childArtifactRef": "artifact:child-output",
                "importedFromArtifactId": "artifact:child-output",
                "collisionPolicy": "rekey",
                "rawChildPath": "/workspace/child/artifacts/raw.md",
                "hiddenReasoning": "do not leak",
            },
        ),
        artifact_service=FakeArtifactService(),
    )

    assert decision.status == "artifact_recorded_local_fake"
    projection = decision.public_projection()
    assert projection["artifact"]["provenanceRefs"] == ["child-envelope:abc123"]
    assert "/workspace/child" not in str(projection)
    assert "hiddenReasoning" not in str(projection)
    assert "do not leak" not in str(projection)


def test_artifact_channel_delivery_rejects_path_and_digest_bypass_attempts() -> None:
    with pytest.raises(ValidationError):
        ArtifactChannelDeliveryRequest(
            operation="artifact.create",
            requestId="artifact-delivery-1",
            sessionKey="session:local",
            filename="../secret.md",
            mimeType="text/markdown",
            contentDigest="sha256:" + "0" * 64,
        )

    with pytest.raises(ValidationError):
        ArtifactRecord(
            artifactId="artifact:bad",
            kind="file",
            title="Bad",
            filename="bad.txt",
            mimeType="text/plain",
            contentDigest="not-a-sha",
            artifactRef="artifact:bad",
        )


def test_artifact_channel_forged_projection_redacts_nested_payloads() -> None:
    from openmagi_core_agent.artifacts.delivery_boundary import (
        ArtifactChannelDeliveryDecision,
        ArtifactChannelAuthorityFlags,
    )

    forged = ArtifactChannelDeliveryDecision.model_construct(
        status="artifact_recorded_local_fake",
        operation="artifact.create",
        requestId="artifact-delivery-1",
        artifact=ArtifactRecord.model_construct(
            artifact_id="/Users/kevin/private-artifact",
            kind="document",
            title="safe title ghp_artifactSecret",
            filename="../secret.md",
            mime_type="text/plain",
            content_digest="not-a-sha",
            artifact_ref="/workspace/private/artifact",
            source_refs=("/data/bots/private-source",),
            provenance_refs=("child-envelope:abc123",),
        ),
        deliveryReceipt=ChannelDeliveryReceipt.model_construct(
            receipt_id="/Users/kevin/receipt",
            request_id="artifact-delivery-1",
            channel=ChannelRef(type="telegram", channelId="/data/bots/chat"),
            status="sent",
            provider_message_id="123456:ABC-secret-token",
            artifact_refs=("/workspace/private-artifact",),
            file_refs=("/Users/kevin/private-file",),
        ),
        reasonCodes=("forged",),
        receiptRef="artifact-delivery-receipt:forged",
        authorityFlags=ArtifactChannelAuthorityFlags.model_construct(
            artifactWritten=True,
            productionStorageWritten=True,
            channelDeliveryPerformed=True,
        ),
    )

    projection = forged.public_projection()
    encoded = str(projection)
    assert "/Users/kevin" not in encoded
    assert "/workspace/private" not in encoded
    assert "/data/bots" not in encoded
    assert "ghp_artifactSecret" not in encoded
    assert "123456:ABC-secret-token" not in encoded
    assert projection["authorityFlags"]["artifactWritten"] is False
    assert projection["authorityFlags"]["productionStorageWritten"] is False


def test_artifact_channel_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.artifacts.delivery_boundary")
forbidden = (
    "google.adk.artifacts",
    "google.adk.runners",
    "google.adk.sessions",
    "telegram",
    "discord",
    "subprocess",
)
loaded = [name for name in forbidden if name in sys.modules]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
