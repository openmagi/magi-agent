from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

import pytest

from magi_agent.channels.telegram_boundary import (
    TelegramRuntimeBoundary,
    TelegramRuntimeConfig,
    TelegramRuntimeRequest,
)


class FakeTelegramProvider:
    openmagi_local_fake_provider = True
    openmagi_delivery_ack_guaranteed = True

    def __init__(
        self,
        updates: list[dict[str, Any]] | None = None,
        *,
        fail_typing: bool = False,
    ) -> None:
        self.updates = updates or []
        self.fail_typing = fail_typing
        self.sent_messages: list[dict[str, Any]] = []
        self.typing_calls: list[str] = []
        self.document_calls: list[dict[str, Any]] = []
        self.photo_calls: list[dict[str, Any]] = []
        self.poll_count = 0

    def poll_once(self, request: TelegramRuntimeRequest) -> list[dict[str, Any]]:
        self.poll_count += 1
        if self.fail_typing:
            raise RuntimeError("telegram token 123456:ABC-secret-token rejected")
        assert request.operation == "poll_once"
        return self.updates

    def send_message(
        self,
        *,
        chat_id: str,
        text: str,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "chatId": chat_id,
            "text": text,
            "replyToMessageId": reply_to_message_id,
            "providerMessageId": f"msg-{len(self.sent_messages) + 1}",
        }
        self.sent_messages.append(payload)
        return payload

    def send_typing(self, *, chat_id: str) -> dict[str, Any]:
        self.typing_calls.append(chat_id)
        if self.fail_typing:
            raise RuntimeError("telegram token 123456:ABC-secret-token rejected")
        return {"chatId": chat_id, "ok": True}

    def send_document(
        self,
        *,
        chat_id: str,
        file_ref: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        payload = {"chatId": chat_id, "fileRef": file_ref, "caption": caption}
        self.document_calls.append(payload)
        return {**payload, "providerMessageId": "doc-1"}

    def send_photo(
        self,
        *,
        chat_id: str,
        file_ref: str,
        caption: str | None = None,
    ) -> dict[str, Any]:
        payload = {"chatId": chat_id, "fileRef": file_ref, "caption": caption}
        self.photo_calls.append(payload)
        return {**payload, "providerMessageId": "photo-1"}


def test_telegram_boundary_is_disabled_by_default() -> None:
    provider = FakeTelegramProvider(
        updates=[
            {
                "update_id": 1,
                "message": {
                    "message_id": 55,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "hello",
                },
            }
        ],
    )

    decision = TelegramRuntimeBoundary(TelegramRuntimeConfig()).execute(
        TelegramRuntimeRequest(operation="poll_once"),
        provider=provider,
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("telegram_runtime_disabled",)
    assert provider.poll_count == 0
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_telegram_poll_projects_text_reply_and_attachment_without_raw_update() -> None:
    provider = FakeTelegramProvider(
        updates=[
            {
                "update_id": 100,
                "message": {
                    "message_id": 55,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "text": "hello bot",
                    "reply_to_message": {
                        "message_id": 54,
                        "text": "previous answer",
                    },
                },
            },
            {
                "update_id": 101,
                "message": {
                    "message_id": 56,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "document": {
                        "file_id": "file-1",
                        "file_name": "report.pdf",
                        "mime_type": "application/pdf",
                        "file_size": 123,
                        "file_path": "/workspace/telegram-downloads/report.pdf",
                    },
                },
            },
        ],
    )

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(TelegramRuntimeRequest(operation="poll_once"), provider=provider)

    assert decision.status == "inbound_projected_local_fake"
    projection = decision.public_projection()
    assert projection["inboundMessages"][0] == {
        "channel": "telegram",
        "chatId": "42",
        "userId": "42",
        "text": "hello bot",
        "messageId": "55",
        "replyTo": {"messageId": "54", "preview": "previous answer", "role": "user"},
        "attachmentRefs": [],
        "rawUpdateRef": "telegram-update:100",
    }
    assert projection["inboundMessages"][1]["attachmentRefs"][0]["filename"] == "report.pdf"
    assert "/workspace/telegram-downloads" not in str(projection)
    assert "file_path" not in str(projection)


def test_telegram_poll_projects_caption_media_and_digests_string_update_id() -> None:
    provider = FakeTelegramProvider(
        updates=[
            {
                "update_id": "/workspace/private/update-token",
                "message": {
                    "message_id": 57,
                    "from": {"id": 42},
                    "chat": {"id": 42},
                    "caption": "photo caption",
                    "photo": [
                        {"file_id": "photo-private-file-id", "file_size": 10},
                    ],
                },
            }
        ],
    )

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(TelegramRuntimeRequest(operation="poll_once"), provider=provider)

    projection = decision.public_projection()
    assert projection["inboundMessages"][0]["text"] == "photo caption"
    assert projection["inboundMessages"][0]["attachmentRefs"][0]["kind"] == "file"
    assert projection["inboundMessages"][0]["attachmentRefs"][0]["filename"] == "photo.jpg"
    assert projection["inboundMessages"][0]["rawUpdateRef"].startswith("telegram-update:")
    assert "/workspace/private" not in str(projection)
    assert "photo-private-file-id" not in str(projection)


def test_telegram_poll_errors_are_swallowed_and_redacted() -> None:
    provider = FakeTelegramProvider(fail_typing=True)

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(TelegramRuntimeRequest(operation="poll_once"), provider=provider)

    assert decision.status == "provider_error_swallowed"
    assert decision.reason_codes == ("telegram_poll_error_swallowed",)
    assert "123456:ABC-secret-token" not in str(decision.public_projection())


def test_telegram_send_chunks_text_and_records_local_fake_receipts() -> None:
    provider = FakeTelegramProvider()
    text = "뉴스\n" + ("A" * 5000)

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_message",
            chatId="42",
            text=text,
            replyToMessageId="55",
        ),
        provider=provider,
    )

    assert decision.status == "sent_local_fake"
    assert len(provider.sent_messages) == 2
    assert "".join(call["text"] for call in provider.sent_messages) == text
    assert all(len(call["text"]) <= 3500 for call in provider.sent_messages)
    assert provider.sent_messages[0]["replyToMessageId"] == "55"
    assert provider.sent_messages[1]["replyToMessageId"] is None
    assert decision.public_projection()["deliveryReceipts"][1]["providerMessageId"] == "msg-2"


