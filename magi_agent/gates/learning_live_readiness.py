"""Learning-layer LIVE adapter readiness gate — PR7.

This is the SEPARATE, explicit gate that promotes the Learning Layer's
local-fake stand-ins (PR1–PR6) to REAL adapters.  It mirrors
``gates/workflow_executor_readiness.py`` exactly:

    * a frozen pydantic config whose single authority flag
      (``live_authority_allowed``) is locked to ``Literal[False]`` so a forged
      env value can never grant live authority — the real decision is computed
      by :func:`learning_live_readiness_health_metadata` from the selected-bot
      scope and the promotion stage;
    * a pure ``*_health_metadata`` function returning
      ``enabled``/``status``/``executionMode``/``readinessReady``/``reasonCodes``;
    * a canary-ladder promotion (selected-bot digest + env allowlist + a
      per-gate promotion stage).

Rollout ladder (matches workflow_executor_readiness):

    disabled
        ▼   gate enabled + selected scope matched + shadow enabled
    shadow      ── compute real adapter outputs, but WRITE / INJECT nothing
        ▼   promoted_gate >= _CANARY_LIVE_GATE AND canary_promotion_confirmed
    live        ── real adapters actually bind (recall injects, writes persist)

Env gate: ``MAGI_LEARNING_LIVE_ENABLED`` (default OFF).  When the env gate is
OFF the resolved mode is ``disabled`` regardless of config — exactly like
``workflow_executor``'s ``_executor_enabled`` short-circuit — so OFF stays
byte-identical to PR1–PR6.

No ``Literal[False]`` authority flag is flipped here.  Live behaviour is
introduced ONLY by the separate live layer (``learning/live.py``) when this gate
resolves to ``shadow``/``live`` AND the audit record is emitted.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


LearningLiveExecutionMode = Literal["disabled", "shadow", "live"]

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: Env variable that enables the LIVE learning layer (default OFF).
_LIVE_ENV_VAR: str = "MAGI_LEARNING_LIVE_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: The gate at which live (canary) binding first becomes eligible.  Below this
#: gate the learning layer stays shadow (compute-only) — never writes/injects.
_CANARY_LIVE_GATE: int = 5


def _live_env_enabled() -> bool:
    """Return whether the LIVE/authority tier is enabled (default OFF, opt-in).

    PR9a layered opt-out leaves the authority tier **default-OFF** — only the
    SAFE tier flips to default-ON.  Resolution now flows through
    :func:`resolve_learning_config` so that:

    * ``MAGI_LEARNING_LIVE_ENABLED`` is still the explicit opt-in (default OFF);
    * the master switch ``MAGI_LEARNING_ENABLED`` being explicitly falsy ALSO
      forces live off (``live_effective = enabled AND live_enabled``).

    No ``Literal[False]`` authority flag is consulted or flipped here; live
    binding still flows through the canary/readiness ladder + audit path below.
    """
    # Imported lazily to avoid a gates↔learning import cycle at module load.
    from magi_agent.learning.config import resolve_learning_config

    return resolve_learning_config().live_effective


class LearningLiveReadinessConfig(BaseModel):
    """Frozen rollout config for the learning-layer LIVE gate.

    Authority is NOT taken from config — ``live_authority_allowed`` is locked to
    ``Literal[False]`` so a forged env flag cannot grant live authority.  The
    real decision is computed by
    :func:`learning_live_readiness_health_metadata` from the selected-bot scope
    and the promotion stage (mirrors ``WorkflowExecutorReadinessConfig``).
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

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
    #: Highest gate reached in the gate1-5 ladder (0 = none).  Live binding is
    #: only eligible once this reaches ``_CANARY_LIVE_GATE``.
    promoted_gate: int = Field(default=0, ge=0, le=9, alias="promotedGate")
    #: Operator-confirmed canary promotion.  Live binding requires BOTH
    #: ``promoted_gate >= _CANARY_LIVE_GATE`` AND this flag.
    canary_promotion_confirmed: bool = Field(
        default=False,
        alias="canaryPromotionConfirmed",
    )
    #: LOCKED authority — never grants live authority regardless of forged env.
    live_authority_allowed: Literal[False] = Field(
        default=False,
        alias="liveAuthorityAllowed",
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

    @field_validator("live_authority_allowed", mode="before")
    @classmethod
    def _force_live_authority_false(cls, _value: object) -> bool:
        # Any forged truthy value is coerced to False — authority is gate-derived.
        return False

    @field_serializer("live_authority_allowed")
    def _serialize_live_authority_false(self, _value: object) -> bool:
        return False


def learning_live_readiness_health_metadata(
    config: LearningLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return the learning-layer LIVE rollout readiness metadata.

    Follows the ``*_health_metadata`` shape from
    ``workflow_executor_readiness_health_metadata``.  ``executionMode`` is the
    resolved rollout stage (``disabled``/``shadow``/``live``); ``status``
    mirrors it for the healthz surface; ``liveAuthorityAllowed`` is the single
    gate-derived authority flag (True only at/after the canary stage).

    The env gate ``MAGI_LEARNING_LIVE_ENABLED`` is an additional hard
    short-circuit: when OFF the resolved mode is ALWAYS ``disabled`` regardless
    of config — keeping OFF byte-identical to PR1–PR6.
    """
    env_on = _live_env_enabled()
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id, env_on=env_on)
    selected_scope_matched = _selected_scope_matched(
        config, bot_id=bot_id, user_id=user_id
    )

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: LearningLiveExecutionMode = "live"
        status = "live"
    elif reason_codes == ("selected_shadow_ready",):
        execution_mode = "shadow"
        status = "shadow"
    elif reason_codes == ("gate_disabled",) or reason_codes == ("env_gate_disabled",):
        execution_mode = "disabled"
        status = "disabled"
    else:
        # Any blocking reason (non-selected bot, malformed scope, kill switch,
        # bad environment, shadow disabled) fails closed to disabled.
        execution_mode = "disabled"
        status = "blocked"

    live_authority_allowed = execution_mode == "live"
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
        "liveAuthorityAllowed": live_authority_allowed,
        "reasonCodes": list(reason_codes),
    }


def resolve_learning_live_execution_mode(
    config: LearningLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> LearningLiveExecutionMode:
    """Convenience: resolve just the execution mode for the live learning layer."""
    meta = learning_live_readiness_health_metadata(
        config, bot_id=bot_id, user_id=user_id
    )
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"learning_live_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected 'disabled', 'shadow', or 'live'"
        )
    return mode  # type: ignore[return-value]


def _reason_codes(
    config: LearningLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
    env_on: bool,
) -> tuple[str, ...]:
    if not env_on:
        # Hard env short-circuit — OFF stays byte-identical to PR1–PR6.
        return ("env_gate_disabled",)
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
    config: LearningLiveReadinessConfig,
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
    "LearningLiveExecutionMode",
    "LearningLiveReadinessConfig",
    "learning_live_readiness_health_metadata",
    "resolve_learning_live_execution_mode",
]
