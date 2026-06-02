from __future__ import annotations

import subprocess
import sys

from openmagi_core_agent.channels.contract import ChannelRef


def test_scheduler_disabled_selects_no_due_turns() -> None:
    from openmagi_core_agent.harness.scheduler_runtime import (
        SchedulerRuntimeBoundary,
        SchedulerRuntimeConfig,
        SchedulerTickRequest,
    )

    decision = SchedulerRuntimeBoundary(SchedulerRuntimeConfig()).tick(
        SchedulerTickRequest(
            requestId="tick-1",
            now=1_000,
            ownerDigest="owner:abc",
            dueRefs=("cron:due",),
        ),
    )

    assert decision.status == "disabled"
    assert decision.due_turns == ()
    assert decision.reason_codes == ("scheduler_runtime_disabled",)
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_scheduler_tick_requires_fresh_lease_and_deduplicates_due_refs() -> None:
    from openmagi_core_agent.harness.scheduler_runtime import (
        SchedulerLease,
        SchedulerRuntimeBoundary,
        SchedulerRuntimeConfig,
        SchedulerTickRequest,
    )

    decision = SchedulerRuntimeBoundary(
        SchedulerRuntimeConfig(enabled=True, localFakeSchedulerEnabled=True),
    ).tick(
        SchedulerTickRequest(
            requestId="tick-2",
            now=1_000,
            ownerDigest="owner:abc",
            dueRefs=("cron:a", "cron:a", "goal:b"),
            lease=SchedulerLease(
                leaseId="lease:abc",
                ownerDigest="owner:abc",
                acquiredAt=950,
                expiresAt=2_000,
            ),
        ),
    )

    assert decision.status == "tick_recorded_local_fake"
    assert tuple(turn.source_ref for turn in decision.due_turns) == ("cron:a", "goal:b")
    assert all(turn.execution_allowed is False for turn in decision.due_turns)
    assert decision.public_projection()["authorityFlags"]["backgroundSchedulerAttached"] is False


def test_scheduler_tick_blocks_stale_or_mismatched_lease() -> None:
    from openmagi_core_agent.harness.scheduler_runtime import (
        SchedulerLease,
        SchedulerRuntimeBoundary,
        SchedulerRuntimeConfig,
        SchedulerTickRequest,
    )

    boundary = SchedulerRuntimeBoundary(
        SchedulerRuntimeConfig(enabled=True, localFakeSchedulerEnabled=True),
    )
    stale = boundary.tick(
        SchedulerTickRequest(
            requestId="tick-stale",
            now=2_001,
            ownerDigest="owner:abc",
            dueRefs=("cron:a",),
            lease=SchedulerLease(
                leaseId="lease:abc",
                ownerDigest="owner:abc",
                acquiredAt=950,
                expiresAt=2_000,
            ),
        ),
    )
    mismatched = boundary.tick(
        SchedulerTickRequest(
            requestId="tick-mismatch",
            now=1_000,
            ownerDigest="owner:abc",
            dueRefs=("cron:a",),
            lease=SchedulerLease(
                leaseId="lease:def",
                ownerDigest="owner:def",
                acquiredAt=950,
                expiresAt=2_000,
            ),
        ),
    )

    assert stale.status == "blocked"
    assert stale.reason_codes == ("scheduler_lease_stale",)
    assert mismatched.status == "blocked"
    assert mismatched.reason_codes == ("scheduler_lease_owner_mismatch",)