def test_telegram_send_blocks_private_text_before_provider_call() -> None:
    provider = FakeTelegramProvider()

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_message",
            chatId="42",
            text="raw tool log /workspace/private Authorization: Bearer unsafe-token",
        ),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("private_outbound_text_blocked",)
    assert provider.sent_messages == []
    assert "unsafe-token" not in str(decision.public_projection())


def test_telegram_send_blocks_provider_token_formats_before_provider_call() -> None:
    provider = FakeTelegramProvider()

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_message",
            chatId="42",
            text=(
                "github_pat_unsafeToken12345 "
                "xoxb-unsafeToken12345 "
                "AIzaUnsafeGoogleToken12345"
            ),
        ),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("private_outbound_text_blocked",)
    assert provider.sent_messages == []


def test_telegram_send_blocks_home_and_kubelet_paths_before_provider_call() -> None:
    provider = FakeTelegramProvider()

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_message",
            chatId="42",
            text="read /home/kevin/.ssh/id_rsa and /var/lib/kubelet/pods/x/token",
        ),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("private_outbound_text_blocked",)
    assert provider.sent_messages == []


def test_telegram_send_requires_provider_ack_before_receipt() -> None:
    class MissingAckProvider(FakeTelegramProvider):
        openmagi_delivery_ack_guaranteed = False

        def send_message(
            self,
            *,
            chat_id: str,
            text: str,
            reply_to_message_id: str | None = None,
        ) -> dict[str, Any]:
            payload = {
                "chatId": chat_id,
                "text": text,
                "replyToMessageId": reply_to_message_id,
            }
            self.sent_messages.append(payload)
            return payload

    provider = MissingAckProvider()

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(operation="send_message", chatId="42", text="hello"),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("provider_message_ack_required",)
    assert decision.delivery_receipts == ()
    assert provider.sent_messages == []


def test_telegram_file_send_requires_ack_capability_before_provider_call() -> None:
    class MissingAckProvider(FakeTelegramProvider):
        openmagi_delivery_ack_guaranteed = False

    provider = MissingAckProvider()

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_document",
            chatId="42",
            fileRef="artifact:report",
            text="safe caption",
        ),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("provider_message_ack_required",)
    assert decision.delivery_receipts == ()
    assert provider.document_calls == []


def test_telegram_typing_errors_are_swallowed_and_redacted() -> None:
    provider = FakeTelegramProvider(fail_typing=True)

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_typing",
            chatId="42",
            metadata={"botToken": "123456:ABC-secret-token"},
        ),
        provider=provider,
    )

    assert decision.status == "provider_error_swallowed"
    projection = decision.public_projection()
    assert "123456:ABC-secret-token" not in str(projection)
    assert projection["authorityFlags"]["telegramPollingAttached"] is False


def test_telegram_diagnostic_metadata_cannot_forge_authority_flags() -> None:
    provider = FakeTelegramProvider()

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_typing",
            chatId="42",
            metadata={
                "productionChannelWrite": True,
                "telegramPollingAttached": True,
                "routeAttached": True,
                "trusted": True,
                "authoritative": True,
                "safeNote": "safe",
            },
        ),
        provider=provider,
    )
    projection = decision.public_projection()
    diagnostic = str(projection["diagnosticMetadata"])

    assert decision.status == "typing_recorded_local_fake"
    assert "productionChannelWrite" not in diagnostic
    assert "telegramPollingAttached" not in diagnostic
    assert "routeAttached" not in diagnostic
    assert "trusted" not in diagnostic
    assert "authoritative" not in diagnostic
    assert projection["diagnosticMetadata"]["safeNote"] == "safe"
    assert projection["authorityFlags"]["productionChannelWrite"] is False


