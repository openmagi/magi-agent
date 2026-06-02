from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from magi_agent.channels.contract import ChannelRef


class FakeDiscordProvider:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        events: Sequence[Mapping[str, Any]] | None = None,
        *,
        fail_send: bool = False,
        missing_channel: bool = False,
        rate_limited: bool = False,
        permission_denied: bool = False,
    ) -> None:
        self.events = tuple(events or ())
        self.fail_send = fail_send
        self.missing_channel = missing_channel
        self.rate_limited = rate_limited
        self.permission_denied = permission_denied
        self.event_calls: list[object] = []
        self.message_calls: list[object] = []
        self.file_calls: list[object] = []
        self.typing_calls: list[object] = []

    def read_events(self, request: object) -> Sequence[Mapping[str, Any]]:
        self.event_calls.append(request)
        return self.events

    def send_message(self, request: object) -> Mapping[str, object]:
        self.message_calls.append(request)
        if self.permission_denied:
            return {"status": "permission_denied", "errorCode": "missing_permissions"}
        if self.missing_channel:
            return {"status": "missing_channel", "errorCode": "channel_not_found"}
        if self.rate_limited:
            return {"status": "rate_limited", "retryAfterMs": 1000}
        if self.fail_send:
            raise RuntimeError("discord token xoxb-secret /home/openmagi/.env")
        return {"status": "sent", "providerMessageId": f"discord-msg-{len(self.message_calls)}"}

    def send_file(self, request: object) -> Mapping[str, object]:
        self.file_calls.append(request)
        if self.fail_send:
            raise RuntimeError("discord file failed Bearer unsafe-token")
        return {"status": "sent", "providerMessageId": "discord-file-1"}

    def send_typing(self, request: object) -> Mapping[str, object]:
        self.typing_calls.append(request)
        if self.fail_send:
            raise RuntimeError("typing failed github_pat_secret")
        return {"status": "sent", "providerMessageId": "discord-typing-1"}


def _config(**overrides: object) -> object:
    from magi_agent.channels.discord_adapter import DiscordAdapterConfig

    payload = {
        "enabled": True,
        "localFakeProviderEnabled": True,
        "selectedChannelRoutes": ("discord",),
        "providerAllowlist": ("discord-provider",),
    }
    payload.update(overrides)
    return DiscordAdapterConfig(**payload)


def _event_request(**overrides: object) -> object:
    from magi_agent.channels.discord_adapter import DiscordEventRequest

    payload = {
        "requestId": "events-1",
        "providerName": "discord-provider",
        "botIdDigest": "bot:abc123",
        "ownerIdDigest": "owner:def456",
        "sessionKeyDigest": "session:789",
        "botUserId": "bot-user",
    }
    payload.update(overrides)
    return DiscordEventRequest(**payload)


def _send_request(**overrides: object) -> object:
    from magi_agent.channels.discord_adapter import DiscordSendRequest

    payload = {
        "operation": "send_message",
        "requestId": "send-1",
        "channel": ChannelRef(type="discord", channelId="chan-1"),
        "providerName": "discord-provider",
        "botIdDigest": "bot:abc123",
        "ownerIdDigest": "owner:def456",
        "sessionKeyDigest": "session:789",
        "channelId": "chan-1",
        "text": "hello",
    }
    payload.update(overrides)
    return DiscordSendRequest(**payload)


