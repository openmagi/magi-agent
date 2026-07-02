from __future__ import annotations

import importlib

from magi_agent.config.models import PythonGate4ReadinessConfig
from magi_agent.gates._readiness_common import (
    DIGEST_RE as _DIGEST_RE,
    SAFE_ENVIRONMENTS,
    digest_present as _digest_present,
    selected_scope_matched,
    sha256_text_digest as _sha256_text_digest,
)


_SAFE_ENVIRONMENTS = SAFE_ENVIRONMENTS
_READY_SURFACES = (
    "gate4_local_shadow_consumer",
    "gate4c0_shadow_config",
    "gate4c1_dry_run_boundary",
    "gate4c1_runner_invoker_contract",
    "gate4c2_shadow_comparison_report",
    "gate4d_local_shadow_diagnostics",
)
_SURFACE_MODULES = {
    "gate4_local_shadow_consumer": "magi_agent.shadow.gate4_consumer",
    "gate4c0_shadow_config": "magi_agent.shadow.gate4c0_shadow_config",
    "gate4c1_dry_run_boundary": "magi_agent.shadow.gate4c1_dry_run_boundary",
    "gate4c1_runner_invoker_contract": (
        "magi_agent.shadow.gate4c1_runner_shadow_invoker"
    ),
    "gate4c2_shadow_comparison_report": (
        "magi_agent.shadow.gate4c2_shadow_comparison_report"
    ),
    "gate4d_local_shadow_diagnostics": (
        "magi_agent.shadow.gate4d_local_shadow_diagnostics"
    ),
}


def gate4_readiness_health_metadata(
    config: PythonGate4ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    modules_ready = _shadow_modules_ready()
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
    )
    readiness_ready = reason_codes == ("selected_local_shadow_ready",)
    status = "disabled" if reason_codes == ("gate_disabled",) else "blocked"
    if readiness_ready:
        status = "ready"
    return {
        "enabled": config.enabled,
        "status": status,
        "readinessReady": readiness_ready,
        "selectedScopeMatched": selected_scope_matched,
        "policyMode": (
            "local_shadow_adk_attachment"
            if config.local_shadow_harness_enabled
            else "disabled"
        ),
        "localOnly": bool(config.local_shadow_harness_enabled),
        "maxLocalBundles": config.max_local_bundles,
        "shadowModulesReady": modules_ready,
        "readySurfaces": list(_READY_SURFACES if modules_ready else ()),
        "routeAttached": False,
        "adkRunnerInvoked": False,
        "liveRunnerAttached": False,
        "modelCallAllowed": False,
        "userVisibleOutputAllowed": False,
        "toolHostDispatchAllowed": False,
        "liveToolsExecuted": False,
        "workspaceMutationAllowed": False,
        "memoryWriteAllowed": False,
        "browserWebNetworkAllowed": False,
        "channelDeliveryAllowed": False,
        "schedulerMutationAllowed": False,
        "dbWriteAllowed": False,
        "reasonCodes": list(reason_codes),
    }


def _reason_codes_for_scope(
    config: PythonGate4ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
    modules_ready: bool,
) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    reasons: list[str] = []
    if config.kill_switch_enabled:
        reasons.append("kill_switch_enabled")
    if not config.local_shadow_harness_enabled:
        reasons.append("local_shadow_harness_disabled")
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
    if config.max_local_bundles < 1:
        reasons.append("max_local_bundles_missing")
    if not modules_ready:
        reasons.append("shadow_modules_missing")
    if not reasons:
        return ("selected_local_shadow_ready",)
    return tuple(dict.fromkeys(reasons))


_selected_scope_matched = selected_scope_matched


def _shadow_modules_ready() -> bool:
    for module_name in _SURFACE_MODULES.values():
        try:
            importlib.import_module(module_name)
        except Exception:
            return False
    return True


__all__ = ["gate4_readiness_health_metadata"]
