from __future__ import annotations

import hashlib
import os
from pathlib import Path

from magi_agent.composio.config import resolve_composio_config
from magi_agent.composio.health import composio_health_metadata
from magi_agent.gates.gate5b_full_toolhost import (
    Gate5BFullToolBundle,
    Gate5BFullToolHostConfig,
    build_gate5b_full_toolhost_bundle,
)
from magi_agent.runtime.openmagi_runtime import OpenMagiRuntime
from magi_agent.runtime.child_runner_status import child_runner_availability_metadata
from magi_agent.transport.chat import (
    build_gate2_sandbox_workspace_canary_config_from_env,
    gate5b_user_visible_chat_gate_active,
)
from magi_agent.evidence.observed_egress import (
    get_observed_egress_evidence_provider,
    observed_egress_diagnostics,
)
from magi_agent.ops import (
    default_runtime_ops_health_metadata,
    scheduler_executor_health_projection,
)
from magi_agent.gates.gate2_readiness import gate2_readiness_health_metadata
from magi_agent.gates.gate3_readiness import gate3_readiness_health_metadata
from magi_agent.gates.gate4_readiness import gate4_readiness_health_metadata
from magi_agent.gates.gate5_readiness import gate5_readiness_health_metadata
from magi_agent.gates.gate7_readiness import gate7_readiness_health_metadata
from magi_agent.gates.gate8_readiness import gate8_readiness_health_metadata
from magi_agent.shadow.gate2_activation_loop_a import (
    Gate2SandboxRootReadiness,
    check_gate2_sandbox_root_readiness,
)
from magi_agent.runtime.readiness import (
    build_runtime_heartbeat_readiness_snapshot,
)


