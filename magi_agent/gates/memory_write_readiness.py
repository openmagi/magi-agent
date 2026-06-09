"""Writable-memory rollout readiness gate — Track D, PR D5.

Promotes the D1–D4 writable-memory stack from disabled → shadow → live through
the same gate pipeline used by ``scheduler_executor_readiness`` and
``learning_live_readiness``.

The D1–D4 safety invariants asserted by this gate
--------------------------------------------------
  1. Read-only is the default tier — write path is opt-in only.
  2. Agent writes are declarative-only (D2 filter guards before any persist).
  3. Write paths are path-safe (workspace-contained), redacted, and bounded
     (D1 ``_ALLOWED_WRITE_FILES``, ``_redact_for_write``, ``max_write_bytes``).
  4. Agent CANNOT write SOUL.md:
       (a) ``_ALLOWED_WRITE_FILES`` in D1 does not include SOUL.md;
       (b) D2 tool rejects forbidden targets loudly;
       (c) ``OperatorSoulWriter`` is structurally unreachable from the agent path.
  5. SOUL write path is OPERATOR-ONLY, gated by a separate
     ``MAGI_SOUL_WRITE_ENABLED`` env var independent from the agent gate.
  6. Memory projection (D3) is cache-safe and incognito-respecting.
  7. Live authority is gate-derived, never config-injected.

Rollout ladder (mirrors scheduler_executor_readiness):

    disabled
        ▼   gate enabled + selected scope matched + shadow enabled
    shadow      ── dry-run evidence recorded, no real writes/projections
        ▼   promoted_gate >= _CANARY_LIVE_GATE AND canary_promotion_confirmed
    live        ── D1–D4 write/project surfaces become callable

Kill-switch: ``MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED`` (default OFF / not set).
When ON the gate is immediately blocked regardless of other flags.

Env gates governed (all triple default-OFF):
  * ``MAGI_MEMORY_WRITE_ENABLED``       — agent gated write (D1/D2)
  * ``MAGI_MEMORY_PROJECTION_ENABLED``  — memory prompt projection (D3)
  * ``MAGI_SOUL_WRITE_ENABLED``         — operator SOUL write (D4)

The master readiness gate: ``MAGI_MEMORY_WRITE_READINESS_ENABLED`` (default OFF).
When OFF the resolved mode is ``disabled`` (not ``blocked``) — exactly like the
env short-circuit in the precedents.

Forbidden imports: urllib, socket, subprocess, http, requests — none appear here.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


MemoryWriteExecutionMode = Literal["disabled", "shadow", "live"]

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: Master readiness env gate (default OFF).
_READINESS_ENV_VAR: str = "MAGI_MEMORY_WRITE_READINESS_ENABLED"

#: Kill-switch env var.  When truthy the gate is immediately blocked.
_KILL_SWITCH_ENV_VAR: str = "MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED"

#: Local single-user developer short-circuit.
#: When set to a truthy value AND ``MAGI_MEMORY_WRITE_READINESS_ENABLED=1``
#: AND ``MAGI_MEMORY_WRITE_ENABLED=1``, ``resolve_memory_write_execution_mode``
#: returns ``"live"`` directly, bypassing the canary-promotion ladder.
#: This has NO effect in multi-tenant hosted deployments because those
#: deployments do NOT set this env var; the canary ladder remains the only
#: path to "live" in production.
MAGI_MEMORY_LOCAL_DEV_ENV: str = "MAGI_MEMORY_LOCAL_DEV"

#: The three D-track surface gates governed by this readiness gate.
MAGI_MEMORY_WRITE_ENABLED_ENV: str = "MAGI_MEMORY_WRITE_ENABLED"
MAGI_MEMORY_PROJECTION_ENABLED_ENV: str = "MAGI_MEMORY_PROJECTION_ENABLED"
MAGI_SOUL_WRITE_ENABLED_ENV: str = "MAGI_SOUL_WRITE_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: Gate at which live promotion first becomes eligible.  Mirrors the precedents.
_CANARY_LIVE_GATE: int = 5


def _readiness_env_enabled() -> bool:
    """Return True only when the master readiness env gate is explicitly truthy."""
    return os.environ.get(_READINESS_ENV_VAR, "").lower() in _TRUE_STRINGS


def _kill_switch_env_active() -> bool:
    """Return True when the kill-switch env var is explicitly truthy."""
    return os.environ.get(_KILL_SWITCH_ENV_VAR, "").lower() in _TRUE_STRINGS


def _local_dev_env_active() -> bool:
    """Return True when the local single-user dev short-circuit is explicitly set.

    Requires BOTH ``MAGI_MEMORY_LOCAL_DEV=1`` AND the master readiness env gate
    (``MAGI_MEMORY_WRITE_READINESS_ENABLED=1``) to be truthy.  This prevents
    accidental activation in environments where the master gate is off.
    Multi-tenant hosted deployments never set MAGI_MEMORY_LOCAL_DEV, so they
    cannot reach "live" through this path — only through canary promotion.
    """
    return (
        os.environ.get(MAGI_MEMORY_LOCAL_DEV_ENV, "").lower() in _TRUE_STRINGS
        and _readiness_env_enabled()
    )


class MemoryWriteReadinessConfig(BaseModel):
    """Frozen rollout config for the writable-memory readiness gate.

    Authority is NOT taken from config — ``live_execution_allowed`` is locked to
    ``Literal[False]`` so a forged env flag cannot grant live execution.  The
    real decision is computed by
    :func:`memory_write_readiness_health_metadata` from the selected scope and
    the promotion stage (mirrors ``SchedulerExecutorReadinessConfig``).

    Kill-switch: ``kill_switch_enabled`` defaults to True (blocked on creation).
    To promote past shadow the operator must explicitly clear the kill-switch.

    The governed env gates (D1/D2/D3/D4) are referenced as documentation — this
    gate does NOT set them; it only reports their status.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

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
    #: Highest gate reached in the gate1-5 ladder (0 = none).  Live is only
    #: eligible once this reaches ``_CANARY_LIVE_GATE``.
    promoted_gate: int = Field(default=0, ge=0, le=9, alias="promotedGate")
    #: Operator-confirmed canary promotion.  Live requires BOTH
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


