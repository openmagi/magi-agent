"""Self-review stack LIVE readiness gate — Track C, PR C4.

Promotes the self-review stack (C1 fork, C2 pipeline, C3 curator) from
``disabled`` → ``shadow`` → ``live`` via the same canary-ladder pattern
established by ``learning_live_readiness`` and ``scheduler_executor_readiness``.

    * a frozen pydantic config whose single authority flag
      (``live_execution_allowed``) is locked to ``Literal[False]`` so a forged
      env value can never grant live execution — the real decision is computed
      by :func:`self_review_readiness_health_metadata` from the selected-bot
      scope and the promotion stage;
    * a pure ``*_health_metadata`` function returning
      ``enabled``/``status``/``executionMode``/``readinessReady``/``reasonCodes``;
    * a canary-ladder promotion (selected-bot digest + env allowlist + a
      per-gate promotion stage).

Rollout ladder (matches learning_live_readiness / scheduler_executor_readiness):

    disabled
        ▼   gate enabled + selected scope matched + shadow enabled
    shadow      ── C1 fork emits candidates, C2 records but does NOT activate,
                   C3 curator computes but does NOT archive
        ▼   promoted_gate >= _CANARY_LIVE_GATE AND canary_promotion_confirmed
    live        ── C1 fork may act on candidates, C2 pipeline may activate
                   example-class items, C3 curator may archive

Safety invariants asserted:
  • C1 fork cache NEVER mutated (fork contract — checked via pre/post fingerprint).
  • C1 fork restricted toolset ENFORCED (REVIEW_DISABLED_TOOLSETS — no shell /
    network / FS writes).
  • C2 eval-gate thresholds NOT weakened (EvalGateConfig defaults apply).
  • C2 rule items NEVER auto-activate without a human ``approval_ref``
    (``policy:no-direct-mutation`` — this is the headline safety property of
    the whole track and is a first-class checkable criterion here).
  • C3 curator archive-only + snapshot-backed (conservative rule, NOT hard-delete).

Env gate: ``MAGI_SELF_REVIEW_LIVE_ENABLED`` (default OFF).  When this env gate
is OFF the resolved mode is ``disabled`` regardless of config — exactly like
``learning_live_readiness``'s short-circuit.  There is no kill-switch env var;
the kill switch is the config field ``kill_switch_enabled`` (default ``True`` =
blocking/safe).  An operator sets it ``False`` in config to allow promotion.

Triple default-OFF: the env gate is OFF, the config ``enabled=False``, and the
config field ``kill_switch_enabled=True`` (blocking) — three independent
defaults that must all be deliberately cleared before any live promotion is
possible.

Human-approval invariant
------------------------
``check_rule_human_approval_invariant`` is a standalone checker that confirms
the C2 pipeline CANNOT activate a ``rule``-class item without a human
``approval_ref``.  This is the headline safety property of the whole track.
It is exposed as a first-class criterion in the readiness metadata so ops can
verify it independently.

Telemetry
---------
``emit_self_review_rollout_staging_event`` reuses the ``learning/telemetry.py``
machinery (same ``DeterministicRuntimeEvent`` envelope, same PII rules) to
surface self-review metrics:

    * candidates_proposed     — fork proposals emitted per session
    * examples_auto_activated — example-class candidates activated by C2
    * rules_pending_approval  — rule-class candidates pending human approval
    * curator_archived        — items archived by C3

All counters only.  No live backend.  Gated on
``MAGI_SELF_REVIEW_TELEMETRY_ENABLED`` (default OFF).

Canary
------
``_CANARY_LIVE_GATE`` mirrors the precedent constant (gate 5 = canary-eligible).
We do NOT mutate any shared canary registry — this module only references the
constant for internal promotion logic (same pattern as learning_live_readiness /
scheduler_executor_readiness).

Forbidden imports: urllib, socket, subprocess, http, requests — none appear here.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.telemetry.deterministic_events import DeterministicRuntimeEvent
from magi_agent.telemetry.logging import log_record

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types / constants
# ---------------------------------------------------------------------------

SelfReviewExecutionMode = Literal["disabled", "shadow", "live"]

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: Env variable that enables the live self-review layer (default OFF).
_LIVE_ENV_VAR: str = "MAGI_SELF_REVIEW_LIVE_ENABLED"
#: Env variable that enables self-review telemetry (default OFF).
_TELEMETRY_ENV_VAR: str = "MAGI_SELF_REVIEW_TELEMETRY_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: The gate at which live (canary) binding first becomes eligible.  Mirrors
#: learning_live_readiness / scheduler_executor_readiness: shadow runs through
#: gates 1–5, then canary at gate 5 flips live.
_CANARY_LIVE_GATE: int = 5

#: Stable null digests for telemetry events that have no policy/ledger of their own.
_NULL_DIGEST = "sha256:" + "0" * 64

# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------


def _live_env_enabled() -> bool:
    """Return True only when the env gate is explicitly set to a truthy value."""
    return os.environ.get(_LIVE_ENV_VAR, "").lower() in _TRUE_STRINGS


def _telemetry_enabled() -> bool:
    """Return True only when self-review telemetry is explicitly enabled."""
    return os.environ.get(_TELEMETRY_ENV_VAR, "").lower() in _TRUE_STRINGS


# ---------------------------------------------------------------------------
# Frozen readiness config
# ---------------------------------------------------------------------------


class SelfReviewReadinessConfig(BaseModel):
    """Frozen rollout config for the self-review stack live gate.

    Authority is NOT taken from config — ``live_execution_allowed`` is locked
    to ``Literal[False]`` so a forged env flag cannot grant live execution.  The
    real decision is computed by :func:`self_review_readiness_health_metadata`
    from the selected-bot scope and the promotion stage.

    Mirrors ``LearningLiveReadinessConfig`` / ``SchedulerExecutorReadinessConfig``.
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
# Human-approval invariant checker
# ---------------------------------------------------------------------------


