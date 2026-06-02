from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import ValidationError

from magi_agent.channels.contract import (
    ChannelAdapterManifest,
    ChannelDeliveryReceipt,
    ChannelDeliveryRequest,
    ChannelRef,
    channel_adapter_manifests,
)


def test_catalog_returns_prd_order_with_disabled_attachment_flags_and_capabilities() -> None:
    manifests = channel_adapter_manifests()

    assert tuple(manifest.channel_type for manifest in manifests) == (
        "web",
        "app",
        "telegram",
        "discord",
    )
    assert all(manifest.default_enabled is False for manifest in manifests)
    assert all(manifest.traffic_attached is False for manifest in manifests)
    assert all(manifest.execution_attached is False for manifest in manifests)

    by_type = {manifest.channel_type: manifest for manifest in manifests}
    assert by_type["web"].supports_sse is True
    assert by_type["app"].supports_sse is True
    assert by_type["telegram"].supports_polling is True
    assert by_type["telegram"].supports_stale_webhook_mitigation is True
    assert by_type["discord"].supports_polling is False
    assert all(manifest.max_text_chars > 0 for manifest in manifests)


def test_catalog_returns_defensive_copies() -> None:
    first = channel_adapter_manifests()
    second = channel_adapter_manifests()

    assert first == second
    assert first is not second
    assert all(left is not right for left, right in zip(first, second, strict=True))

    changed = first[0].model_copy(update={"display_name": "Changed"})
    assert changed.display_name == "Changed"
    assert channel_adapter_manifests()[0].display_name == "Web Chat"

    with pytest.raises(ValidationError):
        first[0].display_name = "Changed"  # type: ignore[misc]


def test_delivery_request_accepts_snake_and_camel_case_and_dumps_camel_case() -> None:
    camel = ChannelDeliveryRequest.model_validate(
        {
            "requestId": "req_1",
            "channel": {"type": "telegram", "channelId": "chat_123"},
            "sessionKey": "bot:session:1",
            "content": "hello",
            "locale": "en",
            "artifactRefs": ["artifact_1"],
            "fileRefs": ["file_1"],
            "metadata": {"opaque": "caller-owned"},
        }
    )
    snake = ChannelDeliveryRequest(
        request_id="req_2",
        channel=ChannelRef(type="web", channel_id="user_123"),
        session_key="bot:session:2",
        text="hello",
        artifact_refs=("artifact_2",),
        file_refs=("file_2",),
        metadata={"opaque": "caller-owned"},
    )

    assert camel.request_id == "req_1"
    assert camel.channel.channel_id == "chat_123"
    assert snake.session_key == "bot:session:2"

    dumped = snake.model_dump(by_alias=True)
    assert dumped["requestId"] == "req_2"
    assert dumped["channel"]["channelId"] == "user_123"
    assert dumped["sessionKey"] == "bot:session:2"
    assert dumped["artifactRefs"] == ("artifact_2",)
    assert dumped["fileRefs"] == ("file_2",)
    assert dumped["metadata"] == {"opaque": "caller-owned"}


def test_delivery_request_metadata_is_defensively_copied_and_immutable() -> None:
    caller_nested: dict[str, object] = {"enabled": True}
    caller_metadata: dict[str, object] = {
        "opaque": "caller-owned",
        "nested": caller_nested,
        "items": ["one"],
    }
    request = ChannelDeliveryRequest(
        request_id="req_immutable",
        channel=ChannelRef(type="web", channel_id="user_123"),
        session_key="bot:session:immutable",
        metadata=caller_metadata,
    )

    caller_metadata["opaque"] = "mutated"
    caller_metadata["new"] = "value"
    caller_nested["enabled"] = False

    assert request.model_dump(by_alias=True)["metadata"] == {
        "opaque": "caller-owned",
        "nested": {"enabled": True},
        "items": ["one"],
    }

    with pytest.raises(TypeError):
        request.metadata["opaque"] = "direct mutation"  # type: ignore[index]

    assert request.model_dump(by_alias=True)["metadata"] == {
        "opaque": "caller-owned",
        "nested": {"enabled": True},
        "items": ["one"],
    }


def test_delivery_request_default_metadata_is_immutable_and_dumps_empty_dict() -> None:
    request = ChannelDeliveryRequest(
        request_id="req_default_metadata",
        channel=ChannelRef(type="web", channel_id="user_123"),
        session_key="bot:session:default-metadata",
    )

    assert request.model_dump(by_alias=True)["metadata"] == {}

    with pytest.raises(TypeError):
        request.metadata["opaque"] = "direct mutation"  # type: ignore[index]

    assert request.model_dump(by_alias=True)["metadata"] == {}