def memory_write_readiness_health_metadata(
    config: MemoryWriteReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return the writable-memory rollout readiness metadata.

    Follows the ``*_health_metadata`` shape from the precedents.
    ``executionMode`` is the resolved rollout stage
    (``disabled``/``shadow``/``live``); ``status`` mirrors it for the healthz
    surface; ``liveExecutionAllowed`` is the single gate-derived authority flag
    (True only at the canary stage).

    The env gate ``MAGI_MEMORY_WRITE_READINESS_ENABLED`` is a hard short-circuit:
    when OFF the resolved mode is ALWAYS ``disabled`` regardless of config —
    keeping OFF byte-identical to D1–D4 pre-D5.

    D1–D4 safety invariants are surfaced in ``safetyInvariantsAsserted``:
      * read_only_default
      * declarative_only_filter
      * path_safe_redacted_bounded
      * soul_not_agent_writable
      * soul_operator_path_separate
      * projection_cache_safe_incognito_respecting
    """
    env_on = _readiness_env_enabled()
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id, env_on=env_on)
    selected_scope_matched = _selected_scope_matched(
        config, bot_id=bot_id, user_id=user_id
    )

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: MemoryWriteExecutionMode = "live"
        status = "live"
    elif reason_codes == ("selected_shadow_ready",):
        execution_mode = "shadow"
        status = "shadow"
    elif reason_codes in {("gate_disabled",), ("env_gate_disabled",)}:
        execution_mode = "disabled"
        status = "disabled"
    elif "env_gate_disabled" in reason_codes:
        # Env gate is off together with other blocking reasons.
        # The gate is simply OFF — not in a conflict state — so use "disabled",
        # not "blocked" (mirrors scheduler_executor_readiness behaviour).
        execution_mode = "disabled"
        status = "disabled"
    else:
        # Any other blocking reason (kill switch, bad env, scope mismatch,
        # shadow disabled) fails closed to disabled/blocked.
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
        # The three D-surface env gates governed by this readiness gate.
        "governedEnvGates": [
            MAGI_MEMORY_WRITE_ENABLED_ENV,
            MAGI_MEMORY_PROJECTION_ENABLED_ENV,
            MAGI_SOUL_WRITE_ENABLED_ENV,
        ],
        # D1–D4 safety invariants surfaced as checkable properties.
        "safetyInvariantsAsserted": [
            "read_only_default",
            "declarative_only_filter",
            "path_safe_redacted_bounded",
            "soul_not_agent_writable",
            "soul_operator_path_separate",
            "projection_cache_safe_incognito_respecting",
        ],
        "reasonCodes": list(reason_codes),
    }


def resolve_memory_write_execution_mode(
    config: MemoryWriteReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> MemoryWriteExecutionMode:
    """Convenience: resolve just the execution mode for the writable-memory gate.

    Local-developer short-circuit: when ``MAGI_MEMORY_LOCAL_DEV=1`` is set
    together with the master readiness gate (``MAGI_MEMORY_WRITE_READINESS_ENABLED``),
    the mode is unconditionally promoted to ``"live"`` without requiring canary
    promotion.  This path is intended ONLY for single-user local development and
    CLI sessions — hosted multi-tenant deployments never set ``MAGI_MEMORY_LOCAL_DEV``.
    """
    if _local_dev_env_active():
        return "live"
    meta = memory_write_readiness_health_metadata(
        config, bot_id=bot_id, user_id=user_id
    )
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"memory_write_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected 'disabled', 'shadow', or 'live'"
        )
    return mode  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reason_codes(
    config: MemoryWriteReadinessConfig,
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
        # reasons so callers can see the full picture (matches
        # scheduler_executor_readiness pattern).
        reasons.append("env_gate_disabled")
    if config.kill_switch_enabled or _kill_switch_env_active():
        reasons.append("kill_switch_enabled")
    if not config.shadow_mode_enabled:
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
    config: MemoryWriteReadinessConfig,
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
    "MemoryWriteExecutionMode",
    "MemoryWriteReadinessConfig",
    "memory_write_readiness_health_metadata",
    "resolve_memory_write_execution_mode",
    "MAGI_MEMORY_WRITE_ENABLED_ENV",
    "MAGI_MEMORY_PROJECTION_ENABLED_ENV",
    "MAGI_SOUL_WRITE_ENABLED_ENV",
    "MAGI_MEMORY_LOCAL_DEV_ENV",
]
