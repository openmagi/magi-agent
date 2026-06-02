from __future__ import annotations

import hashlib
import importlib
import re

from openmagi_core_agent.config.models import PythonGate5ReadinessConfig


_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})
_READY_SURFACES = (
    "gate5a_no_memory_shadow_canary",
    "gate5b4_internal_endpoint_contract",
    "gate5b4c2_shadow_invocation_contract",
    "gate5b4c3_shadow_generation_contract",
    "gate5b4c3_shadow_generation_report",
    "gate5b4d_stream_fixture_audit",
    "gate5b_user_visible_routing_canary_contract",
)
_SURFACE_MODULES = {
    "gate5a_no_memory_shadow_canary": (
        "openmagi_core_agent.shadow.gate5a_no_memory_shadow_canary"
    ),
    "gate5b4_internal_endpoint_contract": (
        "openmagi_core_agent.shadow.gate5b4_internal_endpoint_contract"
    ),
    "gate5b4c2_shadow_invocation_contract": (
        "openmagi_core_agent.shadow.gate5b4c2_shadow_invocation_contract"
    ),
    "gate5b4c3_shadow_generation_contract": (
        "openmagi_core_agent.shadow.gate5b4c3_shadow_generation_contract"
    ),
    "gate5b4c3_shadow_generation_report": (
        "openmagi_core_agent.shadow.gate5b4c3_shadow_generation_report"
    ),
    "gate5b4d_stream_fixture_audit": (
        "openmagi_core_agent.shadow.gate5b4d_stream_fixture_audit"
    ),
    "gate5b_user_visible_routing_canary_contract": (
        "openmagi_core_agent.shadow.gate5b_user_visible_routing_canary"
    ),
}


def gate5_readiness_health_metadata(
    config: PythonGate5ReadinessConfig,
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
    readiness_ready = reason_codes == ("selected_non_user_visible_shadow_ready",)
    status = "disabled" if reason_codes == ("gate_disabled",) else "blocked"
    if readiness_ready:
        status = "ready"
    return {
        "enabled": config.enabled,
        "status": status,
        "readinessReady": readiness_ready,
        "selectedScopeMatched": selected_scope_matched,
        "policyMode": (
            "non_user_visible_shadow_diagnostic"
            if config.non_user_visible_harness_enabled
            else "disabled"
        ),
        "localOnly": bool(config.non_user_visible_harness_enabled),
        "maxShadowChecks": config.max_shadow_checks,
        "shadowModulesReady": modules_ready,
        "readySurfaces": list(_READY_SURFACES if modules_ready else ()),
        "routeAttached": False,
        "shadowEndpointEnabled": False,
        "adkRunnerInvoked": False,
        "liveRunnerAttached": False,
        "modelCallAllowed": False,
        "userVisibleOutputAllowed": False,
        "providerCredentialAllowed": False,
        "proxyEgressAllowed": False,
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
    config: PythonGate5ReadinessConfig,
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
    if not config.non_user_visible_harness_enabled:
        reasons.append("non_user_visible_harness_disabled")
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
    if config.max_shadow_checks < 1:
        reasons.append("max_shadow_checks_missing")
    if not modules_ready:
        reasons.append("shadow_modules_missing")
    if not reasons:
        return ("selected_non_user_visible_shadow_ready",)
    return tuple(dict.fromkeys(reasons))


def _selected_scope_matched(
    config: PythonGate5ReadinessConfig,
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


def _shadow_modules_ready() -> bool:
    for module_name in _SURFACE_MODULES.values():
        try:
            importlib.import_module(module_name)
        except Exception:
            return False
    return True


def _sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_present(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


__all__ = ["gate5_readiness_health_metadata"]