def test_discord_adapter_default_disabled_blocks_events_and_sends() -> None:
    from magi_agent.channels.discord_adapter import (
        DiscordAdapterBoundary,
        DiscordAdapterConfig,
    )

    provider = FakeDiscordProvider()
    boundary = DiscordAdapterBoundary(DiscordAdapterConfig())

    events = boundary.handle_events(_event_request(), provider=provider)
    send = boundary.send(_send_request(), provider=provider)

    assert events.status == "disabled"
    assert send.status == "disabled"
    assert provider.event_calls == []
    assert provider.message_calls == []
    assert set(events.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_discord_events_normalize_message_create_thread_reply_attachments_and_author() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider(
        events=[
            {
                "type": "message_create",
                "id": "msg-1",
                "channel_id": "chan-1",
                "author": {"id": "user-1", "bot": False},
                "content": "<@bot-user> review this",
                "is_dm": False,
                "mentions": ["bot-user"],
                "reference": {
                    "message_id": "quoted-1",
                    "content": "earlier answer",
                    "author_id": "bot-user",
                },
                "attachments": [
                    {
                        "id": "att-private-id",
                        "filename": "../report.pdf",
                        "content_type": "application/pdf",
                        "size": 7,
                        "url": "https://cdn.discordapp.example/attachments/report.pdf",
                    }
                ],
            },
            {
                "type": "message_create",
                "id": "msg-2",
                "channel_id": "dm-1",
                "author": {"id": "user-2", "bot": False},
                "content": "",
                "is_dm": True,
                "attachments": [
                    {
                        "id": "image-private-id",
                        "filename": "photo.png",
                        "content_type": "image/png",
                        "size": 5,
                    }
                ],
            },
        ]
    )

    decision = DiscordAdapterBoundary(_config()).handle_events(
        _event_request(),
        provider=provider,
    )
    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert decision.status == "inbound_projected_local_fake"
    assert projection["inboundEvents"][0]["text"] == "<@bot-user> review this"
    assert str(projection["inboundEvents"][0]["replyTo"]["messageId"]).startswith("discord-message:")
    assert projection["inboundEvents"][0]["replyTo"]["preview"] == "earlier answer"
    assert projection["inboundEvents"][0]["replyTo"]["role"] == "assistant"
    assert projection["inboundEvents"][0]["attachmentRefs"][0]["filename"] == "report.pdf"
    assert projection["inboundEvents"][0]["attachmentRefs"][0]["fileRef"].startswith("discord-file:")
    assert projection["inboundEvents"][1]["text"] == ""
    assert projection["inboundEvents"][1]["attachmentRefs"][0]["mimeType"] == "image/png"
    assert "chan-1" not in rendered
    assert "user-1" not in rendered
    assert "msg-1" not in rendered
    assert "quoted-1" not in rendered
    assert "att-private-id" not in rendered
    assert "image-private-id" not in rendered


def test_discord_public_projection_redacts_snowflake_ids_in_text_and_reply_preview() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider(
        events=[
            {
                "type": "message_create",
                "id": "111111111111111111",
                "channel_id": "444444444444444444",
                "author": {"id": "999999999999999999", "bot": False},
                "content": "<@999999999999999999> see <#444444444444444444> and 555555555555555555",
                "is_dm": True,
                "reference": {
                    "message_id": "222222222222222222",
                    "content": "quoted <@&333333333333333333> <:ok:666666666666666666>",
                    "author_id": "999999999999999999",
                },
            },
        ],
    )

    decision = DiscordAdapterBoundary(_config()).handle_events(
        _event_request(),
        provider=provider,
    )
    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)

    assert projection["inboundEvents"][0]["text"].startswith("<@discord-user:")
    assert "discord-channel:" in projection["inboundEvents"][0]["text"]
    assert "discord-id:" in projection["inboundEvents"][0]["text"]
    assert "discord-role:" in projection["inboundEvents"][0]["replyTo"]["preview"]
    assert "discord-emoji:" in projection["inboundEvents"][0]["replyTo"]["preview"]
    for raw_id in (
        "111111111111111111",
        "222222222222222222",
        "333333333333333333",
        "444444444444444444",
        "555555555555555555",
        "666666666666666666",
        "999999999999999999",
    ):
        assert raw_id not in rendered


def test_discord_events_filter_bots_empty_guild_chatter_and_unmentioned_guild_messages() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider(
        events=[
            {
                "type": "message_create",
                "id": "bot-msg",
                "channel_id": "chan-1",
                "author": {"id": "bot-2", "bot": True},
                "content": "hi",
                "is_dm": True,
            },
            {
                "type": "message_create",
                "id": "empty",
                "channel_id": "chan-1",
                "author": {"id": "user-1", "bot": False},
                "content": "",
                "is_dm": False,
            },
            {
                "type": "message_create",
                "id": "guild-chatter",
                "channel_id": "chan-1",
                "author": {"id": "user-1", "bot": False},
                "content": "hello everyone",
                "is_dm": False,
                "mentions": [],
            },
        ]
    )

    decision = DiscordAdapterBoundary(_config()).handle_events(
        _event_request(),
        provider=provider,
    )

    assert decision.status == "inbound_projected_local_fake"
    assert decision.inbound_events == ()


