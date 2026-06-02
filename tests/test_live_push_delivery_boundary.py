from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from openmagi_core_agent.channels.contract import ChannelRef


class FakePushProvider:
    openmagi_local_fake_provider = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[object] = []

    def push(self, request: object) -> Mapping[str, object]:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("push failed Bearer provider-token /Users/kevin/private")
        return {
            "status": "queued",
            "providerMessageId": "push-1",
            "rawPayload": "Bearer provider-token /workspace/private/push.json",
        }


def _request(**overrides: object) -> object:
    from openmagi_core_agent.channels.push_delivery import PushDeliveryRequest

    payload = {
        "requestId": "push-1",
        "channel": ChannelRef(type="app", channelId="app-session-1"),
        "route": "app",
        "providerName": "app-push-provider",
        "botIdDigest": "bot:abc123",
        "userIdDigest": "user:def456",
        "sessionKeyDigest": "session:789",
        "title": "Update",
        "body": "done",
        "metadata": {
            "Authorization": "Bearer live-token",
            "rawPath": "/workspace/private/push.json",
        },
    }
    payload.update(overrides)
    return PushDeliveryRequest(**payload)


def test_push_delivery_default_disabled_never_calls_provider() -> None:
    from openmagi_core_agent.channels.push_delivery import PushDeliveryBoundary, PushDeliveryConfig

    provider = FakePushProvider()
    decision = PushDeliveryBoundary(PushDeliveryConfig()).deliver(
        _request(),
        provider=provider,
    )

    assert decision.status == "disabled"
    assert decision.reason_codes == ("push_delivery_disabled",)
    assert provider.calls == []
    assert decision.public_projection()["authorityFlags"] == {
        "providerCalled": False,
        "productionPushWrite": False,
        "webAppCanaryAttached": False,
        "routeAttached": False,
    }


def test_push_delivery_requires_explicit_route_and_blocks_unrelated_channel() -> None:
    from openmagi_core_agent.channels.push_delivery import PushDeliveryBoundary, PushDeliveryConfig

    boundary = PushDeliveryBoundary(
        PushDeliveryConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("app",),
            providerAllowlist=("app-push-provider",),
        )
    )
    missing_route = boundary.deliver(_request(route=None), provider=FakePushProvider())
    unrelated = boundary.deliver(
        _request(channel=ChannelRef(type="telegram", channelId="chat-1"), route="app"),
        provider=FakePushProvider(),
    )

    assert missing_route.status == "blocked"
    assert missing_route.reason_codes == ("channel_route_required",)
    assert unrelated.status == "blocked"
    assert unrelated.reason_codes == ("channel_route_mismatch",)


def test_push_delivery_records_local_fake_intent_without_web_app_canary_for_external_channel() -> None:
    from openmagi_core_agent.channels.push_delivery import PushDeliveryBoundary, PushDeliveryConfig

    provider = FakePushProvider()
    decision = PushDeliveryBoundary(
        PushDeliveryConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("telegram",),
            providerAllowlist=("telegram-push-provider",),
            webAppCanaryRouteEnabled=True,
        )
    ).deliver(
        _request(
            channel=ChannelRef(type="telegram", channelId="chat-1"),
            route="telegram",
            providerName="telegram-push-provider",
        ),
        provider=provider,
    )

    projection = decision.public_projection()
    rendered = json.dumps(projection, sort_keys=True)
    assert decision.status == "recorded_local_fake"
    assert len(provider.calls) == 1
    assert projection["receipt"]["providerMessageId"] == "push-1"
    assert projection["authorityFlags"]["webAppCanaryAttached"] is False
    assert "provider-token" not in rendered
    assert "live-token" not in rendered
    assert "/workspace/private" not in rendered


def test_push_delivery_projection_redacts_home_paths_from_chunks() -> None:
    from openmagi_core_agent.channels.push_delivery import PushDeliveryBoundary, PushDeliveryConfig

    decision = PushDeliveryBoundary(
        PushDeliveryConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("app",),
            providerAllowlist=("app-push-provider",),
        )
    ).deliver(
        _request(body="open /home and /home/openmagi/.ssh/id_rsa"),
        provider=FakePushProvider(),
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
def test_push_delivery_projection_redacts_common_token_families(secret: str) -> None:
    from openmagi_core_agent.channels.push_delivery import PushDeliveryBoundary, PushDeliveryConfig

    decision = PushDeliveryBoundary(
        PushDeliveryConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("app",),
            providerAllowlist=("app-push-provider",),
        )
    ).deliver(
        _request(body=f"token {secret}", metadata={"safeNote": secret}),
        provider=FakePushProvider(),
    )

    rendered = json.dumps(decision.public_projection(), sort_keys=True)
    assert secret not in rendered


def test_push_delivery_failure_notice_is_generated_once_and_redacted() -> None:
    from openmagi_core_agent.channels.push_delivery import PushDeliveryBoundary, PushDeliveryConfig

    provider = FakePushProvider(fail=True)
    boundary = PushDeliveryBoundary(
        PushDeliveryConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("app",),
            providerAllowlist=("app-push-provider",),
        )
    )

    first = boundary.deliver(_request(), provider=provider)
    second = boundary.deliver(_request(), provider=provider)
    rendered = json.dumps(first.public_projection(), sort_keys=True)

    assert first.status == "error"
    assert second.status == "error"
    assert first.failure_notice is not None
    assert second.failure_notice is not None
    assert first.failure_notice.notice_id == second.failure_notice.notice_id
    assert len(provider.calls) == 1
    assert "provider-token" not in rendered
    assert "/Users/kevin" not in rendered