def test_telegram_diagnostic_metadata_drops_key_named_credentials() -> None:
    provider = FakeTelegramProvider()

    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_typing",
            chatId="42",
            metadata={
                "apiKey": "plain-provider-credential",
                "privateKey": "plain-private-key",
                "serviceKey": "plain-service-key",
                "authorizationHeader": "plain-auth-header",
                "safeNote": "safe",
            },
        ),
        provider=provider,
    )
    projection = decision.public_projection()
    encoded = json.dumps(projection, sort_keys=True)

    assert "plain-provider-credential" not in encoded
    assert "plain-private-key" not in encoded
    assert "plain-service-key" not in encoded
    assert "plain-auth-header" not in encoded
    assert projection["diagnosticMetadata"]["safeNote"] == "safe"


def test_telegram_document_delivery_requires_ref_and_never_accepts_raw_path() -> None:
    provider = FakeTelegramProvider()
    boundary = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    )

    raw_path = boundary.execute(
        TelegramRuntimeRequest(
            operation="send_document",
            chatId="42",
            fileRef="/Users/kevin/private/report.pdf",
            text="file",
        ),
        provider=provider,
    )
    assert raw_path.status == "blocked"
    assert raw_path.reason_codes == ("raw_path_file_delivery_blocked",)

    sent = boundary.execute(
        TelegramRuntimeRequest(
            operation="send_document",
            chatId="42",
            fileRef="file:report-1",
            text="file",
        ),
        provider=provider,
    )
    assert sent.status == "sent_local_fake"
    assert provider.document_calls == [
        {"chatId": "42", "fileRef": "file:report-1", "caption": "file"}
    ]


def test_telegram_photo_delivery_uses_separate_media_operation() -> None:
    provider = FakeTelegramProvider()

    sent = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(
            operation="send_photo",
            chatId="42",
            fileRef="file:photo-1",
            text="photo",
        ),
        provider=provider,
    )

    assert sent.status == "sent_local_fake"
    assert sent.reason_codes == ("local_fake_telegram_photo_receipt_only",)
    assert provider.photo_calls == [
        {"chatId": "42", "fileRef": "file:photo-1", "caption": "photo"}
    ]


def test_telegram_boundary_rejects_unmarked_local_fake_provider() -> None:
    class UnmarkedTelegramProvider(FakeTelegramProvider):
        openmagi_local_fake_provider = False

    provider = UnmarkedTelegramProvider()
    decision = TelegramRuntimeBoundary(
        TelegramRuntimeConfig(enabled=True, localFakeTelegramProviderEnabled=True),
    ).execute(
        TelegramRuntimeRequest(operation="send_message", chatId="42", text="hello"),
        provider=provider,
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("local_fake_telegram_provider_untrusted",)
    assert provider.sent_messages == []


def test_telegram_forged_projection_redacts_nested_payloads_and_refs() -> None:
    from magi_agent.channels.telegram_boundary import (
        TelegramDeliveryReceipt,
        TelegramInboundMessage,
        TelegramRuntimeDecision,
    )

    forged = TelegramRuntimeDecision.model_construct(
        status="inbound_projected_local_fake",
        operation="poll_once",
        inboundMessages=(
            TelegramInboundMessage.model_construct(
                chat_id="/Users/kevin/private-chat",
                user_id="user-1",
                text="safe\nAuthorization: Bearer unsafe-token",
                message_id="msg-1",
                raw_update_ref="telegram-update:123456:ABC-secret-token",
                attachment_refs=(),
            ),
        ),
        deliveryReceipts=(
            TelegramDeliveryReceipt.model_construct(
                chat_id="/data/bots/private",
                provider_message_id="123456:ABC-secret-token",
                file_ref="/Users/kevin/private.pdf",
            ),
        ),
        reasonCodes=("forged",),
        authorityFlags={"telegramAttached": True, "productionChannelWrite": True},
    )

    projection = forged.public_projection()
    encoded = str(projection)
    assert "/Users/kevin" not in encoded
    assert "/workspace/private" not in encoded
    assert "/data/bots" not in encoded
    assert "unsafe-token" not in encoded
    assert "123456:ABC-secret-token" not in encoded
    assert projection["authorityFlags"]["telegramAttached"] is False
    assert projection["authorityFlags"]["productionChannelWrite"] is False


def test_telegram_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.channels.telegram_boundary")
forbidden = (
    "telegram",
    "aiohttp",
    "httpx",
    "requests",
    "google.adk.runners",
    "magi_agent.runtime.runner",
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


def test_telegram_runtime_config_rejects_attached_flags() -> None:
    with pytest.raises(ValueError):
        TelegramRuntimeConfig.model_validate(
            {
                "enabled": True,
                "telegramPollingAttached": True,
            }
        )
