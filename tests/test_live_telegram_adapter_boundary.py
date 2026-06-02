from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from openmagi_core_agent.channels.contract import ChannelRef


class FakeTelegramAdapterProvider:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        updates: Sequence[Mapping[str, Any]] | None = None,
        *,
        fail_typing: bool = False,
    ) -> None:
        self.updates = tuple(updates or ())
        self.fail_typing = fail_typing
        self.poll_calls: list[object] = []
        self.message_calls: list[object] = []
        self.document_calls: list[object] = []
        self.photo_calls: list[object] = []
        self.typing_calls: list[object] = []
        self.download_calls: list[object] = []

    def poll_updates(self, request: object) -> Sequence[Mapping[str, Any]]:
        self.poll_calls.append(request)
        return self.updates

    def send_message(self, request: object) -> Mapping[str, object]:
        self.message_calls.append(request)
        return {
            "status": "sent",
            "providerMessageId": f"msg-{len(self.message_calls)}",
        }

    def send_document(self, request: object) -> Mapping[str, object]:
        self.document_calls.append(request)
        return {"status": "sent", "providerMessageId": "doc-1"}

    def send_photo(self, request: object) -> Mapping[str, object]:
        self.photo_calls.append(request)
        return {"status": "sent", "providerMessageId": "photo-1"}

    def send_typing(self, request: object) -> Mapping[str, object]:
        self.typing_calls.append(request)
        if self.fail_typing:
            raise RuntimeError("typing failed 123456:ABC-secret-token /home/openmagi/.env")
        return {"status": "sent", "providerMessageId": "typing-1"}

    def download_file(self, request: object) -> Mapping[str, object]:
        self.download_calls.append(request)
        return {
            "status": "downloaded",
            "fileRef": "telegram-file:downloaded",
            "mimeType": "application/pdf",
        }


def _config(**overrides: object) -> object:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterConfig

    payload = {
        "enabled": True,
        "localFakeProviderEnabled": True,
        "selectedChannelRoutes": ("telegram",),
        "providerAllowlist": ("telegram-provider",),
    }
    payload.update(overrides)
    return TelegramAdapterConfig(**payload)


def _poll_request(**overrides: object) -> object:
    from openmagi_core_agent.channels.telegram_adapter import TelegramPollRequest

    payload = {
        "requestId": "poll-1",
        "providerName": "telegram-provider",
        "botIdDigest": "bot:abc123",
        "ownerIdDigest": "owner:def456",
        "sessionKeyDigest": "session:789",
        "offset": 7,
    }
    payload.update(overrides)
    return TelegramPollRequest(**payload)


def _send_request(**overrides: object) -> object:
    from openmagi_core_agent.channels.telegram_adapter import TelegramSendRequest

    payload = {
        "operation": "send_message",
        "requestId": "send-1",
        "channel": ChannelRef(type="telegram", channelId="42"),
        "providerName": "telegram-provider",
        "botIdDigest": "bot:abc123",
        "ownerIdDigest": "owner:def456",
        "sessionKeyDigest": "session:789",
        "chatId": "42",
        "text": "hello",
    }
    payload.update(overrides)
    return TelegramSendRequest(**payload)


def _download_request(**overrides: object) -> object:
    from openmagi_core_agent.channels.telegram_adapter import TelegramDownloadRequest

    payload = {
        "requestId": "download-1",
        "providerName": "telegram-provider",
        "botIdDigest": "bot:abc123",
        "ownerIdDigest": "owner:def456",
        "sessionKeyDigest": "session:789",
        "fileId": "file-1",
        "fileName": "report.pdf",
        "mimeType": "application/pdf",
        "fileUrl": "https://example.com/file/report.pdf",
    }
    payload.update(overrides)
    return TelegramDownloadRequest(**payload)