def health_payload(runtime: OpenMagiRuntime) -> dict[str, object]:
    return {
        "ok": True,
        "botId": runtime.config.bot_id,
        "runtime": runtime.config.runtime,
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
    gate5b_full_toolhost_bundle = (
        _healthz_gate5b_full_toolhost_bundle(runtime)
        if user_visible_output_allowed and canary_routing_allowed
        else None
    )
    child_runner_tool_names: list[str] = []
    if (
        gate5b_full_toolhost_bundle is not None
        and gate5b_full_toolhost_bundle.status == "ready"
    ):
        child_runner_tool_names = list(gate5b_full_toolhost_bundle.exposed_tool_names)
    composio_config = resolve_composio_config(
        {
            **os.environ,
            "USER_ID": runtime.config.user_id,
            "BOT_ID": runtime.config.bot_id,
        }
    )
    body: dict[str, object] = {
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
        "childRunner": _child_runner_health_metadata(
            legacy_child_execution_allowed=authority.child_execution_allowed,
            allowed_tool_names=child_runner_tool_names,
        ),
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
        "runtimeOperations": {
            **default_runtime_ops_health_metadata(),
            "schedulerExecutor": scheduler_executor_health_projection(),
        },
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
    if user_visible_output_allowed and canary_routing_allowed:
        body.update(
            _user_visible_canary_ready_envelope(
                runtime,
                bundle=gate5b_full_toolhost_bundle,
            )
        )
    return body


def _user_visible_canary_ready_envelope(
    runtime: OpenMagiRuntime,
    *,
    bundle: Gate5BFullToolBundle | None = None,
) -> dict[str, object]:
    if bundle is None:
        bundle = _healthz_gate5b_full_toolhost_bundle(runtime)
    envelope: dict[str, object] = {
        "status": "python_ready",
        "fallbackStatus": "none",
        "responseAuthority": "python",
        "authority": {
            "userVisibleOutputAllowed": True,
            "canaryRoutingAllowed": True,
            "memoryWriteAllowed": False,
            "toolDispatchAllowed": False,
            "transcriptWritesAllowed": False,
            "sseWritesAllowed": False,
            "channelWritesAllowed": False,
            "dbWritesAllowed": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "missionRuntimeAllowed": False,
            "evidenceBlockModeAllowed": False,
        },
        "safety": {
            "toolsActive": False,
            "memoryProviderActive": False,
            "browserActive": False,
            "workspaceMutationAllowed": False,
            "childExecutionAllowed": False,
            "missionRuntimeAllowed": False,
            "telegramDeliveryAllowed": False,
            "artifactChannelDeliveryAllowed": False,
            "evidenceBlockModeAllowed": False,
            "productionTranscriptWritesAllowed": False,
            "productionSseWritesAllowed": False,
            "productionDbWritesAllowed": False,
        },
    }
    if bundle is not None and bundle.status == "ready":
        allowed_tool_names = list(bundle.exposed_tool_names)
        envelope["authority"].update(
            {
                "toolDispatchAllowed": True,
                "selectedWorkspaceMutationAllowed": True,
                "productionWorkspaceMutationAllowed": False,
                "bashCommandAllowed": "Bash" in allowed_tool_names,
            }
        )
        envelope["safety"].update(
            {
                "toolsActive": True,
                "readOnlyToolsActive": False,
                "toolHostMode": "selected_full_toolhost",
                "allowedToolNames": allowed_tool_names,
                "selectedWorkspaceMutationAllowed": True,
                "productionWorkspaceMutationAllowed": False,
                "writeMutationAllowed": True,
                "bashCommandAllowed": "Bash" in allowed_tool_names,
            }
        )
        attachment_flags = bundle.attachment_flags.model_dump(
            by_alias=True,
            mode="json",
        )
        forbidden = sorted(
            name
            for name in allowed_tool_names
            if name not in set(bundle.host.config.allowed_tool_names)
        )
        envelope["tooling"] = {
            "schemaVersion": "gate5b.selectedFullToolhost.v1",
            "mode": "selected_full_toolhost",
            "toolsPolicy": "selected_full_toolhost",
            "allowedToolNames": allowed_tool_names,
            "childRunner": _child_runner_health_metadata(
                legacy_child_execution_allowed=False,
                allowed_tool_names=allowed_tool_names,
            ),
            "forbiddenToolsExposed": forbidden,
            "receiptCount": bundle.host.counter.receipt_count,
            "routeAttached": attachment_flags["routeAttached"],
            "productionAttached": attachment_flags["productionAttached"],
            "workspaceRootDigest": bundle.workspace_root_digest,
            "attachmentFlags": attachment_flags,
            "receiptLimits": {
                "maxToolCallsPerTurn": bundle.host.config.max_tool_calls_per_turn,
                "maxPerToolOutputBytes": bundle.host.config.max_per_tool_output_bytes,
                "commandTimeoutMs": bundle.host.config.command_timeout_ms,
            },
        }
    return envelope


def _child_runner_health_metadata(
    *,
    legacy_child_execution_allowed: bool,
    allowed_tool_names: list[str] | tuple[str, ...] = (),
) -> dict[str, object]:
    return child_runner_availability_metadata(
        legacy_child_execution_allowed=legacy_child_execution_allowed,
        allowed_tool_names=allowed_tool_names,
    )


def _healthz_gate5b_full_toolhost_bundle(
    runtime: OpenMagiRuntime,
) -> Gate5BFullToolBundle | None:
    config = getattr(runtime, "gate5b_full_toolhost_config", None)
    if not isinstance(config, Gate5BFullToolHostConfig):
        return None
    return build_gate5b_full_toolhost_bundle(
        config=config,
        scope={
            "selectedBotDigest": _sha256_text_digest(runtime.config.bot_id),
            "selectedOwnerDigest": _sha256_text_digest(runtime.config.user_id),
            "environment": getattr(
                getattr(runtime, "gate5b_user_visible_chat_route_config", None),
                "environment",
                "local",
            )
            or "local",
        },
        workspace_root=_gate5b_full_toolhost_workspace_root(),
        tool_registry=runtime.tool_registry,
    )


def _gate5b_full_toolhost_workspace_root() -> Path:
    configured = os.environ.get("CORE_AGENT_PYTHON_GATE5B_FULL_TOOLHOST_WORKSPACE_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd()


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
