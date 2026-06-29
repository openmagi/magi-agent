from __future__ import annotations

import re

from magi_agent.config.models import PythonGate2ReadinessConfig
from magi_agent.ops.safety import reject_private_text
from magi_agent.shadow.gate2_recipe_profile_resolver import (
    resolve_gate2_recipe_profile,
)
from magi_agent.shadow.gate2_activation_loop_a import (
    Gate2SandboxRootReadiness,
)
from magi_agent.shadow.gate2_shadow_tool_policy import (
    GATE2_ALLOWED_SANDBOX_ACTIONS,
    GATE2_FORBIDDEN_ACTIONS,
)
from magi_agent.gates._readiness_common import (
    DIGEST_RE as _DIGEST_RE,
    digest_present as _digest_present,
    sha256_text_digest as _sha256_text_digest,
)


_SAFE_PUBLIC_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_UNSAFE_PUBLIC_TEXT_RE = re.compile(
    r"auth|cookie|credential|key|password|private|secret|session|token|"
    r"sk-[A-Za-z0-9._:-]{4,}",
    re.IGNORECASE,
)
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})


def gate2_readiness_health_metadata(
    config: PythonGate2ReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
    sandbox_root_readiness: Gate2SandboxRootReadiness | None = None,
) -> dict[str, object]:
    profile = resolve_gate2_recipe_profile(config.profile_ref)
    safe_profile_ref = (
        config.profile_ref
        if _public_safe_profile_ref(config.profile_ref)
        else "invalid_profile_ref"
    )
    reason_codes = _reason_codes_for_scope(
        config,
        expected_profile_digest=profile.profile_digest,
        profile_status=profile.status,
        profile_ref_public_safe=safe_profile_ref == config.profile_ref,
        bot_id=bot_id,
        user_id=user_id,
        sandbox_root_readiness=sandbox_root_readiness,
    )
    selected_scope_matched = _selected_scope_matched(
        config,
        bot_id=bot_id,
        user_id=user_id,
    )
    readiness_ready = reason_codes == ("selected_sandbox_readiness_ready",)
    status = "disabled" if reason_codes == ("gate_disabled",) else "blocked"
    if readiness_ready:
        status = "ready"
    return {
        "enabled": config.enabled,
        "status": status,
        "readinessReady": readiness_ready,
        "selectedScopeMatched": selected_scope_matched,
        "profileRef": safe_profile_ref,
        "profileDigestPresent": _digest_present(config.profile_digest),
        "policyMode": (
            "sandbox_fake_workspace"
            if config.local_sandbox_harness_enabled
            else "disabled"
        ),
        "allowedSandboxActions": list(GATE2_ALLOWED_SANDBOX_ACTIONS),
        "forbiddenActionCount": len(GATE2_FORBIDDEN_ACTIONS),
        "maxMutationAttemptsPerTurn": config.max_mutation_attempts_per_turn,
        "routeAttached": False,
        "productionWorkspaceMutationAllowed": False,
        "writeMutationAuthorityAllowed": False,
        "userVisibleOutputAllowed": False,
        "toolHostDispatchAllowed": False,
        "liveToolExecutionAllowed": False,
        "memoryWriteAllowed": False,
        "browserWebChannelAllowed": False,
        "schedulerMutationAllowed": False,
        "connectorCredentialUseAllowed": False,
        "networkEgressAllowed": False,
        "reasonCodes": list(reason_codes),
        "sandboxRootReady": (
            sandbox_root_readiness.ready
            if sandbox_root_readiness is not None
            else None
        ),
        "sandboxRootStatus": (
            sandbox_root_readiness.status
            if sandbox_root_readiness is not None
            else "not_checked"
        ),
        "sandboxRootDiagnostics": (
            sandbox_root_readiness.parent_create_diagnostics.model_dump(
                by_alias=True,
                mode="json",
            )
            if sandbox_root_readiness is not None
            and sandbox_root_readiness.parent_create_diagnostics is not None
            else None
        ),
    }


def _reason_codes_for_scope(
    config: PythonGate2ReadinessConfig,
    *,
    expected_profile_digest: str,
    profile_status: str,
    profile_ref_public_safe: bool,
    bot_id: str,
    user_id: str,
    sandbox_root_readiness: Gate2SandboxRootReadiness | None,
) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    reasons: list[str] = []
    if config.kill_switch_enabled:
        reasons.append("kill_switch_enabled")
    if not config.local_sandbox_harness_enabled:
        reasons.append("local_sandbox_harness_disabled")
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
    if not profile_ref_public_safe:
        reasons.append("profile_ref_malformed")
    if profile_status != "ready":
        reasons.append("profile_not_approved")
    if not _digest_present(config.profile_digest):
        reasons.append("profile_digest_missing_or_malformed")
    elif config.profile_digest != expected_profile_digest:
        reasons.append("profile_digest_mismatch")
    if sandbox_root_readiness is not None and not sandbox_root_readiness.ready:
        reasons.extend(sandbox_root_readiness.reason_codes)
    if not reasons:
        return ("selected_sandbox_readiness_ready",)
    return tuple(dict.fromkeys(reasons))


def _selected_scope_matched(
    config: PythonGate2ReadinessConfig,
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


def _public_safe_profile_ref(value: str) -> bool:
    if _SAFE_PUBLIC_REF_RE.fullmatch(value) is None:
        return False
    if _UNSAFE_PUBLIC_TEXT_RE.search(value) is not None:
        return False
    try:
        reject_private_text(value, field_name="gate2ProfileRef")
    except ValueError:
        return False
    return True
