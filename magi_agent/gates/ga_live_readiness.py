"""General-Automation live-execution rollout readiness gate — Track 19 PR4.

The GA live harness (``harness/general_automation/live_gate.py``) is the first
real live-execution path for the ``general`` agent role.  Its rollout MUST flow
through the same gate-ladder pattern established by
``gates/workflow_executor_readiness.py`` (Track 17 PR6): shadow → canary
``186bf3d7`` → gate≥5 → fleet, fail-closed, with a single locked authority flag.

Rollout ladder (mirrors the PR4 mission):

    disabled
        ▼   flag on + selected scope + shadow mode enabled
    shadow      ── validate + observe on REAL traffic, ZERO live mutation
        ▼   promotedGate >= _CANARY_LIVE_GATE AND canaryPromotionConfirmed
    live        ── the gate actually permits tool-level mutations for general role

The single authority flag — ``liveExecutionAllowed`` — is derived ENTIRELY from
the gate decision; the config's ``live_execution_allowed`` field is locked to
``Literal[False]`` so a forged env value can never grant live authority (same
defence used by gate7 ``child_execution_allowed`` and the workflow executor's
``live_dispatch_allowed``). Default-OFF is preserved: an unconfigured gate
resolves to ``disabled``.

Telemetry:
    :func:`emit_ga_live_telemetry_record` is a lightweight helper that wraps
    :func:`magi_agent.telemetry.logging.log_record`.  Call it from the gate's
    live execution sites to emit ``ga_live.*`` structured log records; the
    calling surfaces (``live_gate.py``, task-completion verifier) may call it
    directly. No new telemetry infrastructure is introduced here.
"""
from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.telemetry.logging import log_record


GaLiveExecutionMode = Literal["disabled", "shadow", "live"]

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: The gate at which live (canary) execution first becomes eligible.
#: Below this gate the GA live execution stays shadow/observe-only.
#: Mirrors ``_CANARY_LIVE_GATE`` in ``workflow_executor_readiness.py``.
_CANARY_LIVE_GATE: int = 5


class GaLiveReadinessConfig(BaseModel):
    """Frozen rollout config for the GA live execution gate.

    Authority is NOT taken from config — ``live_execution_allowed`` is locked to
    ``Literal[False]`` so a forged env flag cannot grant live execution.  The real
    decision is computed by :func:`ga_live_readiness_health_metadata` from the
    selected-bot scope and the promotion stage.
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
    #: Highest gate reached in the gate1-5 ladder (0 = none).  Live execution
    #: is only eligible once this reaches ``_CANARY_LIVE_GATE``.
    promoted_gate: int = Field(default=0, ge=0, le=9, alias="promotedGate")
    #: Operator-confirmed canary promotion (186bf3d7 → fleet).  Live execution
    #: requires BOTH ``promoted_gate >= _CANARY_LIVE_GATE`` AND this flag.
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


def ga_live_readiness_health_metadata(
    config: GaLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return the GA live execution rollout readiness metadata.

    Follows the ``workflow_executor_readiness_health_metadata`` shape.
    ``executionMode`` is the resolved rollout stage (``disabled``/``shadow``/
    ``live``); ``liveExecutionAllowed`` is the single gate-derived authority
    flag (True only at/after the canary-live stage).
    """
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id)
    selected_scope_matched = _selected_scope_matched(
        config, bot_id=bot_id, user_id=user_id
    )

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: GaLiveExecutionMode = "live"
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

    live_execution_allowed = execution_mode == "live"
    return {
        "enabled": config.enabled,
        "status": status,
        "executionMode": execution_mode,
        "readinessReady": execution_mode in {"shadow", "live"},
        "selectedScopeMatched": selected_scope_matched,
        "promotedGate": config.promoted_gate,
        "canaryLiveGate": _CANARY_LIVE_GATE,
        "canaryPromotionConfirmed": bool(config.canary_promotion_confirmed),
        "liveExecutionAllowed": live_execution_allowed,
        # Telemetry counter contract (populated at runtime by gate / verifier;
        # surfaced here so ops dashboards know which counters to expect).
        "counterRequirements": [
            "gatedCalls",
            "blocked",
            "approvalRequired",
            "allowed",
            "completionVerifierRepairs",
        ],
        "reasonCodes": list(reason_codes),
    }


def resolve_ga_live_execution_mode(
    config: GaLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> GaLiveExecutionMode:
    """Convenience: resolve just the execution mode for the GA live gate."""
    meta = ga_live_readiness_health_metadata(
        config, bot_id=bot_id, user_id=user_id
    )
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"ga_live_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected one of 'disabled', 'shadow', 'live'"
        )
    return mode  # type: ignore[return-value]


def emit_ga_live_telemetry_record(
    *,
    event: str,
    decision: str,
    bot_id: str,
    execution_mode: str,
    detail: str | None = None,
) -> dict[str, object]:
    """Emit a structured telemetry log record for a GA live gate event.

    Wraps :func:`magi_agent.telemetry.logging.log_record`.  Call this from
    ``live_gate.py`` and the task-completion verifier to record gate outcomes
    under the ``ga_live.*`` namespace.  The record is returned so callers can
    pass it to their logger or trace, keeping this function side-effect-free.

    Args:
        event: One of ``"gated_call"``, ``"completion_verifier_repair"``, or
            any other GA live event label.
        decision: ``"allow"``, ``"deny"``, ``"ask"``, or ``"repair"``.
        bot_id: The bot identifier for the call context.
        execution_mode: The resolved execution mode (``"disabled"``,
            ``"shadow"``, or ``"live"``).
        detail: Optional extra detail (e.g. reason code, missing receipt ref).
    """
    # Intentionally-unwired seam: call sites (live_gate / completion path) are
    # wired in a later PR.
    fields: dict[str, object] = {
        "event": event,
        "decision": decision,
        "botId": bot_id,
        "executionMode": execution_mode,
    }
    if detail is not None:
        fields["detail"] = detail
    return log_record("info", f"ga_live.{event}", **fields)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reason_codes(
    config: GaLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    # Fail-closed: an empty/whitespace bot_id or user_id can never resolve to a
    # scope match because sha256("") would trivially match a misconfigured
    # selectedBotDigest=sha256("").
    if not bot_id.strip() or not user_id.strip():
        return ("malformed_caller_identity",)
    reasons: list[str] = []
    if config.kill_switch_enabled:
        reasons.append("kill_switch_enabled")
    if not config.shadow_mode_enabled:
        # Shadow is the safest first stage and a prerequisite for any live
        # promotion — without it the gate cannot observe real traffic at all.
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
    if (
        config.promoted_gate >= _CANARY_LIVE_GATE
        and config.canary_promotion_confirmed
    ):
        return ("selected_canary_live_ready",)
    return ("selected_shadow_ready",)


def _selected_scope_matched(
    config: GaLiveReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> bool:
    if not config.enabled:
        return False
    if not bot_id.strip() or not user_id.strip():
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
    "GaLiveExecutionMode",
    "GaLiveReadinessConfig",
    "emit_ga_live_telemetry_record",
    "ga_live_readiness_health_metadata",
    "resolve_ga_live_execution_mode",
]
