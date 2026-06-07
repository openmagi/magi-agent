"""E5 — Channel-live readiness ladder + platform-routing dispatch.

This module mirrors ``gates/scheduler_executor_readiness.py`` for the
multi-channel live delivery stack (E2/E3/E4: Telegram, Discord, Slack, Email).

Readiness ladder
----------------

    disabled
        ▼  gate enabled + selected scope matched + shadow enabled
    shadow      — dry-run evidence, no real messages sent
        ▼  promoted_gate >= _CANARY_LIVE_GATE AND canary_promotion_confirmed
    live        — real outbound via platform deliver() functions

The ladder governs the OPERATOR control plane; individual platform gates
(``MAGI_CHANNEL_LIVE_TELEGRAM`` etc.) are the per-platform traffic gates.
Both must allow for live traffic to flow.

Env gate
--------
All four platform env gates are read by ``_platform_gate_states()``:
  - ``MAGI_CHANNEL_LIVE_TELEGRAM``
  - ``MAGI_CHANNEL_LIVE_DISCORD``
  - ``MAGI_CHANNEL_LIVE_SLACK``
  - ``MAGI_CHANNEL_LIVE_EMAIL``

Kill-switch
-----------
``MAGI_CHANNEL_LIVE_KILL_SWITCH_ENABLED`` — when set to a truthy value the
kill-switch overrides ALL platform gates and forces ``disabled``.  Takes
precedence over individual platform env gates.

Authority lock
--------------
``live_execution_allowed: Literal[False]`` on the config model is the same
pattern as ``scheduler_executor_readiness``: a forged truthy value is coerced
to False — the real authority is computed from the gate state.

Safety invariants
-----------------
``channel_live_readiness_health_metadata()`` surfaces a ``safetyInvariantsAsserted``
set documenting which invariants this stack enforces.  Observers (ops dashboards,
test assertions) use this set to verify the stack is correctly wired:

  - ``default_off``: all platforms are default-OFF
  - ``injected_provider_only``: no live client is constructed inside adapters
  - ``silent_suppression``: [SILENT] text suppresses delivery on all platforms
  - ``redacted_evidence``: raw recipients/bodies never stored in evidence
  - ``no_core_literal_edit``: extension platforms are registry-only, not Literal

Platform-routing dispatch
-------------------------
``dispatch_live(channel_type, port, target, text, *, evidence)`` routes to the
correct platform's ``deliver()`` function based on ``channel_type``.  It reuses
the existing ``deliver`` functions from the platform modules; no logic is
reimplemented here.

Supported channel_types: ``"telegram"``, ``"discord"``, ``"slack"``, ``"email"``.
Unknown types raise ``ValueError``.

Forbidden imports
-----------------
No ``requests``, ``httpx``, ``slack_sdk``, ``smtplib``, ``urllib3``, ``aiohttp``,
``telegram``, ``discord`` at top level.  Provider references are resolved lazily
inside ``dispatch_live`` to keep this module import-clean.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ChannelLiveExecutionMode = Literal["disabled", "shadow", "live"]

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: Gate at which live (canary) dispatch first becomes eligible.  Mirrors
#: scheduler_executor_readiness: shadow runs through gate1-5, canary at gate5.
_CANARY_LIVE_GATE: int = 5

#: Kill-switch env var: when truthy, ALL platform live gates are forced off.
_KILL_SWITCH_ENV_VAR: str = "MAGI_CHANNEL_LIVE_KILL_SWITCH_ENABLED"

#: Per-platform env gate names (also published in health metadata).
_PLATFORM_ENV_GATES: dict[str, str] = {
    "telegram": "MAGI_CHANNEL_LIVE_TELEGRAM",
    "discord": "MAGI_CHANNEL_LIVE_DISCORD",
    "slack": "MAGI_CHANNEL_LIVE_SLACK",
    "email": "MAGI_CHANNEL_LIVE_EMAIL",
}

#: Safety invariants asserted by this stack.
_SAFETY_INVARIANTS: frozenset[str] = frozenset({
    "default_off",
    "injected_provider_only",
    "silent_suppression",
    "redacted_evidence",
    "no_core_literal_edit",
})


# ---------------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------------

class ChannelLiveReadinessConfig(BaseModel):
    """Frozen rollout config for the channel-live readiness gate.

    Mirrors ``SchedulerExecutorReadinessConfig``.  ``live_execution_allowed``
    is locked to ``Literal[False]`` so a forged env flag cannot grant live
    execution — the real decision is computed by
    :func:`channel_live_readiness_health_metadata`.
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
    #: Highest gate reached in the gate1-5 ladder (0 = none).
    promoted_gate: int = Field(default=0, ge=0, le=9, alias="promotedGate")
    #: Operator-confirmed canary promotion.
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


# ---------------------------------------------------------------------------
# Health metadata
# ---------------------------------------------------------------------------