def check_rule_human_approval_invariant(
    *,
    candidate_kind: str,
    approval_ref: str | None,
) -> dict[str, object]:
    """Check that a ``rule``-class candidate CANNOT be activated without an
    ``approval_ref``.

    This is the headline safety property of the C1–C4 self-review track:
    **rule candidates MUST have human approval before activation**.  Example-class
    candidates may be auto-activated by the eval-gate (C2 policy), but ``rule``
    items are NEVER activated without a human-supplied ``approval_ref``.

    Parameters
    ----------
    candidate_kind:
        The learning kind string (``"rule"``, ``"example"``, ``"eval"``, etc.).
    approval_ref:
        The human approval reference (``None`` when absent).

    Returns
    -------
    dict with:
      invariant_holds — True when the invariant is satisfied (either kind is not
                        ``"rule"``, or kind is ``"rule"`` AND approval_ref is present).
      kind            — the candidate kind checked.
      approval_ref    — the supplied approval ref (None if absent).
      reason          — human-readable explanation.
    """
    if candidate_kind != "rule":
        return {
            "invariant_holds": True,
            "kind": candidate_kind,
            "approval_ref": approval_ref,
            "reason": (
                f"Non-rule candidate kind {candidate_kind!r} does not require "
                "human approval before activation."
            ),
        }

    has_approval = approval_ref is not None and approval_ref.strip() != ""
    if has_approval:
        return {
            "invariant_holds": True,
            "kind": candidate_kind,
            "approval_ref": approval_ref,
            "reason": "Rule candidate has human approval_ref — invariant satisfied.",
        }

    return {
        "invariant_holds": False,
        "kind": candidate_kind,
        "approval_ref": None,
        "reason": (
            "Rule candidate CANNOT be activated without a human approval_ref "
            "(policy:no-direct-mutation). "
            "Provide an approval_ref before attempting rule activation."
        ),
    }


# ---------------------------------------------------------------------------
# Readiness health metadata
# ---------------------------------------------------------------------------