@pytest.mark.parametrize(
    "metadata",
    (
        {"unsupported": {"not-json-like"}},
        {"nested": {"unsupported": bytearray(b"mutable")}},
        {"nested": [{"unsupported": object()}]},
    ),
)
def test_delivery_request_metadata_rejects_unsupported_mutable_values(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChannelDeliveryRequest(
            request_id="req_unsupported_metadata",
            channel=ChannelRef(type="web", channel_id="user_123"),
            session_key="bot:session:unsupported-metadata",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    (
        {"unsupported": ("caller", "tuple")},
        {"nested": [{"unsupported": ("caller", "tuple")}]},
    ),
)
def test_delivery_request_metadata_rejects_tuples(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChannelDeliveryRequest(
            request_id="req_tuple_metadata",
            channel=ChannelRef(type="web", channel_id="user_123"),
            session_key="bot:session:tuple-metadata",
            metadata=metadata,
        )


@pytest.mark.parametrize(
    "metadata",
    (
        {"unsupported": float("nan")},
        {"unsupported": float("inf")},
        {"unsupported": float("-inf")},
        {"nested": [{"unsupported": float("nan")}]},
    ),
)
def test_delivery_request_metadata_rejects_non_finite_floats(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ChannelDeliveryRequest(
            request_id="req_non_finite_metadata",
            channel=ChannelRef(type="web", channel_id="user_123"),
            session_key="bot:session:non-finite-metadata",
            metadata=metadata,
        )


def test_delivery_receipt_accepts_snake_and_camel_case_and_dumps_camel_case() -> None:
    camel = ChannelDeliveryReceipt.model_validate(
        {
            "receiptId": "rcpt_1",
            "requestId": "req_1",
            "channel": {"type": "app", "channelId": "user_123"},
            "status": "sent",
            "providerMessageId": "msg_123",
            "deliveredAt": "2026-05-15T12:00:00Z",
            "artifactRefs": ["artifact_1"],
            "fileRefs": ["file_1"],
            "transcriptEventId": "evt_123",
        }
    )
    snake = ChannelDeliveryReceipt(
        receipt_id="rcpt_2",
        request_id="req_2",
        channel=ChannelRef(type="discord", channel_id="channel_123"),
        status="failed",
        error_code="provider_error",
        error_message="delivery failed",
    )

    assert camel.receipt_id == "rcpt_1"
    assert camel.delivered_at is not None
    assert snake.error_code == "provider_error"

    dumped = snake.model_dump(by_alias=True)
    assert dumped["receiptId"] == "rcpt_2"
    assert dumped["requestId"] == "req_2"
    assert dumped["channel"]["channelId"] == "channel_123"
    assert dumped["errorCode"] == "provider_error"
    assert dumped["errorMessage"] == "delivery failed"
    assert dumped["transcriptEventId"] is None


def test_manifest_catalog_has_no_secret_contract_fields_or_values() -> None:
    serialized = repr([manifest.model_dump(by_alias=True) for manifest in channel_adapter_manifests()]).lower()

    assert "token" not in serialized
    assert "secret" not in serialized
    assert "credential" not in serialized
    assert "password" not in serialized


@pytest.mark.parametrize(
    "attached_flag",
    ("defaultEnabled", "trafficAttached", "executionAttached"),
)
def test_direct_manifest_construction_rejects_enabled_or_attached_flags(attached_flag: str) -> None:
    payload: dict[str, object] = {
        "channelType": "web",
        "displayName": "Web Chat",
        "maxTextChars": 16_000,
        attached_flag: True,
    }

    with pytest.raises(ValidationError):
        ChannelAdapterManifest.model_validate(payload)


@pytest.mark.parametrize(
    "attached_flag",
    ("defaultEnabled", "trafficAttached", "executionAttached"),
)
def test_manifest_model_copy_revalidates_enabled_or_attached_flags(attached_flag: str) -> None:
    manifest = channel_adapter_manifests()[0]

    with pytest.raises(ValidationError):
        manifest.model_copy(update={attached_flag: True})


def test_manifest_model_copy_rejects_unexpected_extra_fields() -> None:
    manifest = channel_adapter_manifests()[0]

    with pytest.raises(ValidationError):
        manifest.model_copy(update={"runnerKwargs": {"attach": True}})


@pytest.mark.parametrize(
    "metadata",
    (
        {"unsupported": object()},
        {"unsupported": ("caller", "tuple")},
        {"unsupported": float("nan")},
    ),
)
def test_delivery_request_model_copy_revalidates_metadata(metadata: dict[str, object]) -> None:
    request = ChannelDeliveryRequest(
        request_id="req_copy_metadata",
        channel=ChannelRef(type="web", channel_id="user_123"),
        session_key="bot:session:copy-metadata",
        metadata={"opaque": "caller-owned"},
    )

    with pytest.raises(ValidationError):
        request.model_copy(update={"metadata": metadata})


def test_delivery_request_model_copy_rejects_unexpected_extra_fields() -> None:
    request = ChannelDeliveryRequest(
        request_id="req_copy_extra",
        channel=ChannelRef(type="web", channel_id="user_123"),
        session_key="bot:session:copy-extra",
    )

    with pytest.raises(ValidationError):
        request.model_copy(update={"runnerKwargs": {"attach": True}})


def test_channel_contract_import_stays_traffic_free_in_fresh_process() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.channels")
importlib.import_module("magi_agent.channels.contract")
forbidden_modules = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport",
    "magi_agent.tools.dispatcher",
    "magi_agent.hooks.bus",
    "magi_agent.plugins",
)
loaded = [module for module in forbidden_modules if module in sys.modules]
if loaded:
    raise AssertionError(f"channel contract import loaded forbidden modules: {loaded}")
""",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