def test_discord_send_message_chunks_to_content_limit() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider()
    text = "A" * 4300

    decision = DiscordAdapterBoundary(_config()).send(
        _send_request(text=text),
        provider=provider,
    )

    assert decision.status == "sent_local_fake"
    assert len(provider.message_calls) == 3
    assert "".join(call.text for call in provider.message_calls) == text
    assert all(len(call.text) <= 1900 for call in provider.message_calls)
    assert decision.delivery_receipts[-1].provider_message_id == "discord-msg-3"


def test_discord_file_delivery_requires_artifact_receipt() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider()
    boundary = DiscordAdapterBoundary(_config())

    missing = boundary.send(
        _send_request(operation="send_file", fileRef="artifact:report", text="report"),
        provider=provider,
    )
    sent = boundary.send(
        _send_request(
            operation="send_file",
            fileRef="artifact:report",
            artifactReceiptRef="artifact-receipt:abc123",
            text="report",
        ),
        provider=provider,
    )

    assert missing.status == "blocked"
    assert missing.reason_codes == ("artifact_receipt_required",)
    assert sent.status == "sent_local_fake"
    assert len(provider.file_calls) == 1


def test_discord_file_delivery_blocks_secret_shaped_refs_without_throwing() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider()

    decision = DiscordAdapterBoundary(_config()).send(
        _send_request(
            operation="send_file",
            fileRef="Bearer sk-test-abcdefghijklmnopqrstuvwxyz",
            artifactReceiptRef="artifact-receipt:abc123",
            text="report",
        ),
        provider=provider,
    )
    projection = decision.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("invalid_file_ref_blocked",)
    assert provider.file_calls == []
    assert "sk-test" not in encoded
    assert "Bearer" not in encoded


def test_discord_file_delivery_blocks_private_caption_before_provider_call() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider()

    decision = DiscordAdapterBoundary(_config()).send(
        _send_request(
            operation="send_file",
            fileRef="artifact:report",
            artifactReceiptRef="artifact-receipt:abc123",
            text="raw tool result /Users/kevin/private.txt",
        ),
        provider=provider,
    )
    encoded = json.dumps(decision.public_projection(), sort_keys=True)

    assert decision.status == "blocked"
    assert decision.reason_codes == ("private_outbound_text_blocked",)
    assert provider.file_calls == []
    assert "/Users/kevin" not in encoded
    assert "raw tool result" not in encoded


@pytest.mark.parametrize(
    ("provider", "reason"),
    (
        (FakeDiscordProvider(permission_denied=True), "discord_permission_denied"),
        (FakeDiscordProvider(missing_channel=True), "discord_channel_missing"),
        (FakeDiscordProvider(rate_limited=True), "discord_rate_limited"),
        (FakeDiscordProvider(fail_send=True), "discord_provider_error"),
    ),
)
def test_discord_provider_failures_produce_deterministic_redacted_receipts(
    provider: FakeDiscordProvider,
    reason: str,
) -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    first = DiscordAdapterBoundary(_config()).send(_send_request(), provider=provider)
    second = DiscordAdapterBoundary(_config()).send(_send_request(), provider=provider)
    encoded = json.dumps(first.public_projection(), sort_keys=True)

    assert first.status in {"blocked", "provider_error_swallowed"}
    assert first.reason_codes == (reason,)
    assert first.failure_receipt is not None
    assert second.failure_receipt is not None
    assert first.failure_receipt.receipt_id == second.failure_receipt.receipt_id
    assert "xoxb-secret" not in encoded
    assert "/home/openmagi" not in encoded