def self_review_readiness_health_metadata(
    config: SelfReviewReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return the self-review stack live rollout readiness metadata.

    Follows the ``*_health_metadata`` shape from ``learning_live_readiness`` and
    ``scheduler_executor_readiness``.  ``executionMode`` is the resolved rollout
    stage (``disabled``/``shadow``/``live``); ``status`` mirrors it for the
    healthz surface; ``liveExecutionAllowed`` is the gate-derived authority flag
    (True only at/after the canary stage).

    The env gate ``MAGI_SELF_REVIEW_LIVE_ENABLED`` is a hard short-circuit: when
    OFF the resolved mode is ALWAYS ``disabled`` regardless of config.

    Safety invariant criteria are surfaced in ``invariantCriteria`` so ops
    dashboards can verify C1–C3 invariants independently.
    """
    env_on = _live_env_enabled()
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id, env_on=env_on)
    selected_scope_matched = _selected_scope_matched(
        config, bot_id=bot_id, user_id=user_id
    )

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: SelfReviewExecutionMode = "live"
        status = "live"
    elif reason_codes == ("selected_shadow_ready",):
        execution_mode = "shadow"
        status = "shadow"
    elif reason_codes in {("gate_disabled",), ("env_gate_disabled",)}:
        execution_mode = "disabled"
        status = "disabled"
    elif "env_gate_disabled" in reason_codes:
        # Env gate is off together with other blocking reasons.
        # The feature is simply OFF — not in a conflict state — so use "disabled".
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
        # Safety invariant criteria — surfaced for ops dashboards.
        "invariantCriteria": [
            "c1_fork_cache_untouched",
            "c1_restricted_toolset_enforced",
            "c2_eval_gate_thresholds_not_weakened",
            "c2_rule_no_auto_activate_without_approval_ref",
            "c3_archive_only_snapshot_backed",
        ],
        # Telemetry counter contract (populated at runtime by the stack components).
        "counterRequirements": [
            "candidates_proposed",
            "examples_auto_activated",
            "rules_pending_approval",
            "curator_archived",
        ],
        "reasonCodes": list(reason_codes),
    }


def resolve_self_review_execution_mode(
    config: SelfReviewReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> SelfReviewExecutionMode:
    """Convenience: resolve just the execution mode for the self-review stack."""
    meta = self_review_readiness_health_metadata(
        config, bot_id=bot_id, user_id=user_id
    )
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"self_review_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected 'disabled', 'shadow', or 'live'"
        )
    return mode  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


def emit_self_review_rollout_staging_event(
    *,
    tenant_id: str,
    bot_id: str,
    execution_mode: str,
    promoted_gate: int,
    canary_live_gate: int,
    user_id_digest: str,
    candidates_proposed: int = 0,
    examples_auto_activated: int = 0,
    rules_pending_approval: int = 0,
    curator_archived: int = 0,
    sink: object | None = None,
) -> DeterministicRuntimeEvent | None:
    """Emit the self-review readiness ladder stage as a telemetry event.

    Reuses the ``DeterministicRuntimeEvent`` envelope and the same PII rules as
    ``learning/telemetry.py``:
      * raw user id is NEVER recorded — only its ``sha256:`` digest.
      * ``activationEnabled == False`` enforced by the model.
      * ``redactionStatus="redacted"``.

    Counters surfaced:
      candidates_proposed       — fork proposals emitted per session
      examples_auto_activated   — example-class candidates activated by C2
      rules_pending_approval    — rule-class candidates pending human approval
      curator_archived          — items archived by C3

    Gated on ``MAGI_SELF_REVIEW_TELEMETRY_ENABLED`` (default OFF).
    When OFF this is a no-op returning None — zero side effects.

    The real fleet/canary wiring lives in the hosted monorepo, out of OSS scope;
    this module surfaces the generic stage + scope (hashed owner digest,
    opaque bot_id) only.
    """
    if not _telemetry_enabled():
        return None

    try:
        event = DeterministicRuntimeEvent(
            eventId="self_review.rollout.staging",
            runId="self_review.rollout",
            workflowId="openmagi.self_review",
            stepId="self_review.staging",
            eventType="checkpoint",
            routeDecision="self_review_rollout_staging",
            effectivePolicySnapshotDigest=_NULL_DIGEST,
            ledgerHeadDigest=_NULL_DIGEST,
            repairAttempt=0,
            projectionMode="self_review_rollout_staging",
            redactionStatus="redacted",
            metadata={
                "tenantId": tenant_id,
                "botId": bot_id,
                "executionMode": execution_mode,
                "promotedGate": int(promoted_gate),
                "canaryLiveGate": int(canary_live_gate),
                "ownerUserIdDigest": user_id_digest,
                "candidatesProposed": int(candidates_proposed),
                "examplesAutoActivated": int(examples_auto_activated),
                "rulesPendingApproval": int(rules_pending_approval),
                "curatorArchived": int(curator_archived),
            },
        )
    except Exception:
        logger.warning(
            "self_review telemetry event construction failed; dropping event "
            "(execution_mode=%s)",
            execution_mode,
        )
        return None

    if sink is not None:
        if callable(sink):
            sink(event)  # type: ignore[operator]
    else:
        log_record(
            "info",
            "self_review.telemetry",
            event=event.model_dump(by_alias=True, mode="json"),
        )
    return event


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _reason_codes(
    config: SelfReviewReadinessConfig,
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
        # reasons so callers can see the full picture.
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
    config: SelfReviewReadinessConfig,
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
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "SelfReviewExecutionMode",
    "SelfReviewReadinessConfig",
    "check_rule_human_approval_invariant",
    "emit_self_review_rollout_staging_event",
    "resolve_self_review_execution_mode",
    "self_review_readiness_health_metadata",
]