def test_scheduler_config_and_authority_flags_cannot_be_forged_with_model_copy() -> None:
    from openmagi_core_agent.harness.scheduler_runtime import (
        SchedulerAuthorityFlags,
        SchedulerRuntimeConfig,
    )

    config = SchedulerRuntimeConfig().model_copy(
        update={
            "backgroundSchedulerAttached": True,
            "background_scheduler_attached": True,
            "productionChannelWriteEnabled": True,
            "production_channel_write_enabled": True,
            "routeAttached": True,
            "route_attached": True,
        }
    )
    flags = SchedulerAuthorityFlags().model_copy(
        update={
            "backgroundSchedulerAttached": True,
            "background_scheduler_attached": True,
            "backgroundTaskStarted": True,
            "background_task_started": True,
            "productionChannelWrite": True,
            "production_channel_write": True,
            "channelDeliveryPerformed": True,
            "channel_delivery_performed": True,
            "routeAttached": True,
            "route_attached": True,
        }
    )

    assert config.background_scheduler_attached is False
    assert config.production_channel_write_enabled is False
    assert config.route_attached is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_scheduled_channel_delivery_requires_dispatcher_receipt() -> None:
    from openmagi_core_agent.channels.dispatcher import (
        ChannelDispatchConfig,
        ChannelDispatchDecision,
        ChannelDispatchRequest,
        ChannelDispatcher,
    )
    from openmagi_core_agent.harness.scheduler_runtime import (
        SchedulerDeliveryRequest,
        SchedulerRuntimeBoundary,
        SchedulerRuntimeConfig,
    )

    class FakeDispatchProvider:
        openmagi_local_fake_provider = True

        def execute(self, request: ChannelDispatchRequest) -> dict[str, object]:
            return {
                "status": "sent",
                "providerMessageId": "msg-1",
                "channelId": request.channel.channel_id,
            }

    dispatcher = ChannelDispatcher(
        ChannelDispatchConfig(
            enabled=True,
            localFakeProviderEnabled=True,
            selectedChannelRoutes=("web",),
            providerAllowlist=("fake-channel",),
        )
    )
    delivery = SchedulerRuntimeBoundary(
        SchedulerRuntimeConfig(enabled=True, localFakeSchedulerEnabled=True),
    ).deliver(
        SchedulerDeliveryRequest(
            requestId="deliver-1",
            ownerDigest="owner:abc",
            sourceRef="cron:daily",
            channel=ChannelRef(type="web", channelId="web-session"),
            providerName="fake-channel",
            text="scheduled update",
            botIdDigest="bot:abc",
            sessionKeyDigest="session:abc",
        ),
        dispatcher=dispatcher,
        provider=FakeDispatchProvider(),
    )

    assert delivery.status == "delivery_recorded_local_fake"
    assert delivery.delivery_receipt is not None
    assert delivery.delivery_receipt.provider_message_id == "msg-1"
    assert delivery.authority_flags.production_channel_write is False

    missing = SchedulerRuntimeBoundary(
        SchedulerRuntimeConfig(enabled=True, localFakeSchedulerEnabled=True),
    )._delivery_decision_from_dispatch(  # noqa: SLF001 - intentional boundary regression
        SchedulerDeliveryRequest(
            requestId="deliver-2",
            ownerDigest="owner:abc",
            sourceRef="cron:daily",
            channel=ChannelRef(type="web", channelId="web-session"),
            providerName="fake-channel",
            text="scheduled update",
            botIdDigest="bot:abc",
            sessionKeyDigest="session:abc",
        ),
        ChannelDispatchDecision(
            status="blocked",
            requestId="dispatch-2",
            requestDigest="digest",
            reasonCodes=("provider_message_ack_required",),
        ),
    )
    assert missing.status == "blocked"
    assert missing.reason_codes == ("scheduled_channel_delivery_receipt_required",)


def test_scheduler_runtime_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("openmagi_core_agent.harness.scheduler_runtime")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "openmagi_core_agent.adk_bridge",
    "openmagi_core_agent.transport",
    "openmagi_core_agent.routing",
    "openmagi_core_agent.deploy",
    "openmagi_core_agent.chat_proxy",
    "openmagi_core_agent.runtime_selector",
    "openmagi_core_agent.k8s",
    "subprocess",
    "kubernetes",
    "telegram",
    "discord",
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "urllib",
    "playwright",
    "selenium",
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
