from __future__ import annotations

import importlib

from magi_agent.config.models import PythonGate3ReadinessConfig
from magi_agent.gates._readiness_common import (
    DIGEST_RE as _DIGEST_RE,
    digest_present as _digest_present,
    sha256_text_digest as _sha256_text_digest,
)


_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
_READY_SURFACES = (
    "gate3a_recorded_replay",
    "gate3a_comparison_report",
    "gate3b_local_consumer",
    "gate3b_local_report",
    "gate3b_metrics",
)
_SURFACE_MODULES = {
    "gate3a_recorded_replay": "magi_agent.shadow.gate3a_replay",
    "gate3a_comparison_report": "magi_agent.shadow.gate3a_report",
    "gate3b_local_consumer": "magi_agent.shadow.gate3b_local_consumer",
    "gate3b_local_report": "magi_agent.shadow.gate3b_local_report",
    "gate3b_metrics": "magi_agent.shadow.gate3b_metrics",
}


def gate3_readiness_health_metadata(
    config: PythonGate3ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    modules_ready = _replay_modules_ready()
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
    readiness_ready = reason_codes == ("selected_local_replay_ready",)
    status = "disabled" if reason_codes == ("gate_disabled",) else "blocked"
    if readiness_ready:
        status = "ready"
    return {
        "enabled": config.enabled,
        "status": status,
        "readinessReady": readiness_ready,
        "selectedScopeMatched": selected_scope_matched,
        "policyMode": (
            "recorded_replay_comparison"
            if config.local_replay_harness_enabled
            else "disabled"
        ),
        "localOnly": bool(config.local_replay_harness_enabled),
        "maxReplayBundles": config.max_replay_bundles,
        "replayModulesReady": modules_ready,
        "readySurfaces": list(_READY_SURFACES if modules_ready else ()),
        "routeAttached": False,
        "liveCaptureAllowed": False,
        "modelCallAllowed": False,
        "userVisibleOutputAllowed": False,
        "toolHostDispatchAllowed": False,
        "workspaceMutationAllowed": False,
        "memoryWriteAllowed": False,
        "browserWebNetworkAllowed": False,
        "channelDeliveryAllowed": False,
        "schedulerMutationAllowed": False,
        "dbWriteAllowed": False,
        "reasonCodes": list(reason_codes),
    }


def _reason_codes_for_scope(
    config: PythonGate3ReadinessConfig,
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
    if config.max_replay_bundles < 1:
        reasons.append("max_replay_bundles_missing")
    if not modules_ready:
        reasons.append("replay_modules_missing")
    if not reasons:
        return ("selected_local_replay_ready",)
    return tuple(dict.fromkeys(reasons))


def _selected_scope_matched(
    config: PythonGate3ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> bool:
    if not config.enabled:
        return False
    if not _digest_present(config.selected_bot_digest) or not _digest_present(
        config.selected_owner_user_id_digest
    ):
        return False
    if config.selected_bot_digest != _sha256_text_digest(bot_id):
        return False
    if config.selected_owner_user_id_digest != _sha256_text_digest(user_id):
        return False
    if config.environment not in _SAFE_ENVIRONMENTS:
        return False
    return config.environment in config.environment_allowlist


def _replay_modules_ready() -> bool:
    for module_name in _SURFACE_MODULES.values():
        try:
            importlib.import_module(module_name)
        except Exception:
            return False
    return True


__all__ = ["gate3_readiness_health_metadata"]
