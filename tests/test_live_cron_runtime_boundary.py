from __future__ import annotations

import json
import subprocess
import sys

from magi_agent.channels.contract import ChannelRef


def _cron(**overrides: object) -> object:
    from magi_agent.harness.cron_runtime import CronDefinition

    payload = {
        "cronId": "cron:daily",
        "ownerDigest": "owner:abc",
        "expression": "*/5 * * * *",
        "timezone": "UTC",
        "promptPreview": "daily summary",
        "deliveryChannel": ChannelRef(type="web", channelId="web-session"),
        "enabled": True,
        "nextFireAt": 1_000,
        "lastFiredAt": None,
        "consecutiveFailures": 0,
    }
    payload.update(overrides)
    return CronDefinition(**payload)


def test_cron_hydration_disabled_does_not_emit_due_turns() -> None:
    from magi_agent.harness.cron_runtime import (
        CronHydrationRequest,
        CronRuntimeBoundary,
        CronRuntimeConfig,
    )

    decision = CronRuntimeBoundary(CronRuntimeConfig()).hydrate(
        CronHydrationRequest(requestId="cron-1", now=1_000, crons=(_cron(),)),
    )

    assert decision.status == "disabled"
    assert decision.due_turns == ()
    assert decision.reason_codes == ("cron_runtime_disabled",)
    assert set(decision.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_cron_hydration_handles_timezone_missed_window_duplicate_and_resume() -> None:
    from magi_agent.harness.cron_runtime import (
        CronHydrationRequest,
        CronRuntimeBoundary,
        CronRuntimeConfig,
    )

    boundary = CronRuntimeBoundary(CronRuntimeConfig(enabled=True, localFakeCronEnabled=True))
    decision = boundary.hydrate(
        CronHydrationRequest(
            requestId="cron-2",
            now=1_600,
            crons=(
                _cron(),
                _cron(cronId="cron:paused", enabled=False, paused=True, nextFireAt=900),
                _cron(cronId="cron:duplicate", nextFireAt=1_000),
                _cron(cronId="cron:duplicate", nextFireAt=1_000),
                _cron(cronId="cron:future", nextFireAt=2_000),
            ),
            firedRefs=("cron:duplicate",),
        ),
    )

    assert decision.status == "hydrated_local_fake"
    assert tuple(turn.source_ref for turn in decision.due_turns) == ("cron:daily",)
    assert decision.suppressed_refs == ("cron:paused", "cron:duplicate", "cron:future")
    assert decision.updated_crons[0].last_fired_at == 1_600
    assert decision.updated_crons[0].next_fire_at > 1_600
    assert decision.updated_crons[0].timezone == "UTC"


def test_cron_next_fire_respects_expression_interval_and_timezone() -> None:
    from magi_agent.harness.cron_runtime import (
        CronHydrationRequest,
        CronRuntimeBoundary,
        CronRuntimeConfig,
    )

    boundary = CronRuntimeBoundary(CronRuntimeConfig(enabled=True, localFakeCronEnabled=True))
    interval = boundary.hydrate(
        CronHydrationRequest(
            requestId="cron-interval",
            now=1_600_000,
            crons=(
                _cron(
                    cronId="cron:ten-minute",
                    expression="*/10 * * * *",
                    nextFireAt=1_000,
                ),
            ),
        ),
    )
    utc_daily = boundary.hydrate(
        CronHydrationRequest(
            requestId="cron-utc",
            now=1,
            crons=(
                _cron(
                    cronId="cron:utc-daily",
                    expression="0 9 * * *",
                    timezone="UTC",
                    nextFireAt=0,
                ),
            ),
        ),
    )
    seoul_daily = boundary.hydrate(
        CronHydrationRequest(
            requestId="cron-seoul",
            now=1,
            crons=(
                _cron(
                    cronId="cron:seoul-daily",
                    expression="0 9 * * *",
                    timezone="Asia/Seoul",
                    nextFireAt=0,
                ),
            ),
        ),
    )

    assert interval.updated_crons[0].next_fire_at == 1_800_000
    assert utc_daily.updated_crons[0].next_fire_at == 32_400_000
    assert seoul_daily.updated_crons[0].next_fire_at == 86_400_000


def test_cron_next_fire_respects_range_and_step_expressions() -> None:
    from magi_agent.harness.cron_runtime import (
        CronHydrationRequest,
        CronRuntimeBoundary,
        CronRuntimeConfig,
    )

    boundary = CronRuntimeBoundary(CronRuntimeConfig(enabled=True, localFakeCronEnabled=True))
    hourly_business_range = boundary.hydrate(
        CronHydrationRequest(
            requestId="cron-range",
            now=8 * 60 * 60 * 1000 + 1,
            crons=(
                _cron(
                    cronId="cron:range",
                    expression="0 9-17 * * *",
                    timezone="UTC",
                    nextFireAt=0,
                ),
            ),
        ),
    )
    stepped_minutes = boundary.hydrate(
        CronHydrationRequest(
            requestId="cron-step-range",
            now=9 * 60 * 60 * 1000 + 1,
            crons=(
                _cron(
                    cronId="cron:step-range",
                    expression="15-45/15 9 * * *",
                    timezone="UTC",
                    nextFireAt=0,
                ),
            ),
        ),
    )

    assert hourly_business_range.updated_crons[0].next_fire_at == 9 * 60 * 60 * 1000
    assert stepped_minutes.updated_crons[0].next_fire_at == (9 * 60 + 15) * 60 * 1000


def test_cron_pause_resume_cancel_are_metadata_only() -> None:
    from magi_agent.harness.cron_runtime import (
        CronMutationRequest,
        CronRuntimeBoundary,
        CronRuntimeConfig,
    )

    boundary = CronRuntimeBoundary(CronRuntimeConfig(enabled=True, localFakeCronEnabled=True))
    paused = boundary.mutate(CronMutationRequest(operation="pause", cron=_cron()))
    resumed = boundary.mutate(CronMutationRequest(operation="resume", cron=paused.cron))
    cancelled = boundary.mutate(CronMutationRequest(operation="cancel", cron=resumed.cron))

    assert paused.status == "mutated_local_fake"
    assert paused.cron is not None and paused.cron.paused is True
    assert resumed.cron is not None and resumed.cron.paused is False
    assert cancelled.cron is not None and cancelled.cron.cancelled is True
    assert cancelled.public_projection()["authorityFlags"]["backgroundSchedulerAttached"] is False


def test_cron_config_and_authority_flags_cannot_be_forged_with_model_copy() -> None:
    from magi_agent.harness.cron_runtime import (
        CronAuthorityFlags,
        CronRuntimeConfig,
    )

    config = CronRuntimeConfig().model_copy(
        update={
            "backgroundSchedulerAttached": True,
            "background_scheduler_attached": True,
            "productionWritesEnabled": True,
            "production_writes_enabled": True,
            "routeAttached": True,
            "route_attached": True,
        }
    )
    flags = CronAuthorityFlags().model_copy(
        update={
            "backgroundSchedulerAttached": True,
            "background_scheduler_attached": True,
            "productionWritesEnabled": True,
            "production_writes_enabled": True,
            "routeAttached": True,
            "route_attached": True,
        }
    )

    assert config.background_scheduler_attached is False
    assert config.production_writes_enabled is False
    assert config.route_attached is False
    assert set(flags.model_dump(by_alias=True).values()) == {False}


def test_cron_hydration_blocks_stale_lease_and_redacts_prompt() -> None:
    from magi_agent.harness.cron_runtime import (
        CronHydrationRequest,
        CronLease,
        CronRuntimeBoundary,
        CronRuntimeConfig,
    )

    decision = CronRuntimeBoundary(CronRuntimeConfig(enabled=True, localFakeCronEnabled=True)).hydrate(
        CronHydrationRequest(
            requestId="cron-3",
            now=2_001,
            lease=CronLease(leaseId="lease:abc", ownerDigest="owner:abc", acquiredAt=1_000, expiresAt=2_000),
            crons=(
                _cron(
                    promptPreview="ship /Users/kevin/private token ghp_cronSecret",
                ),
            ),
        ),
    )

    assert decision.status == "blocked"
    assert decision.reason_codes == ("cron_lease_stale",)
    rendered = str(decision.public_projection())
    assert "/Users/kevin" not in rendered
    assert "ghp_cronSecret" not in rendered


def test_cron_diagnostic_metadata_cannot_forge_authority_claims() -> None:
    from magi_agent.harness.cron_runtime import (
        CronHydrationRequest,
        CronRuntimeBoundary,
        CronRuntimeConfig,
    )

    decision = CronRuntimeBoundary(CronRuntimeConfig()).hydrate(
        CronHydrationRequest(
            requestId="cron-forge",
            now=1,
            metadata={
                "enabled": True,
                "backgroundSchedulerAttached": True,
                "productionWritesEnabled": True,
                "routeAttached": True,
                "authorityFlags": "fake",
                "safeNote": "public",
            },
        ),
    )
    projection = decision.public_projection()
    rendered = json.dumps(projection["diagnosticMetadata"], sort_keys=True)

    assert projection["authorityFlags"]["backgroundSchedulerAttached"] is False
    assert projection["authorityFlags"]["productionWritesEnabled"] is False
    assert projection["diagnosticMetadata"] == {"safeNote": "public"}
    assert "enabled" not in rendered
    assert "Attached" not in rendered
    assert "authority" not in rendered


def test_cron_runtime_boundary_has_no_live_imports() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

importlib.import_module("magi_agent.harness.cron_runtime")
forbidden_prefixes = (
    "google.adk",
    "google.genai",
    "magi_agent.adk_bridge",
    "magi_agent.transport",
    "magi_agent.routing",
    "magi_agent.deploy",
    "magi_agent.chat_proxy",
    "magi_agent.runtime_selector",
    "magi_agent.k8s",
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