def test_telegram_adapter_default_disabled_blocks_polling_and_sending() -> None:
    from openmagi_core_agent.channels.telegram_adapter import (
        TelegramAdapterBoundary,
        TelegramAdapterConfig,
    )

    provider = FakeTelegramAdapterProvider()
    boundary = TelegramAdapterBoundary(TelegramAdapterConfig())

    poll = boundary.poll_updates(_poll_request(), provider=provider)
    send = boundary.send(_send_request(), provider=provider)

    assert poll.status == "disabled"
    assert send.status == "disabled"
    assert provider.poll_calls == []
    assert provider.message_calls == []
    assert set(poll.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_telegram_get_updates_normalizes_text_media_replies_captions_and_attachments() -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider(
        updates=[
            {
                "update_id": 10,
                "message": {
                    "message_id": 55,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "hello bot",
                    "reply_to_message": {"message_id": 54, "caption": "quoted photo"},
                },
            },
            {
                "update_id": 11,
                "message": {
                    "message_id": 56,
                    "from": {"id": 43},
                    "chat": {"id": 42},
                    "caption": "photo caption",
                    "photo": [
                        {"file_id": "small-photo-private-id", "file_size": 1},
                        {"file_id": "large-photo-private-id", "file_size": 10},
                    ],
                },
            },
        ]
    )

    decision = TelegramAdapterBoundary(_config()).poll_updates(
        _poll_request(),
        provider=provider,
    )
    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert decision.status == "inbound_projected_local_fake"
    assert projection["inboundUpdates"][0]["text"] == "hello bot"
    assert projection["inboundUpdates"][0]["replyTo"] == {
        "messageId": "54",
        "preview": "quoted photo",
        "role": "user",
    }
    assert projection["inboundUpdates"][1]["text"] == "photo caption"
    assert projection["inboundUpdates"][1]["attachmentRefs"][0]["filename"] == "photo.jpg"
    assert projection["inboundUpdates"][1]["attachmentRefs"][0]["fileRef"].startswith("telegram-file:")
    assert "large-photo-private-id" not in rendered


def test_telegram_offset_persistence_is_digest_addressed_and_idempotent() -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider(
        updates=[
            {"update_id": 7, "message": {"message_id": 1, "from": {"id": 1}, "chat": {"id": 1}, "text": "a"}},
            {"update_id": 8, "message": {"message_id": 2, "from": {"id": 1}, "chat": {"id": 1}, "text": "b"}},
        ]
    )
    boundary = TelegramAdapterBoundary(_config())

    first = boundary.poll_updates(_poll_request(offset=7), provider=provider)
    second = boundary.poll_updates(_poll_request(offset=7), provider=provider)

    assert first.offset_receipt_ref.startswith("telegram-offset:")
    assert first.offset_receipt_ref == second.offset_receipt_ref
    assert first.next_offset == 9
    assert second.next_offset == 9
    assert str(first.next_offset) not in first.offset_receipt_ref


@pytest.mark.parametrize("method_name", ("poll_updates", "download_file"))
def test_telegram_poll_and_download_require_selected_telegram_route(method_name: str) -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider(
        updates=[
            {"update_id": 7, "message": {"message_id": 1, "from": {"id": 1}, "chat": {"id": 1}, "text": "a"}}
        ]
    )
    boundary = TelegramAdapterBoundary(_config(selectedChannelRoutes=("web",), downloadEnabled=True))
    request = _poll_request() if method_name == "poll_updates" else _download_request()

    decision = getattr(boundary, method_name)(request, provider=provider)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("channel_route_not_selected",)
    assert provider.poll_calls == []
    assert provider.download_calls == []


def test_telegram_stale_webhook_mitigation_is_intent_not_execution() -> None:
    from openmagi_core_agent.channels.telegram_adapter import (
        TelegramAdapterBoundary,
        TelegramWebhookMitigationRequest,
    )

    provider = FakeTelegramAdapterProvider()

    decision = TelegramAdapterBoundary(_config()).mitigate_stale_webhook(
        TelegramWebhookMitigationRequest(
            requestId="webhook-1",
            providerName="telegram-provider",
            botIdDigest="bot:abc123",
            ownerIdDigest="owner:def456",
            sessionKeyDigest="session:789",
        ),
        provider=provider,
    )

    assert decision.status == "webhook_mitigation_intent"
    assert decision.reason_codes == ("telegram_stale_webhook_mitigation_intent",)
    assert provider.poll_calls == []
    assert decision.public_projection()["authorityFlags"]["webhookDeleted"] is False


def test_telegram_send_message_chunks_to_telegram_limits() -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider()
    text = "A" * 7600

    decision = TelegramAdapterBoundary(_config()).send(
        _send_request(text=text, replyToMessageId="55"),
        provider=provider,
    )

    assert decision.status == "sent_local_fake"
    assert len(provider.message_calls) == 3
    assert "".join(call.text for call in provider.message_calls) == text
    assert all(len(call.text) <= 3500 for call in provider.message_calls)
    assert provider.message_calls[0].reply_to_message_id == "55"
    assert provider.message_calls[1].reply_to_message_id is None
    assert decision.delivery_receipts[-1].provider_message_id == "msg-3"


def test_telegram_send_document_and_photo_require_artifact_receipts() -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider()
    boundary = TelegramAdapterBoundary(_config())

    missing = boundary.send(
        _send_request(operation="send_document", fileRef="artifact:report", text="report"),
        provider=provider,
    )
    document = boundary.send(
        _send_request(
            operation="send_document",
            fileRef="artifact:report",
            artifactReceiptRef="artifact-receipt:abc123",
            text="report",
        ),
        provider=provider,
    )
    photo = boundary.send(
        _send_request(
            operation="send_photo",
            requestId="send-photo-1",
            fileRef="artifact:photo",
            artifactReceiptRef="artifact-receipt:def456",
            text="photo",
        ),
        provider=provider,
    )

    assert missing.status == "blocked"
    assert missing.reason_codes == ("artifact_receipt_required",)
    assert document.status == "sent_local_fake"
    assert photo.status == "sent_local_fake"
    assert len(provider.document_calls) == 1
    assert len(provider.photo_calls) == 1


def test_telegram_typing_errors_are_swallowed_and_redacted() -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider(fail_typing=True)

    decision = TelegramAdapterBoundary(_config()).send(
        _send_request(operation="send_typing", text=None, metadata={"botToken": "123456:ABC-secret-token"}),
        provider=provider,
    )
    encoded = json.dumps(decision.public_projection(), sort_keys=True)

    assert decision.status == "provider_error_swallowed"
    assert decision.reason_codes == ("telegram_typing_error_swallowed",)
    assert "123456:ABC-secret-token" not in encoded
    assert "/home/openmagi" not in encoded


def test_telegram_adapter_diagnostic_metadata_redacts_sensitive_keys() -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider()

    decision = TelegramAdapterBoundary(_config()).send(
        _send_request(
            operation="send_typing",
            text=None,
            metadata={
                "/home/openmagi/.ssh/id_rsa": "safe",
                "Bearer sk-test-abcdefghijklmnopqrstuvwxyz": "safe",
                "github_pat_abcdefghijklmnopqrstuvwxyz123456": "safe",
                ("xox" + "b-123456789012-abcdefghijklmnopqrstuvwxyz"): "safe",
                "AKIA1234567890ABCDEF": "safe",
                "AIzaabcdefghijklmnopqrstuvwxyz123456789": "safe",
                "productionChannelWrite": True,
                "routeAttached": True,
                "safeNote": "safe",
            },
        ),
        provider=provider,
    )

    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)
    assert projection["diagnosticMetadata"] == {"operation": "send_typing", "safeNote": "safe"}
    assert "/home" not in rendered
    assert "sk-test" not in rendered
    assert "github_pat_" not in rendered
    assert "xoxb-" not in rendered
    assert "AKIA" not in rendered
    assert "AIza" not in rendered
    assert projection["authorityFlags"]["productionChannelWrite"] is False
    assert projection["authorityFlags"]["routeAttached"] is False


