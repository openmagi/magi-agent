from __future__ import annotations

import asyncio
import subprocess
import sys

from openmagi_core_agent.channels.contract import ChannelRef
from openmagi_core_agent.channels.runtime_boundary import (
    ChannelRuntimeBoundary,
    ChannelRuntimeConfig,
    ChannelRuntimeRequest,
)


class FakeChannelProvider:
    openmagi_local_fake_provider = True

    def __init__(
        self,
        *,
        status: str = "sent",
        provider_message_id: str | None = "msg-1",
        fail: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.status = status
        self.provider_message_id = provider_message_id
        self.fail = fail

    async def execute(self, request: ChannelRuntimeRequest) -> dict[str, object]:
        self.calls.append(request.operation)
        if self.fail:
            raise RuntimeError("channel raw_tool_log /Users/kevin/private ghp_channelSecret")
        return {
            "status": self.status,
            "providerMessageId": self.provider_message_id,
        }


def _request(
    operation: str,
    *,
    channel_type: str = "discord",
    text: str | None = "hello",
    file_ref: str | None = None,
    provider_message_id: str | None = None,
) -> ChannelRuntimeRequest:
    return ChannelRuntimeRequest(
        operation=operation,
        requestId="req-1",
        channel=ChannelRef(type=channel_type, channelId="chan-1"),
        text=text,
        fileRef=file_ref,
        providerMessageId=provider_message_id,
    )


def test_channel_runtime_boundary_is_disabled_by_default() -> None:
    provider = FakeChannelProvider()
    decision = asyncio.run(
        ChannelRuntimeBoundary(ChannelRuntimeConfig()).execute(
            _request("dispatch.message"),
            provider=provider,
        )
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("channel_runtime_disabled",)
    assert provider.calls == []
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_channel_dispatch_chunks_by_channel_and_records_ack_without_delivery_authority() -> None:
    provider = FakeChannelProvider()
    boundary = ChannelRuntimeBoundary(
        ChannelRuntimeConfig(enabled=True, localFakeChannelProviderEnabled=True),
    )

    decision = asyncio.run(
        boundary.execute(
            _request("dispatch.message", channel_type="discord", text="A" * 3900),
            provider=provider,
        )
    )

    projection = decision.public_projection()
    assert decision.status == "recorded_local_fake"
    assert len(projection["receipt"]["chunks"]) == 3
    assert all(len(chunk) <= 1900 for chunk in projection["receipt"]["chunks"])
    assert projection["receipt"]["providerMessageId"] == "msg-1"
    assert projection["authorityFlags"]["productionChannelWrite"] is False
    assert projection["authorityFlags"]["channelProviderCalled"] is False


def test_channel_runtime_typing_and_download_are_metadata_only() -> None:
    provider = FakeChannelProvider(provider_message_id=None)
    boundary = ChannelRuntimeBoundary(
        ChannelRuntimeConfig(enabled=True, localFakeChannelProviderEnabled=True),
    )

    typing = asyncio.run(
        boundary.execute(_request("typing.start", text=None), provider=provider)
    )
    download = asyncio.run(
        boundary.execute(
            _request("file.download", text=None, file_ref="file:report"),
            provider=provider,
        )
    )

    assert typing.status == "recorded_local_fake"
    assert download.status == "recorded_local_fake"
    assert download.public_projection()["authorityFlags"]["fileDownloadPerformed"] is False
    assert provider.calls == ["typing.start", "file.download"]


def test_channel_runtime_blocks_private_payloads_raw_paths_failed_and_missing_acks() -> None:
    boundary = ChannelRuntimeBoundary(
        ChannelRuntimeConfig(enabled=True, localFakeChannelProviderEnabled=True),
    )

    private = asyncio.run(
        boundary.execute(
            _request(
                "dispatch.message",
                text="safe\nraw_tool_log: /Users/kevin/private ghp_channelSecret",
            ),
            provider=FakeChannelProvider(),
        )
    )
    raw_file = asyncio.run(
        boundary.execute(
            _request("file.send", text=None, file_ref="/Users/kevin/private.pdf"),
            provider=FakeChannelProvider(),
        )
    )
    failed = asyncio.run(
        boundary.execute(
            _request("dispatch.message"),
            provider=FakeChannelProvider(status="failed", provider_message_id="msg-1"),
        )
    )
    missing = asyncio.run(
        boundary.execute(
            _request("dispatch.message"),
            provider=FakeChannelProvider(provider_message_id=None),
        )
    )

    assert private.status == "blocked"
    assert private.reason_codes == ("private_channel_payload_blocked",)
    assert raw_file.status == "blocked"
    assert raw_file.reason_codes == ("raw_file_ref_blocked",)
    assert failed.status == "blocked"
    assert failed.reason_codes == ("channel_provider_ack_failed",)
    assert missing.status == "blocked"
    assert missing.reason_codes == ("channel_provider_ack_missing",)


def test_channel_runtime_rejects_unmarked_provider_and_sanitizes_provider_errors() -> None:
    class UnmarkedProvider(FakeChannelProvider):
        openmagi_local_fake_provider = False

    boundary = ChannelRuntimeBoundary(
        ChannelRuntimeConfig(enabled=True, localFakeChannelProviderEnabled=True),
    )

    untrusted = asyncio.run(
        boundary.execute(_request("dispatch.message"), provider=UnmarkedProvider())
    )
    errored = asyncio.run(
        boundary.execute(_request("dispatch.message"), provider=FakeChannelProvider(fail=True))
    )
    encoded = str(errored.public_projection())

    assert untrusted.status == "blocked"
    assert untrusted.reason_codes == ("local_fake_channel_provider_untrusted",)
    assert errored.status == "error"
    assert "raw_tool_log" not in encoded
    assert "/Users/kevin" not in encoded
    assert "ghp_channelSecret" not in encoded


def test_channel_runtime_diagnostic_metadata_cannot_forge_authority_flags() -> None:
    boundary = ChannelRuntimeBoundary(
        ChannelRuntimeConfig(enabled=True, localFakeChannelProviderEnabled=True),
    )

    decision = asyncio.run(
        boundary.execute(
            ChannelRuntimeRequest(
                operation="dispatch.message",
                requestId="req-authority",
                channel=ChannelRef(type="web", channelId="chan-1"),
                text="hello",
                metadata={
                    "productionChannelWritesEnabled": True,
                    "routeAttached": True,
                    "providerCalled": True,
                    "trusted": True,
                    "authoritative": True,
                    "safeNote": "safe",
                },
            ),
            provider=FakeChannelProvider(),
        )
    )
    projection = decision.public_projection()
    diagnostic = str(projection["diagnosticMetadata"])

    assert decision.status == "recorded_local_fake"
    assert "productionChannelWritesEnabled" not in diagnostic
    assert "routeAttached" not in diagnostic
    assert "providerCalled" not in diagnostic
    assert "trusted" not in diagnostic
    assert "authoritative" not in diagnostic
    assert projection["diagnosticMetadata"]["safeNote"] == "safe"
    assert projection["authorityFlags"]["productionChannelWrite"] is False


def test_channel_runtime_diagnostic_metadata_redacts_sensitive_keys() -> None:
    boundary = ChannelRuntimeBoundary(
        ChannelRuntimeConfig(enabled=True, localFakeChannelProviderEnabled=True),
    )

    decision = asyncio.run(
        boundary.execute(
            ChannelRuntimeRequest(
                operation="dispatch.message",
                requestId="req-sensitive-keys",
                channel=ChannelRef(type="web", channelId="chan-1"),
                text="hello",
                metadata={
                    "/home/openmagi/.ssh/id_rsa": "safe",
                    "/home": "safe",
                    "Bearer sk-test-abcdefghijklmnopqrstuvwxyz": "safe",
                    "github_pat_abcdefghijklmnopqrstuvwxyz123456": "safe",
                    ("xox" + "b-123456789012-abcdefghijklmnopqrstuvwxyz"): "safe",
                    "AKIA1234567890ABCDEF": "safe",
                    "AIzaabcdefghijklmnopqrstuvwxyz123456789": "safe",
                    "safeNote": "safe",
                },
            ),
            provider=FakeChannelProvider(),
        )
    )

    rendered = str(decision.public_projection()["diagnosticMetadata"])
    assert decision.public_projection()["diagnosticMetadata"] == {"safeNote": "safe"}
    assert "/home" not in rendered
    assert "sk-test" not in rendered
    assert "github_pat_" not in rendered
    assert "xoxb-" not in rendered
    assert "AKIA" not in rendered
    assert "AIza" not in rendered


def test_channel_runtime_decision_model_copy_cannot_forge_authority_flags() -> None:
    decision = ChannelRuntimeBoundary(
        ChannelRuntimeConfig(enabled=True, localFakeChannelProviderEnabled=True),
    ).consume_dispatch_decision(
        _request("dispatch.message"),
        object(),
    )

    copied = decision.model_copy(
        update={
            "authority_flags": {
                "channelProviderCalled": True,
                "productionChannelWrite": True,
                "pollingAttached": True,
                "fileDownloadPerformed": True,
                "routeAttached": True,
            },
            "authorityFlags": {
                "channelProviderCalled": True,
                "productionChannelWrite": True,
                "pollingAttached": True,
                "fileDownloadPerformed": True,
                "routeAttached": True,
            },
        }
    )

    assert set(copied.public_projection()["authorityFlags"].values()) == {False}


def test_channel_runtime_config_construct_and_copy_cannot_enable_production_flags() -> None:
    constructed = ChannelRuntimeConfig.model_construct(
        enabled=True,
        local_fake_channel_provider_enabled=True,
        production_channel_writes_enabled=True,
        polling_attached=True,
        route_attached=True,
    )
    copied = ChannelRuntimeConfig().model_copy(
        update={
            "enabled": True,
            "localFakeChannelProviderEnabled": True,
            "productionChannelWritesEnabled": True,
            "pollingAttached": True,
            "routeAttached": True,
        }
    )

    assert constructed.model_dump(by_alias=True) == {
        "enabled": False,
        "localFakeChannelProviderEnabled": False,
        "productionChannelWritesEnabled": False,
        "pollingAttached": False,
        "routeAttached": False,
    }
    assert copied.model_dump(by_alias=True) == {
        "enabled": True,
        "localFakeChannelProviderEnabled": True,
        "productionChannelWritesEnabled": False,
        "pollingAttached": False,
        "routeAttached": False,
    }


def test_channel_runtime_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.channels.runtime_boundary")
forbidden = (
    "discord",
    "telegram",
    "requests",
    "httpx",
    "subprocess",
    "google.adk.runners",
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
