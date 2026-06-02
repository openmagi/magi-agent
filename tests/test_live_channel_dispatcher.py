from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from magi_agent.channels.contract import ChannelRef


class FakeDispatchProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, status: str = "sent", provider_message_id: str | None = "msg-1") -> None:
        self.status = status
        self.provider_message_id = provider_message_id
        self.calls: list[object] = []

    def execute(self, request: object) -> Mapping[str, object]:
        self.calls.append(request)
        return {
            "status": self.status,
            "providerMessageId": self.provider_message_id,
            "rawProviderResponse": "Bearer provider-token /Users/kevin/private/channel.log",
        }


def _request(**overrides: object) -> object:
    from magi_agent.channels.dispatcher import ChannelDispatchRequest

    payload = {
        "operation": "dispatch.message",
        "requestId": "dispatch-1",
        "channel": ChannelRef(type="telegram", channelId="telegram-chat-1"),
        "providerName": "telegram-provider",
        "botIdDigest": "bot:abc123",
        "userIdDigest": "user:def456",
        "sessionKeyDigest": "session:789",
        "text": "hello",
        "metadata": {
            "Authorization": "Bearer live-token",
            "rawPath": "/workspace/private/channel.txt",
        },
    }
    payload.update(overrides)
    return ChannelDispatchRequest(**payload)


def test_channel_dispatcher_default_disabled_never_calls_provider() -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher

    provider = FakeDispatchProvider()
    decision = ChannelDispatcher(ChannelDispatchConfig()).dispatch(
        _request(),
        provider=provider,
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("channel_dispatch_disabled",)
    assert provider.calls == []
    assert decision.authority_flags.model_dump(by_alias=True) == {
        "providerCalled": False,
        "productionChannelWrite": False,
        "webAppCanaryAttached": False,
        "routeAttached": False,
    }


@pytest.mark.parametrize(
    ("overrides", "reason"),
    (
        ({"botIdDigest": ""}, "bot_id_digest_required"),
        ({"userIdDigest": ""}, "user_id_digest_required"),
        ({"sessionKeyDigest": ""}, "session_key_digest_required"),
        ({"channel": ChannelRef(type="discord", channelId="discord-1")}, "channel_route_not_selected"),
        ({"providerName": "discord-provider"}, "provider_not_allowlisted"),
    ),
)
def test_selected_channel_dispatch_requires_scope_and_provider_allowlist(
    overrides: dict[str, object],
    reason: str,
) -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher

    provider = FakeDispatchProvider()
    decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
        )
    ).dispatch(_request(**overrides), provider=provider)

    assert decision.status == "blocked"
    assert decision.reason_codes == (reason,)
    assert provider.calls == []


def test_duplicate_request_digest_returns_idempotent_receipt_without_second_provider_call() -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher

    provider = FakeDispatchProvider()
    dispatcher = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
        )
    )

    first = dispatcher.dispatch(_request(), provider=provider)
    second = dispatcher.dispatch(_request(), provider=provider)

    assert first.status == "recorded_local_fake"
    assert second.status == "recorded_local_fake"
    assert first.receipt is not None
    assert second.receipt is not None
    assert first.receipt.receipt_id == second.receipt.receipt_id
    assert first.request_digest == second.request_digest
    assert len(provider.calls) == 1
    assert second.reason_codes == ("channel_dispatch_idempotent_receipt",)


def test_channel_dispatcher_projects_sanitized_runtime_receipt() -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher

    decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
        )
    ).dispatch(_request(), provider=FakeDispatchProvider())

    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)
    assert projection["receipt"]["providerMessageId"] == "msg-1"
    assert projection["receipt"]["channelType"] == "telegram"
    assert "provider-token" not in rendered
    assert "live-token" not in rendered
    assert "/Users/kevin" not in rendered
    assert "/workspace/private" not in rendered


def test_channel_dispatcher_projection_redacts_home_paths_from_chunks() -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher

    decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
        )
    ).dispatch(
        _request(text="see /home and /home/openmagi/.ssh/id_rsa"),
        provider=FakeDispatchProvider(),
    )

    rendered = json.dumps(decision.public_projection(), sort_keys=True)
    assert "/home" not in rendered
    assert "/home/openmagi" not in rendered
    assert ".ssh" not in rendered


@pytest.mark.parametrize(
    "secret",
    (
        "github_pat_abcdefghijklmnopqrstuvwxyz123456",
        "xox" + "b-123456789012-abcdefghijklmnopqrstuvwxyz",
        "AKIA1234567890ABCDEF",
        "AIzaabcdefghijklmnopqrstuvwxyz123456789",
    ),
)
def test_channel_dispatcher_projection_redacts_common_token_families(secret: str) -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher

    decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
        )
    ).dispatch(
        _request(text=f"token {secret}", metadata={"safeNote": secret}),
        provider=FakeDispatchProvider(),
    )

    rendered = json.dumps(decision.public_projection(), sort_keys=True)
    assert secret not in rendered


def test_non_web_channels_do_not_attach_web_app_canary() -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher

    decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
            webAppCanaryRouteEnabled=True,
        )
    ).dispatch(_request(), provider=FakeDispatchProvider())

    assert decision.public_projection()["authorityFlags"]["webAppCanaryAttached"] is False


def test_channel_runtime_boundary_consumes_correlated_dispatch_decision_only() -> None:
    from magi_agent.channels.dispatcher import ChannelDispatchConfig, ChannelDispatcher
    from magi_agent.channels.runtime_boundary import (
        ChannelRuntimeBoundary,
        ChannelRuntimeConfig,
        ChannelRuntimeRequest,
    )

    request = _request()
    dispatch_decision = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-provider",),
        )
    ).dispatch(request, provider=FakeDispatchProvider())
    runtime_request = ChannelRuntimeRequest(
        operation="dispatch.message",
        requestId="dispatch-1",
        channel=ChannelRef(type="telegram", channelId="telegram-chat-1"),
        text="hello",
    )
    mismatched_request = runtime_request.model_copy(update={"request_id": "other-request"})
    boundary = ChannelRuntimeBoundary(ChannelRuntimeConfig(enabled=True))

    consumed = boundary.consume_dispatch_decision(runtime_request, dispatch_decision)
    mismatched = boundary.consume_dispatch_decision(mismatched_request, dispatch_decision)
    forged = boundary.consume_dispatch_decision(
        runtime_request,
        type(
            "ForgedDispatch",
            (),
            {
                "status": "recorded_local_fake",
                "receipt": dispatch_decision.receipt,
            },
        )(),
    )

    assert consumed.status == "recorded_local_fake"
    assert consumed.reason_codes == ("channel_dispatch_receipt_consumed",)
    assert mismatched.status == "blocked"
    assert mismatched.reason_codes == ("channel_dispatch_receipt_mismatch",)
    assert forged.status == "blocked"
    assert forged.reason_codes == ("channel_dispatch_decision_invalid",)
