"""Workflow-executor rollout readiness gate — Track 17 PR6.

The bounded workflow-executor (``harness/workflow_executor.py``) is the FIRST
real child-execution path in the runtime, so its rollout MUST flow through the
EXISTING gate pipeline rather than a parallel framework.  This module mirrors
the readiness-gate pattern established by ``gate7_readiness`` (a frozen pydantic
config whose authority flags are ``Literal[False]`` regardless of forged env,
plus a pure ``*_health_metadata`` function returning ``enabled``/``status``/
``readinessReady``/``reasonCodes``) and the canary-ladder promotion expressed in
``api_canary_ladder`` (selected-bot ``186bf3d7`` digest + env allowlist + a
per-gate promotion stage).

Rollout ladder (matches the PR6 mission):

    disabled
        ▼   gate enabled + selected canary scope matched (but pre-canary stage)
    shadow      ── validate + dry-run on REAL traffic, ZERO live child dispatch
        ▼   promotedGate >= canary stage AND canaryPromotionConfirmed
    live        ── bounded per-child fan-out actually dispatches

The single authority flag — ``liveDispatchAllowed`` — is derived ENTIRELY from
the gate decision; the config's ``live_dispatch_allowed`` field is locked to
``Literal[False]`` so a forged env value can never grant live authority (the
same defence gate7 uses for ``child_execution_allowed`` et al.).  Default-OFF is
preserved: an unconfigured gate resolves to ``disabled`` (no dispatch).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator
from magi_agent.gates._readiness_common import (
    DIGEST_RE as _DIGEST_RE,
    digest_present as _digest_present,
    sha256_text_digest as _sha256_text_digest,
)


WorkflowExecutionMode = Literal["disabled", "shadow", "live"]

_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: The gate at which live (canary) dispatch first becomes eligible.  Mirrors the
#: PR6 ladder: shadow runs through gate1-5, then canary 186bf3d7 promotion flips
#: live.  Below this gate the executor stays shadow/dry-run only.
_CANARY_LIVE_GATE: int = 5


class WorkflowExecutorReadinessConfig(BaseModel):
    """Frozen rollout config for the workflow-executor gate.

    Authority is NOT taken from config — ``live_dispatch_allowed`` is locked to
    ``Literal[False]`` so a forged env flag cannot grant live dispatch.  The real
    decision is computed by :func:`workflow_executor_readiness_health_metadata`
    from the selected-bot scope and the promotion stage.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    shadow_mode_enabled: bool = Field(default=False, alias="shadowModeEnabled")
    selected_bot_digest: str = Field(default="", alias="selectedBotDigest")
    selected_owner_user_id_digest: str = Field(
        default="",
        alias="selectedOwnerUserIdDigest",
    )
    environment: str = "local"
    environment_allowlist: tuple[str, ...] = Field(
        default=(),
        alias="environmentAllowlist",
    )
    #: Highest gate reached in the gate1-5 ladder (0 = none).  Live dispatch is
    #: only eligible once this reaches ``_CANARY_LIVE_GATE``.
    promoted_gate: int = Field(default=0, ge=0, le=9, alias="promotedGate")
    #: Operator-confirmed canary promotion (186bf3d7 → fleet).  Live dispatch
    #: requires BOTH ``promoted_gate >= _CANARY_LIVE_GATE`` AND this flag.
    canary_promotion_confirmed: bool = Field(
        default=False,
        alias="canaryPromotionConfirmed",
    )
    #: LOCKED authority — never grants live dispatch regardless of forged env.
    live_dispatch_allowed: Literal[False] = Field(
        default=False,
        alias="liveDispatchAllowed",
    )

    @field_validator("environment_allowlist", mode="before")
    @classmethod
    def _coerce_environment_allowlist(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        if isinstance(value, tuple | list):
            return tuple(str(item).strip() for item in value if str(item).strip())
        return ()

    @field_validator("live_dispatch_allowed", mode="before")
    @classmethod
    def _force_live_dispatch_false(cls, _value: object) -> bool:
        # Any forged truthy value is coerced to False — authority is gate-derived.
        return False

    @field_serializer("live_dispatch_allowed")
    def _serialize_live_dispatch_false(self, _value: object) -> bool:
        return False


def workflow_executor_readiness_health_metadata(
    config: WorkflowExecutorReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return the workflow-executor rollout readiness metadata.

    Follows the gate7 ``*_health_metadata`` shape.  ``executionMode`` is the
    resolved rollout stage (``disabled``/``shadow``/``live``); ``status`` mirrors
    it for the healthz surface; ``liveDispatchAllowed`` is the single
    gate-derived authority flag (True only at/after the canary stage).
    """
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id)
    selected_scope_matched = _selected_scope_matched(
        config, bot_id=bot_id, user_id=user_id
    )

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: WorkflowExecutionMode = "live"
        status = "live"
    elif reason_codes == ("selected_shadow_ready",):
        execution_mode = "shadow"
        status = "shadow"
    elif reason_codes == ("gate_disabled",):
        execution_mode = "disabled"
        status = "disabled"
    else:
        # Any blocking reason (non-selected bot, malformed scope, kill switch,
        # bad environment, shadow disabled) fails closed to disabled.
        execution_mode = "disabled"
        status = "blocked"

    live_dispatch_allowed = execution_mode == "live"
    return {
        "enabled": config.enabled,
        "status": status,
        "executionMode": execution_mode,
        "readinessReady": execution_mode in {"shadow", "live"},
        "selectedScopeMatched": selected_scope_matched,
        "promotedGate": config.promoted_gate,
        "canaryLiveGate": _CANARY_LIVE_GATE,
        "canaryPromotionConfirmed": bool(config.canary_promotion_confirmed),
        "liveDispatchAllowed": live_dispatch_allowed,
        # Telemetry counter contract (populated at runtime by the executor;
        # surfaced here so ops dashboards know which counters to expect).
        "counterRequirements": [
            "runs",
            "agentsSpawned",
            "concurrencyHighWater",
            "filteredClaims",
        ],
        "reasonCodes": list(reason_codes),
    }


def resolve_workflow_execution_mode(
    config: WorkflowExecutorReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> WorkflowExecutionMode:
    """Convenience: resolve just the execution mode for the executor."""
    meta = workflow_executor_readiness_health_metadata(
        config, bot_id=bot_id, user_id=user_id
    )
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"workflow_executor_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected one of 'disabled', 'shadow', 'live'"
        )
    return mode  # type: ignore[return-value]


def _reason_codes(
    config: WorkflowExecutorReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    reasons: list[str] = []
    if config.kill_switch_enabled:
        reasons.append("kill_switch_enabled")
    if not config.shadow_mode_enabled:
        # Shadow is the safest first stage and a prerequisite for any live
        # promotion — without it the gate cannot run on real traffic at all.
        reasons.append("shadow_mode_disabled")
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
    if reasons:
        return tuple(dict.fromkeys(reasons))

    # Scope + shadow are satisfied → at minimum shadow is ready.  Live requires
    # canary-stage promotion (gate >= _CANARY_LIVE_GATE AND operator confirmed).
    if config.promoted_gate >= _CANARY_LIVE_GATE and config.canary_promotion_confirmed:
        return ("selected_canary_live_ready",)
    return ("selected_shadow_ready",)


def _selected_scope_matched(
    config: WorkflowExecutorReadinessConfig,
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


__all__ = [
    "WorkflowExecutionMode",
    "WorkflowExecutorReadinessConfig",
    "resolve_workflow_execution_mode",
    "workflow_executor_readiness_health_metadata",
]
