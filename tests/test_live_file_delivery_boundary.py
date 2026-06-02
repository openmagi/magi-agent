from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from magi_agent.channels.contract import ChannelDeliveryReceipt, ChannelRef


class FakeFileArtifactProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, status: str = "ok") -> None:
        self.status = status
        self.calls: list[object] = []

    def write_artifact(self, request: object) -> Mapping[str, object]:
        self.calls.append(request)
        return {
            "status": self.status,
            "artifactRef": "artifact:market-brief",
            "contentDigest": "sha256:" + "1" * 64,
            "receiptId": "artifact-provider-receipt:local",
            "rawContent": "Bearer provider-token /Users/kevin/private/report.md",
        }


class FakeChannelDeliveryProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, status: str = "sent", provider_message_id: str | None = "msg-1") -> None:
        self.status = status
        self.provider_message_id = provider_message_id
        self.calls: list[object] = []

    def deliver(self, request: object) -> ChannelDeliveryReceipt:
        self.calls.append(request)
        assert request.channel is not None
        return ChannelDeliveryReceipt(
            receiptId="channel-receipt:local",
            requestId=request.request_id,
            channel=request.channel,
            status=self.status,
            providerMessageId=self.provider_message_id,
            artifactRefs=request.artifact_refs,
            fileRefs=request.file_refs,
        )


class MismatchedChannelDeliveryProvider(FakeChannelDeliveryProvider):
    def deliver(self, request: object) -> ChannelDeliveryReceipt:
        self.calls.append(request)
        assert request.channel is not None
        return ChannelDeliveryReceipt(
            receiptId="channel-receipt:stale",
            requestId="stale-request",
            channel=ChannelRef(type="telegram", channelId="different-chat"),
            status="sent",
            providerMessageId="stale-msg",
            artifactRefs=(),
            fileRefs=(),
        )


def _request(
    operation: str = "file.deliver",
    *,
    channel: ChannelRef | None = None,
    artifact_refs: tuple[str, ...] = ("artifact:market-brief",),
    file_refs: tuple[str, ...] = (),
    filename: str = "market-brief.md",
    mime_type: str = "text/markdown",
) -> object:
    from magi_agent.artifacts.file_delivery import FileDeliveryRequest

    return FileDeliveryRequest(
        operation=operation,
        requestId="file-delivery-1",
        sessionKey="session:local",
        channel=channel,
        artifactRefs=artifact_refs,
        fileRefs=file_refs,
        filename=filename,
        mimeType=mime_type,
        contentDigest="sha256:" + "0" * 64,
        metadata={
            "rawWorkspacePath": "/workspace/private/report.md",
            "Authorization": "Bearer live-token",
        },
    )


def test_file_delivery_is_disabled_by_default_and_calls_no_provider() -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    artifact_provider = FakeFileArtifactProvider()
    channel_provider = FakeChannelDeliveryProvider()
    decision = FileDeliveryBoundary(FileDeliveryConfig()).execute(
        _request(channel=ChannelRef(type="web", channelId="web-session-1")),
        artifact_provider=artifact_provider,
        channel_provider=channel_provider,
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("file_delivery_disabled",)
    assert artifact_provider.calls == []
    assert channel_provider.calls == []
    assert decision.authority_flags.model_dump(by_alias=True) == {
        "adkArtifactServiceAttached": False,
        "artifactWritten": False,
        "channelDeliveryPerformed": False,
        "productionStorageWritten": False,
        "productionChannelWrite": False,
        "routeAttached": False,
        "rawContentInjected": False,
    }


def test_file_delivery_metadata_keys_are_sanitized_even_when_default_disabled() -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    decision = FileDeliveryBoundary(FileDeliveryConfig()).execute(
        _request(
            channel=ChannelRef(type="web", channelId="web-session-1"),
        ).model_copy(
            update={
                "metadata": {
                    "/Users/kevin/private/report.md": "safe",
                    "/home/openmagi/.ssh/id_rsa": "safe",
                    "Bearer live-token": "safe",
                    "apiKey": "plain-provider-credential",
                    "safeNote": "/home/openmagi/.ssh/id_rsa",
                    "visible": "ok",
                }
            }
        )
    )

    rendered = json.dumps(decision.public_projection(), sort_keys=True)
    assert "visible" in rendered
    assert "/Users/kevin" not in rendered
    assert "/home/openmagi" not in rendered
    assert "Bearer" not in rendered
    assert "live-token" not in rendered
    assert "apiKey" not in rendered
    assert "plain-provider-credential" not in rendered


def test_fake_artifact_write_is_digest_addressed_and_not_success_without_channel_receipt() -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    artifact_provider = FakeFileArtifactProvider()
    decision = FileDeliveryBoundary(
        FileDeliveryConfig(enabled=True, localFakeArtifactServiceEnabled=True)
    ).execute(
        _request(channel=ChannelRef(type="web", channelId="web-session-1")),
        artifact_provider=artifact_provider,
    )

    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)
    assert len(artifact_provider.calls) == 1
    assert decision.status == "delivery_intent"
    assert decision.reason_codes == ("channel_delivery_receipt_required",)
    assert projection["artifactRef"].startswith("artifact:")
    assert projection["artifactRef"] != "artifact:market-brief"
    assert projection["artifactReceipt"]["requestDigest"].startswith("sha256:")
    assert projection["artifactReceipt"]["responseDigest"].startswith("sha256:")
    assert "provider-token" not in rendered
    assert "/Users/kevin" not in rendered
    assert "/workspace/private" not in rendered
    assert projection["authorityFlags"]["artifactWritten"] is False


