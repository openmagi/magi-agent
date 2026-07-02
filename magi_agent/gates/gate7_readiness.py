from __future__ import annotations

import importlib

from magi_agent.config.models import PythonGate7ReadinessConfig
from magi_agent.gates._readiness_common import (
    DIGEST_RE as _DIGEST_RE,
    SAFE_ENVIRONMENTS,
    digest_present as _digest_present,
    selected_scope_matched,
    sha256_text_digest as _sha256_text_digest,
)


_SAFE_ENVIRONMENTS = SAFE_ENVIRONMENTS
_DEFAULT_REQUIRED_SURFACES = (
    "local_child_runner_boundary",
    "child_runtime_envelope",
    "workspace_adoption_preflight_contract",
    "workspace_adoption_boundary",
    "coding_subagent_recipe_boundary",
)
_SURFACE_MODULES = {
    "local_child_runner_boundary": "magi_agent.runtime.child_runner_boundary",
    "child_runtime_envelope": "magi_agent.evidence.child_runtime_envelope",
    "workspace_adoption_preflight_contract": (
        "magi_agent.shadow.workspace_adoption_preflight_contract"
    ),
    "workspace_adoption_boundary": "magi_agent.workspace.adoption_boundary",
    "coding_subagent_recipe_boundary": "magi_agent.recipes.coding_subagents",
}


def gate7_readiness_health_metadata(
    config: PythonGate7ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    required_surfaces = _configured_required_surfaces(config)
    configured_optional_surfaces = _configured_optional_surfaces(config)
    optional_surfaces = _optional_import_surfaces(
        config,
        configured_optional_surfaces=configured_optional_surfaces,
    )
    modules_ready, ready_surfaces, unknown_surfaces = _child_modules_ready(
        required_surfaces,
        optional_surfaces,
        configured_optional_surfaces=configured_optional_surfaces,
    )
    selected_scope_matched = _selected_scope_matched(
        config,
        bot_id=bot_id,
        user_id=user_id,
    )
    reason_codes = _reason_codes_for_scope(
        config,
        bot_id=bot_id,
        user_id=user_id,
        modules_ready=modules_ready,
        unknown_surfaces=unknown_surfaces,
    )
    readiness_ready = reason_codes == ("selected_local_child_replay_ready",)
    status = "disabled" if reason_codes == ("gate_disabled",) else "blocked"
    if readiness_ready:
        status = "ready"
    return {
        "enabled": config.enabled,
        "status": status,
        "readinessReady": readiness_ready,
        "selectedScopeMatched": selected_scope_matched,
        "policyMode": (
            "local_child_replay_evaluation"
            if config.local_replay_harness_enabled
            else "disabled"
        ),
        "localOnly": bool(config.local_replay_harness_enabled),
        "fakeOnly": bool(config.local_replay_harness_enabled),
        "maxLocalChildTasks": config.max_local_child_tasks,
        "maxEnvelopeBytes": config.max_envelope_bytes,
        "maxAdoptionPreflights": config.max_adoption_preflights,
        "childModulesReady": modules_ready,
        "readySurfaces": list(ready_surfaces),
        "routeAttached": False,
        "adkRunnerInvoked": False,
        "localFakeChildRunnerReady": readiness_ready,
        "childExecutionAllowed": False,
        "realChildRunnerExecuted": False,
        "workspaceAdoptionApplied": False,
        "workspaceMutationAllowed": False,
        "modelCallAllowed": False,
        "userVisibleOutputAllowed": False,
        "providerCredentialAllowed": False,
        "proxyEgressAllowed": False,
        "toolHostDispatchAllowed": False,
        "liveToolsExecuted": False,
        "memoryWriteAllowed": False,
        "browserWebNetworkAllowed": False,
        "channelDeliveryAllowed": False,
        "schedulerMutationAllowed": False,
        "dbWriteAllowed": False,
        "reasonCodes": list(reason_codes),
    }


def _reason_codes_for_scope(
    config: PythonGate7ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
    modules_ready: bool,
    unknown_surfaces: tuple[str, ...] = (),
) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    reasons: list[str] = []
    if config.kill_switch_enabled:
        reasons.append("kill_switch_enabled")
    if not config.local_replay_harness_enabled:
        reasons.append("local_replay_harness_disabled")
    if not _digest_present(config.selected_bot_digest) or not _digest_present(
        config.selected_owner_user_id_digest
    ):
        reasons.append("malformed_selected_scope")
    else:
        if config.selected_bot_digest != _sha256_text_digest(bot_id):
            reasons.append("bot_not_selected")
        if config.selected_owner_user_id_digest != _sha256_text_digest(user_id):
            reasons.append("owner_not_selected")
    if config.environment not in _SAFE_ENVIRONMENTS:
        reasons.append("invalid_environment")
    if config.environment not in config.environment_allowlist:
        reasons.append("environment_not_allowlisted")
    if config.max_local_child_tasks < 1:
        reasons.append("max_local_child_tasks_missing")
    if config.max_envelope_bytes < 1:
        reasons.append("max_envelope_bytes_missing")
    if config.max_adoption_preflights < 1:
        reasons.append("max_adoption_preflights_missing")
    if unknown_surfaces:
        reasons.append("unknown_ready_surface")
    if not modules_ready:
        reasons.append("child_modules_missing")
    if not reasons:
        return ("selected_local_child_replay_ready",)
    return tuple(dict.fromkeys(reasons))


_selected_scope_matched = selected_scope_matched


def _configured_required_surfaces(config: PythonGate7ReadinessConfig) -> tuple[str, ...]:
    if config.required_surface_refs:
        return tuple(
            dict.fromkeys((*_DEFAULT_REQUIRED_SURFACES, *config.required_surface_refs))
        )
    return _DEFAULT_REQUIRED_SURFACES


def _configured_optional_surfaces(config: PythonGate7ReadinessConfig) -> tuple[str, ...]:
    return tuple(dict.fromkeys(config.optional_surface_refs))


def _optional_import_surfaces(
    config: PythonGate7ReadinessConfig,
    *,
    configured_optional_surfaces: tuple[str, ...],
) -> tuple[str, ...]:
    if config.environment != "local":
        return ()
    return configured_optional_surfaces


def _child_modules_ready(
    required_surfaces: tuple[str, ...],
    optional_surfaces: tuple[str, ...],
    *,
    configured_optional_surfaces: tuple[str, ...] = (),
) -> tuple[bool, tuple[str, ...], tuple[str, ...]]:
    unknown = tuple(
        surface
        for surface in (*required_surfaces, *configured_optional_surfaces)
        if surface not in _SURFACE_MODULES
    )
    if unknown:
        return False, (), unknown
    ready: list[str] = []
    for surface in required_surfaces:
        module_name = _SURFACE_MODULES[surface]
        try:
            importlib.import_module(module_name)
        except Exception:
            return False, tuple(ready), ()
        ready.append(surface)
    for surface in optional_surfaces:
        module_name = _SURFACE_MODULES[surface]
        try:
            importlib.import_module(module_name)
        except Exception:
            continue
        ready.append(surface)
    return True, tuple(dict.fromkeys(ready)), ()


__all__ = ["gate7_readiness_health_metadata"]
