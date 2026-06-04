"""Scheduler-executor rollout readiness gate — Track A, PR A5.

The bounded scheduler executor (``harness/scheduler_job_execution.py``) is the
OSS in-process scheduler runtime.  Its rollout MUST flow through the EXISTING
gate pipeline rather than a parallel framework.  This module mirrors the
readiness-gate pattern established by ``workflow_executor_readiness`` and
``learning_live_readiness``:

    * a frozen pydantic config whose single authority flag
      (``live_execution_allowed``) is locked to ``Literal[False]`` so a forged
      env value can never grant live authority — the real decision is computed by
      :func:`scheduler_executor_readiness_health_metadata` from the selected-bot
      scope and the promotion stage;
    * a pure ``*_health_metadata`` function returning
      ``enabled``/``status``/``executionMode``/``readinessReady``/``reasonCodes``;
    * a canary-ladder promotion (selected-bot digest + env allowlist + a
      per-gate promotion stage aligned with gate5 in ``api_canary_ladder``).

Rollout ladder (matches workflow_executor_readiness):

    disabled
        ▼   gate enabled + selected scope matched + shadow enabled
    shadow      ── dry-run evidence recorded, no agents dispatched
        ▼   promoted_gate >= _CANARY_LIVE_GATE AND canary_promotion_confirmed
    live        ── real turn dispatch via scheduler_job_execution

Env gate: ``MAGI_SCHEDULER_EXECUTOR_ENABLED`` (default OFF).  When the env gate
is OFF the resolved mode is ``disabled`` regardless of config — exactly like
``learning_live_readiness``'s env short-circuit.

oc-cron transition guard
------------------------
``check_oc_cron_transition_guard`` / ``check_oc_cron_transition_guard_from_env``
enforce that the OSS in-process scheduler and the legacy hosted ``oc-cron`` are
never BOTH active at the same time.  The end state is oc-cron → OSS replacement.
Default: both OFF (safe).

Forbidden imports: urllib, socket, subprocess, http, requests — none appear here.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


SchedulerExecutionMode = Literal["disabled", "shadow", "live"]

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: Env variable that enables the OSS scheduler executor (default OFF).
_EXECUTOR_ENV_VAR: str = "MAGI_SCHEDULER_EXECUTOR_ENABLED"
#: Env variable indicating the legacy oc-cron is active in this environment.
_OC_CRON_ENV_VAR: str = "MAGI_OC_CRON_ACTIVE"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: The gate at which live (canary) dispatch first becomes eligible.  Mirrors
#: workflow_executor_readiness: shadow runs through gate1-5, then canary at
#: gate5 (scheduler cron mission gate) flips live.
_CANARY_LIVE_GATE: int = 5


def _executor_env_enabled() -> bool:
    """Return True only when the env gate is explicitly set to a truthy value."""
    return os.environ.get(_EXECUTOR_ENV_VAR, "").lower() in _TRUE_STRINGS


class SchedulerExecutorReadinessConfig(BaseModel):
    """Frozen rollout config for the scheduler-executor gate.

    Authority is NOT taken from config — ``live_execution_allowed`` is locked to
    ``Literal[False]`` so a forged env flag cannot grant live execution.  The real
    decision is computed by :func:`scheduler_executor_readiness_health_metadata`
    from the selected-bot scope and the promotion stage (mirrors
    ``WorkflowExecutorReadinessConfig``).
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
    #: Operator-confirmed canary promotion.  Live dispatch requires BOTH
    #: ``promoted_gate >= _CANARY_LIVE_GATE`` AND this flag.
    canary_promotion_confirmed: bool = Field(
        default=False,
        alias="canaryPromotionConfirmed",
    )
    #: LOCKED authority — never grants live execution regardless of forged env.
    live_execution_allowed: Literal[False] = Field(
        default=False,
        alias="liveExecutionAllowed",
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

    @field_validator("live_execution_allowed", mode="before")
    @classmethod
    def _force_live_execution_false(cls, _value: object) -> bool:
        # Any forged truthy value is coerced to False — authority is gate-derived.
        return False

    @field_serializer("live_execution_allowed")
    def _serialize_live_execution_false(self, _value: object) -> bool:
        return False


def scheduler_executor_readiness_health_metadata(
    config: SchedulerExecutorReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return the scheduler-executor rollout readiness metadata.

    Follows the ``*_health_metadata`` shape from
    ``workflow_executor_readiness_health_metadata``.  ``executionMode`` is the
    resolved rollout stage (``disabled``/``shadow``/``live``); ``status``
    mirrors it for the healthz surface; ``liveExecutionAllowed`` is the single
    gate-derived authority flag (True only at/after the canary stage).

    The env gate ``MAGI_SCHEDULER_EXECUTOR_ENABLED`` is an additional hard
    short-circuit: when OFF the resolved mode is ALWAYS ``disabled`` regardless
    of config — keeping OFF byte-identical to pre-A3.
    """
    env_on = _executor_env_enabled()
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id, env_on=env_on)
    selected_scope_matched = _selected_scope_matched(
        config, bot_id=bot_id, user_id=user_id
    )

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: SchedulerExecutionMode = "live"
        status = "live"
    elif reason_codes == ("selected_shadow_ready",):
        execution_mode = "shadow"
        status = "shadow"
    elif reason_codes in {("gate_disabled",), ("env_gate_disabled",)}:
        execution_mode = "disabled"
        status = "disabled"
    else:
        # Any blocking reason (non-selected bot, malformed scope, kill switch,
        # bad environment, shadow disabled) fails closed to disabled.
        execution_mode = "disabled"
        status = "blocked"

    live_execution_allowed = execution_mode == "live"
    return {
        "enabled": config.enabled,
        "envGateEnabled": env_on,
        "status": status,
        "executionMode": execution_mode,
        "readinessReady": execution_mode in {"shadow", "live"},
        "selectedScopeMatched": selected_scope_matched,
        "promotedGate": config.promoted_gate,
        "canaryLiveGate": _CANARY_LIVE_GATE,
        "canaryPromotionConfirmed": bool(config.canary_promotion_confirmed),
        "liveExecutionAllowed": live_execution_allowed,
        # Telemetry counter contract (populated at runtime by the executor;
        # surfaced here so ops dashboards know which counters to expect).
        "counterRequirements": [
            "fired",
            "suppressed_silent",
            "skipped",
            "timed_out",
            "lease_rejected",
        ],
        "reasonCodes": list(reason_codes),
    }


def resolve_scheduler_execution_mode(
    config: SchedulerExecutorReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> SchedulerExecutionMode:
    """Convenience: resolve just the execution mode for the scheduler executor."""
    meta = scheduler_executor_readiness_health_metadata(
        config, bot_id=bot_id, user_id=user_id
    )
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"scheduler_executor_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected 'disabled', 'shadow', or 'live'"
        )
    return mode  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# oc-cron transition guard
# ---------------------------------------------------------------------------

def check_oc_cron_transition_guard(
    *,
    oss_scheduler_enabled: bool,
    oc_cron_active: bool,
) -> dict[str, object]:
    """Check that the OSS in-process scheduler and legacy oc-cron are not both active.

    The end state is oc-cron → OSS replacement.  During transition only ONE of the
    two should be active per environment; having both active causes double-fire.

    Returns a dict with:
      safe      — True when at most one is active.
      conflict  — True when both are active (unsafe double-fire risk).
      reason    — human-readable explanation.
      endState  — documents the intended final state (oc-cron fully replaced by OSS).
    """
    conflict = oss_scheduler_enabled and oc_cron_active
    if conflict:
        return {
            "safe": False,
            "conflict": True,
            "reason": (
                "Both OSS in-process scheduler (MAGI_SCHEDULER_EXECUTOR_ENABLED=1) and "
                "hosted oc-cron (MAGI_OC_CRON_ACTIVE=1) are active simultaneously — "
                "this causes double-fire of scheduled jobs. "
                "Disable oc-cron before enabling the OSS scheduler."
            ),
            "endState": "oc-cron fully replaced by OSS in-process scheduler",
        }
    return {
        "safe": True,
        "conflict": False,
        "reason": "At most one scheduler is active — no double-fire risk.",
        "endState": "oc-cron fully replaced by OSS in-process scheduler",
    }


def check_oc_cron_transition_guard_from_env() -> dict[str, object]:
    """Evaluate the oc-cron transition guard from environment variables.

    Reads:
      MAGI_SCHEDULER_EXECUTOR_ENABLED  — OSS in-process scheduler active (default OFF).
      MAGI_OC_CRON_ACTIVE              — legacy hosted oc-cron active (default OFF).
    """
    oss_enabled = os.environ.get(_EXECUTOR_ENV_VAR, "").lower() in _TRUE_STRINGS
    oc_cron = os.environ.get(_OC_CRON_ENV_VAR, "").lower() in _TRUE_STRINGS
    return check_oc_cron_transition_guard(
        oss_scheduler_enabled=oss_enabled,
        oc_cron_active=oc_cron,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _reason_codes(
    config: SchedulerExecutorReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
    env_on: bool,
) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    reasons: list[str] = []
    if not env_on:
        # Env gate is off — record it and continue collecting all other blocking
        # reasons so that callers can see the full picture (e.g. bot mismatch
        # AND env gate off together).  This matches test expectations that both
        # ``env_gate_disabled`` and ``bot_not_selected`` appear in reasonCodes.
        reasons.append("env_gate_disabled")
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
    config: SchedulerExecutorReadinessConfig,
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


def _sha256_text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_present(value: object) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


__all__ = [
    "SchedulerExecutionMode",
    "SchedulerExecutorReadinessConfig",
    "check_oc_cron_transition_guard",
    "check_oc_cron_transition_guard_from_env",
    "resolve_scheduler_execution_mode",
    "scheduler_executor_readiness_health_metadata",
]