def test_file_deliver_to_chat_requires_delivery_receipt_before_success() -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    artifact_provider = FakeFileArtifactProvider()
    channel_provider = FakeChannelDeliveryProvider()
    decision = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        _request(channel=ChannelRef(type="web", channelId="web-session-1")),
        artifact_provider=artifact_provider,
        channel_provider=channel_provider,
    )

    projection = decision.public_projection()
    assert decision.status == "delivered_local_fake"
    assert len(artifact_provider.calls) == 1
    assert len(channel_provider.calls) == 1
    assert projection["deliveryReceipt"]["providerMessageId"] == "msg-1"
    assert projection["deliveryClaimAllowed"] is True
    assert projection["authorityFlags"]["channelDeliveryPerformed"] is False


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    (
        ({"file_refs": ("/Users/kevin/private/report.md",)}, "raw_file_ref_blocked"),
        ({"file_refs": ("../secret.md",)}, "raw_file_ref_blocked"),
        ({"file_refs": ("sealed:SOUL.md",)}, "sealed_file_ref_blocked"),
        ({"mime_type": "application/x-shellscript"}, "unsupported_mime_type"),
        ({"artifact_refs": (), "file_refs": ()}, "artifact_or_file_ref_required"),
    ),
)
def test_file_send_refuses_unsafe_inputs_before_provider(kwargs: dict[str, object], reason: str) -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    artifact_provider = FakeFileArtifactProvider()
    channel_provider = FakeChannelDeliveryProvider()
    decision = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        _request(
            "file.send",
            channel=ChannelRef(type="telegram", channelId="chat-1"),
            **kwargs,
        ),
        artifact_provider=artifact_provider,
        channel_provider=channel_provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == (reason,)
    assert artifact_provider.calls == []
    assert channel_provider.calls == []


def test_channel_delivery_cannot_claim_success_without_provider_receipt() -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    missing_ack = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        artifact_provider=FakeFileArtifactProvider(),
        channel_provider=FakeChannelDeliveryProvider(provider_message_id=None),
    )

    assert missing_ack.status == "blocked"
    assert missing_ack.reason_codes == ("channel_delivery_receipt_missing",)
    assert missing_ack.public_projection()["deliveryClaimAllowed"] is False


def test_channel_delivery_requires_correlated_receipt_and_artifact_provider_receipt() -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    channel_only = FileDeliveryBoundary(
        FileDeliveryConfig(enabled=True, localFakeChannelDeliveryEnabled=True)
    ).execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        channel_provider=FakeChannelDeliveryProvider(),
    )
    mismatched = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        artifact_provider=FakeFileArtifactProvider(),
        channel_provider=MismatchedChannelDeliveryProvider(),
    )

    assert channel_only.status == "delivery_intent"
    assert channel_only.reason_codes == ("artifact_provider_receipt_required",)
    assert channel_only.public_projection()["deliveryClaimAllowed"] is False
    assert mismatched.status == "blocked"
    assert mismatched.reason_codes == ("channel_delivery_receipt_mismatch",)


def test_file_delivery_projection_omits_raw_content_and_paths() -> None:
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    decision = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1")),
        artifact_provider=FakeFileArtifactProvider(),
        channel_provider=FakeChannelDeliveryProvider(),
    )

    rendered = json.dumps(decision.public_projection(), sort_keys=True)
    assert "Bearer" not in rendered
    assert "live-token" not in rendered
    assert "provider-token" not in rendered
    assert '"rawContent":' not in rendered
    assert "/workspace/private" not in rendered
    assert "/Users/kevin" not in rendered