def test_telegram_adapter_decision_copy_cannot_forge_authority_flags() -> None:
    from openmagi_core_agent.channels.telegram_adapter import (
        TelegramAdapterAuthorityFlags,
        TelegramAdapterDecision,
    )

    decision = TelegramAdapterDecision(
        status="blocked",
        operation="send_message",
        requestDigest="digest",
        reasonCodes=("blocked",),
    )

    copied = decision.model_copy(
        update={
            "authority_flags": TelegramAdapterAuthorityFlags.model_construct(
                providerCalled=True,
                productionChannelWrite=True,
                routeAttached=True,
                webhookDeleted=True,
                downloadPerformed=True,
            ),
            "authorityFlags": {
                "providerCalled": True,
                "productionChannelWrite": True,
                "routeAttached": True,
                "webhookDeleted": True,
                "downloadPerformed": True,
            },
        }
    )

    assert set(copied.public_projection()["authorityFlags"].values()) == {False}


def test_telegram_adapter_config_construct_and_copy_cannot_enable_live_flags() -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterConfig

    constructed = TelegramAdapterConfig.model_construct(
        enabled=True,
        local_fake_provider_enabled=True,
        production_channel_write_enabled=True,
        telegram_polling_attached=True,
        telegram_attached=True,
        telegram_webhook_mitigation_attached=True,
        route_attached=True,
    )
    copied = TelegramAdapterConfig().model_copy(
        update={
            "enabled": True,
            "localFakeProviderEnabled": True,
            "productionChannelWriteEnabled": True,
            "telegramPollingAttached": True,
            "telegramAttached": True,
            "telegramWebhookMitigationAttached": True,
            "routeAttached": True,
        }
    )

    assert constructed.model_dump(by_alias=True) == {
        "enabled": False,
        "localFakeProviderEnabled": False,
        "selectedChannelRoutes": (),
        "providerAllowlist": (),
        "downloadEnabled": False,
        "productionChannelWriteEnabled": False,
        "telegramPollingAttached": False,
        "telegramAttached": False,
        "telegramWebhookMitigationAttached": False,
        "routeAttached": False,
    }
    assert copied.model_dump(by_alias=True) == {
        "enabled": True,
        "localFakeProviderEnabled": True,
        "selectedChannelRoutes": (),
        "providerAllowlist": (),
        "downloadEnabled": False,
        "productionChannelWriteEnabled": False,
        "telegramPollingAttached": False,
        "telegramAttached": False,
        "telegramWebhookMitigationAttached": False,
        "routeAttached": False,
    }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    (
        ({"fileName": "../report.pdf"}, "download_path_traversal_blocked"),
        ({"fileUrl": "http://169.254.169.254/latest/meta-data"}, "download_private_url_blocked"),
        ({"fileUrl": "https://api.telegram.org/file/bot123456:ABC-secret-token/report.pdf"}, "download_token_url_blocked"),
        ({"fileUrl": "https://example.com/file/report.pdf?X-Amz-Signature=abcdef"}, "download_token_url_blocked"),
        ({"fileUrl": "https://public.supabase.co/storage/v1/object/sign/private/report.pdf"}, "download_private_url_blocked"),
        ({"mimeType": "application/x-msdownload"}, "download_mime_not_allowed"),
    ),
)
def test_telegram_download_blocks_unsafe_inputs_before_provider_call(
    overrides: dict[str, object],
    reason: str,
) -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider()

    decision = TelegramAdapterBoundary(_config()).download_file(
        _download_request(**overrides),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == (reason,)
    assert provider.download_calls == []


@pytest.mark.parametrize(
    "file_url",
    (
        "http://0.0.0.0/x",
        "http://[::1]/x",
        "http://[fd00::1]/x",
        "http://2130706433/x",
    ),
)
def test_telegram_download_blocks_loopback_ipv6_and_integer_private_urls(file_url: str) -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider()

    decision = TelegramAdapterBoundary(_config()).download_file(
        _download_request(fileUrl=file_url),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("download_private_url_blocked",)
    assert provider.download_calls == []


@pytest.mark.parametrize("channel_type", ("web", "app", "discord"))
def test_telegram_adapter_never_calls_provider_for_non_telegram_channel(channel_type: str) -> None:
    from openmagi_core_agent.channels.telegram_adapter import TelegramAdapterBoundary

    provider = FakeTelegramAdapterProvider()

    decision = TelegramAdapterBoundary(_config()).send(
        _send_request(channel=ChannelRef(type=channel_type, channelId="not-telegram")),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("telegram_channel_required",)
    assert provider.message_calls == []


def test_telegram_dispatch_provider_adapter_feeds_channel_dispatcher_receipts() -> None:
    from openmagi_core_agent.channels.dispatcher import (
        ChannelDispatchConfig,
        ChannelDispatchRequest,
        ChannelDispatcher,
    )
    from openmagi_core_agent.channels.telegram_adapter import (
        TelegramAdapterBoundary,
        TelegramChannelDispatchProviderAdapter,
    )

    provider = FakeTelegramAdapterProvider()
    dispatch_provider = TelegramChannelDispatchProviderAdapter(
        boundary=TelegramAdapterBoundary(_config()),
        telegram_provider=provider,
    )

    decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
        )
    ).dispatch(
        ChannelDispatchRequest(
            operation="dispatch.message",
            requestId="dispatch-telegram-1",
            channel=ChannelRef(type="telegram", channelId="42"),
            providerName="telegram-provider",
            botIdDigest="bot:abc123",
            userIdDigest="owner:def456",
            sessionKeyDigest="session:789",
            text="hello",
        ),
        provider=dispatch_provider,
    )

    assert decision.status == "recorded_local_fake"
    assert decision.receipt is not None
    assert decision.receipt.provider_message_id == "msg-1"
    assert len(provider.message_calls) == 1


def test_telegram_adapter_fixture_matrix_is_packaged() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "channel_telegram"
        / "live_adapter_matrix.json"
    )

    matrix = json.loads(fixture.read_text())

    assert {row["operation"] for row in matrix["rows"]} >= {
        "poll_updates",
        "send_message",
        "send_document",
        "send_photo",
        "send_typing",
        "download_file",
        "mitigate_stale_webhook",
    }