@pytest.mark.parametrize("channel_type", ("web", "app", "telegram"))
def test_discord_adapter_cannot_be_selected_by_non_discord_channels(channel_type: str) -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider()

    decision = DiscordAdapterBoundary(_config()).send(
        _send_request(channel=ChannelRef(type=channel_type, channelId="other")),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("discord_channel_required",)
    assert provider.message_calls == []


def test_discord_dispatch_provider_adapter_feeds_channel_dispatcher_receipts() -> None:
    from magi_agent.channels.discord_adapter import (
        DiscordAdapterBoundary,
        DiscordChannelDispatchProviderAdapter,
    )
    from magi_agent.channels.dispatcher import (
        ChannelDispatchConfig,
        ChannelDispatchRequest,
        ChannelDispatcher,
    )

    provider = FakeDiscordProvider()
    dispatch_provider = DiscordChannelDispatchProviderAdapter(
        boundary=DiscordAdapterBoundary(_config()),
        discord_provider=provider,
    )

    decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("discord",),
            providerAllowlist=("discord-provider",),
        )
    ).dispatch(
        ChannelDispatchRequest(
            operation="dispatch.message",
            requestId="dispatch-discord-1",
            channel=ChannelRef(type="discord", channelId="chan-1"),
            providerName="discord-provider",
            botIdDigest="bot:abc123",
            userIdDigest="owner:def456",
            sessionKeyDigest="session:789",
            text="hello",
        ),
        provider=dispatch_provider,
    )

    assert decision.status == "recorded_local_fake"
    assert decision.receipt is not None
    assert decision.receipt.provider_message_id == "discord-msg-1"
    assert len(provider.message_calls) == 1


def test_discord_adapter_diagnostic_metadata_redacts_sensitive_keys_and_flags() -> None:
    from magi_agent.channels.discord_adapter import DiscordAdapterBoundary

    provider = FakeDiscordProvider()

    decision = DiscordAdapterBoundary(_config()).send(
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
                "parentContext": "raw child transcript should not leak",
                "systemPrompt": "hidden system prompt",
                "developerInstruction": "secret instruction",
                "safeNote": "safe",
            },
        ),
        provider=provider,
    )

    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)
    assert projection["diagnosticMetadata"] == {"operation": "send_typing", "safeNote": "safe"}
    assert "/home" not in rendered
    assert "github_pat_" not in rendered
    assert "xoxb-" not in rendered
    assert "AKIA" not in rendered
    assert "AIza" not in rendered
    assert "parentContext" not in rendered
    assert "systemPrompt" not in rendered
    assert "developerInstruction" not in rendered
    assert "hidden system prompt" not in rendered
    assert "raw child transcript" not in rendered
    assert projection["authorityFlags"]["productionChannelWrite"] is False
    assert projection["authorityFlags"]["routeAttached"] is False


def test_discord_adapter_config_and_decision_cannot_forge_live_flags() -> None:
    from magi_agent.channels.discord_adapter import (
        DiscordAdapterAuthorityFlags,
        DiscordAdapterConfig,
        DiscordAdapterDecision,
    )

    config = DiscordAdapterConfig.model_construct(
        enabled=True,
        local_fake_provider_enabled=True,
        production_channel_write_enabled=True,
        discord_gateway_attached=True,
        discord_attached=True,
        route_attached=True,
    )
    copied_config = DiscordAdapterConfig().model_copy(
        update={
            "enabled": True,
            "localFakeProviderEnabled": True,
            "productionChannelWriteEnabled": True,
            "discordGatewayAttached": True,
            "discordAttached": True,
            "routeAttached": True,
        }
    )
    copied_decision = DiscordAdapterDecision(
        status="blocked",
        operation="send_message",
        requestDigest="digest",
        reasonCodes=("blocked",),
    ).model_copy(
        update={
            "authority_flags": DiscordAdapterAuthorityFlags.model_construct(
                providerCalled=True,
                productionChannelWrite=True,
                routeAttached=True,
                gatewayAttached=True,
            )
        }
    )

    assert config.model_dump(by_alias=True)["productionChannelWriteEnabled"] is False
    assert config.model_dump(by_alias=True)["discordGatewayAttached"] is False
    assert copied_config.model_dump(by_alias=True)["productionChannelWriteEnabled"] is False
    assert copied_config.model_dump(by_alias=True)["discordGatewayAttached"] is False
    assert set(copied_decision.public_projection()["authorityFlags"].values()) == {False}


def test_discord_adapter_fixture_matrix_is_packaged() -> None:
    fixture = (
        Path(__file__).parent
        / "fixtures"
        / "channel_discord"
        / "live_adapter_matrix.json"
    )

    matrix = json.loads(fixture.read_text())

    assert {row["operation"] for row in matrix["rows"]} >= {
        "handle_events",
        "send_message",
        "send_file",
        "send_typing",
    }