def test_artifact_channel_boundary_consumes_file_delivery_decision_receipts() -> None:
    from magi_agent.artifacts.delivery_boundary import (
        ArtifactChannelDeliveryBoundary,
        ArtifactChannelDeliveryConfig,
        ArtifactChannelDeliveryRequest,
    )
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    file_request = _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1"))
    pending = FileDeliveryBoundary(
        FileDeliveryConfig(enabled=True, localFakeArtifactServiceEnabled=True)
    ).execute(file_request, artifact_provider=FakeFileArtifactProvider())
    delivered = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        file_request,
        artifact_provider=FakeFileArtifactProvider(),
        channel_provider=FakeChannelDeliveryProvider(),
    )
    assert delivered.artifact_ref is not None
    artifact_request = ArtifactChannelDeliveryRequest(
        operation="file.send",
        requestId="file-delivery-1",
        sessionKey="session:local",
        channel=ChannelRef(type="telegram", channelId="chat-1"),
        artifactRefs=(delivered.artifact_ref,),
        filename="market-brief.md",
        mimeType="text/markdown",
        contentDigest="sha256:" + "0" * 64,
    )
    boundary = ArtifactChannelDeliveryBoundary(ArtifactChannelDeliveryConfig(enabled=True))

    pending_decision = boundary.consume_file_delivery_decision(artifact_request, pending)
    delivered_decision = boundary.consume_file_delivery_decision(artifact_request, delivered)

    assert pending_decision.status == "delivery_intent"
    assert pending_decision.reason_codes == ("file_delivery_receipt_required",)
    assert delivered_decision.status == "delivery_recorded_local_fake"
    assert delivered_decision.reason_codes == ("file_delivery_receipt_consumed",)
    assert delivered_decision.public_projection()["deliveryReceipt"]["providerMessageId"] == "msg-1"


def test_artifact_channel_boundary_rejects_forged_or_mismatched_file_delivery_decisions() -> None:
    from magi_agent.artifacts.delivery_boundary import (
        ArtifactChannelDeliveryBoundary,
        ArtifactChannelDeliveryConfig,
        ArtifactChannelDeliveryRequest,
    )
    from magi_agent.artifacts.file_delivery import (
        FileDeliveryBoundary,
        FileDeliveryConfig,
    )

    file_request = _request("file.send", channel=ChannelRef(type="telegram", channelId="chat-1"))
    delivered = FileDeliveryBoundary(
        FileDeliveryConfig(
            enabled=True,
            localFakeArtifactServiceEnabled=True,
            localFakeChannelDeliveryEnabled=True,
        )
    ).execute(
        file_request,
        artifact_provider=FakeFileArtifactProvider(),
        channel_provider=FakeChannelDeliveryProvider(),
    )
    forged = type(
        "ForgedDecision",
        (),
        {
            "status": "delivered_local_fake",
            "delivery_claim_allowed": True,
            "delivery_receipt": delivered.delivery_receipt,
            "artifact_ref": "artifact:market-brief",
            "content_digest": "sha256:" + "1" * 64,
        },
    )()
    assert delivered.artifact_ref is not None
    forged_real_decision = delivered.model_copy(
        update={"delivery_claim_allowed": True, "status": "delivered_local_fake"}
    )
    forged_constructed = type(delivered).model_construct(
        status="delivered_local_fake",
        operation="file.send",
        requestId="file-delivery-1",
        artifactRef=delivered.artifact_ref,
        contentDigest=delivered.content_digest,
        artifactReceipt=delivered.artifact_receipt,
        deliveryReceipt=delivered.delivery_receipt,
        deliveryClaimAllowed=True,
        reasonCodes=("forged",),
    )
    wrong_request = ArtifactChannelDeliveryRequest(
        operation="file.send",
        requestId="different-request",
        sessionKey="session:local",
        channel=ChannelRef(type="telegram", channelId="chat-1"),
        artifactRefs=(delivered.artifact_ref,),
        filename="market-brief.md",
        mimeType="text/markdown",
        contentDigest="sha256:" + "0" * 64,
    )
    matching_request = ArtifactChannelDeliveryRequest(
        operation="file.send",
        requestId="file-delivery-1",
        sessionKey="session:local",
        channel=ChannelRef(type="telegram", channelId="chat-1"),
        artifactRefs=(delivered.artifact_ref,),
        filename="market-brief.md",
        mimeType="text/markdown",
        contentDigest="sha256:" + "0" * 64,
    )
    boundary = ArtifactChannelDeliveryBoundary(ArtifactChannelDeliveryConfig(enabled=True))

    forged_decision = boundary.consume_file_delivery_decision(matching_request, forged)
    mismatched_decision = boundary.consume_file_delivery_decision(wrong_request, delivered)
    forged_real = boundary.consume_file_delivery_decision(matching_request, forged_real_decision)
    forged_constructed_decision = boundary.consume_file_delivery_decision(
        matching_request,
        forged_constructed,
    )
    wrong_artifact = boundary.consume_file_delivery_decision(
        matching_request.model_copy(update={"artifact_refs": ("artifact:ffffffffffffffff",)}),
        delivered,
    )

    assert forged_decision.status == "blocked"
    assert forged_decision.reason_codes == ("file_delivery_decision_invalid",)
    assert mismatched_decision.status == "blocked"
    assert mismatched_decision.reason_codes == ("file_delivery_receipt_mismatch",)
    assert forged_real.status == "blocked"
    assert forged_real.reason_codes == ("file_delivery_decision_unverified",)
    assert forged_constructed_decision.status == "blocked"
    assert forged_constructed_decision.reason_codes == ("file_delivery_decision_unverified",)
    assert wrong_artifact.status == "blocked"
    assert wrong_artifact.reason_codes == ("file_delivery_receipt_mismatch",)


