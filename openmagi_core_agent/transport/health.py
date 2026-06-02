from __future__ import annotations

import hashlib
import os

from openmagi_core_agent.composio.config import resolve_composio_config
from openmagi_core_agent.composio.health import composio_health_metadata
from openmagi_core_agent.runtime.openmagi_runtime import OpenMagiRuntime
from openmagi_core_agent.transport.chat import (
    build_gate2_sandbox_workspace_canary_config_from_env,
    gate5b_user_visible_chat_gate_active,
)
from openmagi_core_agent.evidence.observed_egress import (
    get_observed_egress_evidence_provider,
    observed_egress_diagnostics,
)
from openmagi_core_agent.ops import default_runtime_ops_health_metadata
from openmagi_core_agent.gates.gate2_readiness import gate2_readiness_health_metadata
from openmagi_core_agent.gates.gate3_readiness import gate3_readiness_health_metadata
from openmagi_core_agent.gates.gate4_readiness import gate4_readiness_health_metadata
from openmagi_core_agent.gates.gate5_readiness import gate5_readiness_health_metadata
from openmagi_core_agent.gates.gate7_readiness import gate7_readiness_health_metadata
from openmagi_core_agent.gates.gate8_readiness import gate8_readiness_health_metadata
from openmagi_core_agent.shadow.gate2_activation_loop_a import (
    Gate2SandboxRootReadiness,
    check_gate2_sandbox_root_readiness,
)
from openmagi_core_agent.runtime.readiness import (
    build_runtime_heartbeat_readiness_snapshot,
)


def health_payload(runtime: OpenMagiRuntime) -> dict[str, object]:
    return {
        "ok": True,
        "botId": runtime.config.bot_id,
        "runtime": "core-agent",
        "version": runtime.config.build.version,
        "buildSha": runtime.config.build.build_sha,
    }


def healthz_payload(runtime: OpenMagiRuntime) -> dict[str, object]:
    status = runtime.status()
    authority = runtime.config.authority
    context_continuity = runtime.config.context_continuity
    gate2_readiness = runtime.config.gate2_readiness
    gate3_readiness = runtime.config.gate3_readiness
    gate4_readiness = runtime.config.gate4_readiness
    gate5_readiness = runtime.config.gate5_readiness
    gate7_readiness = runtime.config.gate7_readiness
    gate8_readiness = runtime.config.gate8_readiness
    canary_route_active = gate5b_user_visible_chat_gate_active(runtime)
    user_visible_output_allowed = (
        authority.user_visible_output_allowed is True and canary_route_active
    )
    canary_routing_allowed = (
        authority.canary_routing_allowed is True and canary_route_active
    )
    observed_egress = observed_egress_diagnostics(
        get_observed_egress_evidence_provider(runtime)
    )
    gate2_sandbox_root_readiness = _gate2_sandbox_root_readiness(runtime)
    composio_config = resolve_composio_config(
        {
            **os.environ,
            "USER_ID": runtime.config.user_id,
            "BOT_ID": runtime.config.bot_id,
        }
    )
    return {
        **status,
        "userVisibleOutputAllowed": user_visible_output_allowed,
        "canaryRoutingAllowed": canary_routing_allowed,
        **observed_egress,
        "toolHostActive": False,
        "memoryProviderActive": False,
        "transcriptWritesAllowed": authority.transcript_write_allowed,
        "sseWritesAllowed": authority.sse_write_allowed,
        "channelWritesAllowed": authority.channel_write_allowed,
        "dbWritesAllowed": authority.db_write_allowed,
        "workspaceMutationAllowed": authority.workspace_mutation_allowed,
        "childExecutionAllowed": authority.child_execution_allowed,
        "missionRuntimeAllowed": authority.mission_runtime_allowed,
        "evidenceBlockModeAllowed": authority.evidence_block_mode_allowed,
        "contextContinuity": context_continuity.health_metadata,
        "gate2Readiness": gate2_readiness_health_metadata(
            gate2_readiness,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
            sandbox_root_readiness=gate2_sandbox_root_readiness,
        ),
        "gate3Readiness": gate3_readiness_health_metadata(
            gate3_readiness,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
        ),
        "gate4Readiness": gate4_readiness_health_metadata(
            gate4_readiness,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
        ),
        "gate5Readiness": gate5_readiness_health_metadata(
            gate5_readiness,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
        ),
        "gate7Readiness": gate7_readiness_health_metadata(
            gate7_readiness,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
        ),
        "gate8Readiness": gate8_readiness_health_metadata(
            gate8_readiness,
            context_continuity,
            bot_id=runtime.config.bot_id,
            user_id=runtime.config.user_id,
            observed_egress=observed_egress,
        ),
        "runtimeHeartbeatReadiness": build_runtime_heartbeat_readiness_snapshot().model_dump(
            by_alias=True,
            mode="json",
        ),
        "runtimeOperations": default_runtime_ops_health_metadata(),
        "composio": composio_health_metadata(composio_config),
        "profile": {
            "name": runtime.profile.name,
            "hardSafety": {
                "enabledByDefault": runtime.profile.hard_safety.enabled_by_default,
                "optOut": runtime.profile.hard_safety.opt_out,
                "gates": list(runtime.profile.hard_safety.gates),
            },
            "harnessPacks": [
                {
                    "name": pack.name,
                    "enabledByDefault": pack.enabled_by_default,
                    "optOut": pack.opt_out,
                    "hardSafety": pack.hard_safety,
                }
                for pack in runtime.profile.harness_packs
            ],
        },
    }


def _gate2_sandbox_root_readiness(
    runtime: OpenMagiRuntime,
) -> Gate2SandboxRootReadiness | None:
    route_config = getattr(runtime, "gate2_sandbox_workspace_canary_config", None)
    if route_config is None:
        route_config = build_gate2_sandbox_workspace_canary_config_from_env(
            os.environ,
            runtime.config,
        )
    if getattr(route_config, "kill_switch_enabled", True) is not False:
        return None
    readiness_config = runtime.config.gate2_readiness
    if not readiness_config.enabled or readiness_config.kill_switch_enabled:
        return None
    readiness_metadata = gate2_readiness_health_metadata(
        readiness_config,
        bot_id=runtime.config.bot_id,
        user_id=runtime.config.user_id,
    )
    if readiness_metadata.get("readinessReady") is not True:
        return None
    bot_digest = _sha256_text_digest(runtime.config.bot_id)
    owner_digest = _sha256_text_digest(runtime.config.user_id)
    if readiness_config.selected_bot_digest != bot_digest:
        return None
    if readiness_config.selected_owner_user_id_digest != owner_digest:
        return None
    if readiness_config.environment not in readiness_config.environment_allowlist:
        return None
    if not getattr(route_config, "enabled", False):
        return None
    if not getattr(route_config, "selected_mutation_provider_enabled", False):
        return None
    if getattr(route_config, "selected_bot_digest", "") != bot_digest:
        return None
    if getattr(route_config, "selected_owner_user_id_digest", "") != owner_digest:
        return None
    route_environment = getattr(route_config, "environment", "")
    if route_environment not in getattr(route_config, "environment_allowlist", ()):
        return None
    return check_gate2_sandbox_root_readiness(
        getattr(route_config, "sandbox_root", None)
    )


def _sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()