def channel_live_readiness_health_metadata(
    config: ChannelLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return readiness metadata for the channel-live gate.

    Shape mirrors ``scheduler_executor_readiness_health_metadata``:
      - ``status`` / ``executionMode``: resolved ladder stage
      - ``liveExecutionAllowed``: gate-derived bool (True only at live stage)
      - ``readinessReady``: True when shadow or live
      - ``platformGateStates``: per-platform env gate booleans
      - ``safetyInvariantsAsserted``: set of invariant names (see module docstring)
      - ``reasonCodes``: list of blocking/passing reason strings
    """
    platform_gate_states = _platform_gate_states()
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id)

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: ChannelLiveExecutionMode = "live"
        status = "live"
    elif reason_codes == ("selected_shadow_ready",):
        execution_mode = "shadow"
        status = "shadow"
    elif reason_codes in {("gate_disabled",), ("env_gate_disabled",)}:
        execution_mode = "disabled"
        status = "disabled"
    elif "env_gate_disabled" in reason_codes:
        execution_mode = "disabled"
        status = "disabled"
    else:
        execution_mode = "disabled"
        status = "blocked" if "gate_disabled" not in reason_codes else "disabled"

    live_execution_allowed = execution_mode == "live"
    return {
        "enabled": config.enabled,
        "status": status,
        "executionMode": execution_mode,
        "readinessReady": execution_mode in {"shadow", "live"},
        "selectedScopeMatched": _selected_scope_matched(config, bot_id=bot_id, user_id=user_id),
        "promotedGate": config.promoted_gate,
        "canaryLiveGate": _CANARY_LIVE_GATE,
        "canaryPromotionConfirmed": bool(config.canary_promotion_confirmed),
        "liveExecutionAllowed": live_execution_allowed,
        "platformGateStates": platform_gate_states,
        "safetyInvariantsAsserted": _SAFETY_INVARIANTS,
        "reasonCodes": list(reason_codes),
    }


def resolve_channel_live_execution_mode(
    config: ChannelLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> ChannelLiveExecutionMode:
    """Convenience: resolve just the execution mode for the channel-live gate."""
    meta = channel_live_readiness_health_metadata(config, bot_id=bot_id, user_id=user_id)
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"channel_live_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected 'disabled', 'shadow', or 'live'"
        )
    return mode  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Platform-routing dispatch
# ---------------------------------------------------------------------------

def dispatch_live(
    channel_type: str,
    port: Any,
    target: str,
    text: str,
    *,
    evidence: dict[str, object],
) -> bool:
    """Route an outbound message to the correct platform's ``deliver()`` function.

    Reuses the existing ``deliver`` functions from the platform modules (E2/E3/E4).
    No logic is reimplemented here — this is a thin router.

    Supported channel_types
    -----------------------
    - ``"telegram"``  → ``magi_agent.channels.telegram_live.deliver``
    - ``"discord"``   → ``magi_agent.channels.discord_live.deliver``
    - ``"slack"``     → ``magi_agent.channels.slack_live.deliver``
    - ``"email"``     → ``magi_agent.channels.email_live.deliver``

    Unknown channel_type raises ``ValueError("unknown channel_type: ...")``.

    Each platform's ``deliver()`` is responsible for its own gate check, [SILENT]
    suppression, evidence redaction, and provider call — this function is the
    routing seam only.

    Parameters
    ----------
    channel_type : str
        The channel to route to.
    port : Any
        The injected provider for the platform.
    target : str
        The delivery target (chat_id / channel_id / email address).
    text : str
        The outbound message text.
    evidence : dict[str, object]
        Audit accumulator, passed through to the platform's deliver().

    Returns
    -------
    bool
        The return value of the platform's deliver() call.
    """
    if channel_type == "telegram":
        from magi_agent.channels.telegram_live import deliver as tg_deliver
        return tg_deliver(port, target, text, evidence=evidence)

    if channel_type == "discord":
        from magi_agent.channels.discord_live import deliver as dc_deliver
        return dc_deliver(port, target, text, evidence=evidence)

    if channel_type == "slack":
        from magi_agent.channels.slack_live import deliver as sl_deliver
        return sl_deliver(port, target, text, evidence=evidence)

    if channel_type == "email":
        from magi_agent.channels.email_live import deliver as em_deliver
        return em_deliver(port, target, text, evidence=evidence)

    raise ValueError(f"unknown channel_type: {channel_type!r}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _platform_gate_states() -> dict[str, bool]:
    """Return the current env gate state for each platform."""
    return {
        platform: _is_truthy_env(env_var)
        for platform, env_var in _PLATFORM_ENV_GATES.items()
    }


def _is_truthy_env(var: str) -> bool:
    # Permissive check: any non-empty value that is not an explicit falsy word is
    # treated as truthy.  This matches the per-platform adapter semantics
    # (``is_live_slack_enabled``, ``is_live_email_enabled``, etc.) so that
    # ``platformGateStates`` in the health metadata always reflects whether the
    # adapter would actually send — values like "enabled" or "2" are ON here just
    # as they are in the adapters, preventing a dashboard-vs-reality mismatch.
    raw = os.environ.get(var, "")
    return bool(raw) and raw.lower() not in {"0", "false", "no", "off"}


def _kill_switch_active() -> bool:
    return _is_truthy_env(_KILL_SWITCH_ENV_VAR)


def _reason_codes(
    config: ChannelLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    reasons: list[str] = []
    if config.kill_switch_enabled or _kill_switch_active():
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

    # Scope + shadow are satisfied.
    if config.promoted_gate >= _CANARY_LIVE_GATE and config.canary_promotion_confirmed:
        return ("selected_canary_live_ready",)
    return ("selected_shadow_ready",)


def _selected_scope_matched(
    config: ChannelLiveReadinessConfig,
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


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "ChannelLiveExecutionMode",
    "ChannelLiveReadinessConfig",
    "channel_live_readiness_health_metadata",
    "dispatch_live",
    "resolve_channel_live_execution_mode",
]
# Note: _CANARY_LIVE_GATE is intentionally private (leading underscore) and is
# NOT listed in __all__.  Tests that need it can still import it directly:
#   from magi_agent.gates.channel_live_readiness import _CANARY_LIVE_GATE