def test_legacy_artifact_channel_file_send_requires_file_delivery_boundary() -> None:
    from magi_agent.artifacts.delivery_boundary import (
        ArtifactChannelDeliveryBoundary,
        ArtifactChannelDeliveryConfig,
        ArtifactChannelDeliveryRequest,
    )

    request = ArtifactChannelDeliveryRequest(
        operation="file.send",
        requestId="legacy-file-send",
        sessionKey="session:local",
        channel=ChannelRef(type="telegram", channelId="chat-1"),
        artifactRefs=("artifact:market-brief",),
        filename="market-brief.md",
        mimeType="text/markdown",
        contentDigest="sha256:" + "0" * 64,
        metadata={
            "/Users/kevin/private/report.md": "safe",
            "apiKey": "plain-provider-credential",
            "safeNote": "/home/openmagi/.ssh/id_rsa",
        },
    )
    decision = ArtifactChannelDeliveryBoundary(
        ArtifactChannelDeliveryConfig(enabled=True, localFakeChannelDeliveryEnabled=True)
    ).execute(request, channel_provider=FakeChannelDeliveryProvider())

    rendered = json.dumps(decision.public_projection(), sort_keys=True)
    assert decision.status == "delivery_intent"
    assert decision.reason_codes == ("file_delivery_boundary_required",)
    assert "apiKey" not in rendered
    assert "plain-provider-credential" not in rendered
    assert "/Users/kevin" not in rendered
    assert "/home/openmagi" not in rendered


def test_file_delivery_config_false_fields_are_hardened_against_copy_and_construct() -> None:
    from magi_agent.artifacts.file_delivery import FileDeliveryConfig

    copied = FileDeliveryConfig().model_copy(
        update={
            "productionStorageWritesEnabled": True,
            "productionChannelDeliveryEnabled": True,
            "routeAttached": True,
        }
    )
    constructed = FileDeliveryConfig.model_construct(
        productionStorageWritesEnabled=True,
        productionChannelDeliveryEnabled=True,
        routeAttached=True,
    )

    assert copied.production_storage_writes_enabled is False
    assert copied.production_channel_delivery_enabled is False
    assert copied.route_attached is False
    assert constructed.production_storage_writes_enabled is False
    assert constructed.production_channel_delivery_enabled is False
    assert constructed.route_attached is False


def test_live_file_delivery_fixture_records_default_off_matrix() -> None:
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "artifact_channel_delivery"
            / "live_file_delivery_matrix.json"
        ).read_text()
    )

    assert fixture["fixtureId"] == "live_file_delivery_matrix_0001"
    assert fixture["attachmentFlags"] == {
        "adkArtifactServiceAttached": False,
        "artifactWritten": False,
        "channelDeliveryPerformed": False,
        "productionStorageWritten": False,
        "productionChannelWrite": False,
        "routeAttached": False,
        "rawContentInjected": False,
    }
    assert {case["caseId"] for case in fixture["cases"]} >= {
        "disabled_no_provider_call",
        "file_deliver_requires_channel_receipt",
        "file_send_local_fake_receipt_only",
    }


def test_file_delivery_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.artifacts.file_delivery")
forbidden_prefixes = (
    "google.adk",
    "magi_agent.transport",
    "magi_agent.memory",
    "magi_agent.browser",
    "magi_agent.web_acquisition",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "socket",
    "urllib",
)
loaded = [
    name
    for name in sys.modules
    if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"forbidden modules loaded: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
