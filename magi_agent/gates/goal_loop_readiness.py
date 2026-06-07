"""Goal-loop rollout readiness gate — Track B, PR B5.

The persistent goal loop (``harness/goal_loop_control.py``, ``harness/goal_loop.py``)
is the Ralph loop autonomous continuation engine.  Its live promotion MUST flow
through the EXISTING gate pipeline rather than a parallel framework.  This module
mirrors the readiness-gate pattern established by
``gates/scheduler_executor_readiness.py`` and ``gates/learning_live_readiness.py``:

    * a frozen pydantic config whose single authority flag
      (``live_execution_allowed``) is locked to ``Literal[False]`` so a forged
      env value can never grant live authority — the real decision is computed by
      :func:`goal_loop_readiness_health_metadata` from the selected scope and the
      promotion stage;
    * a pure ``*_health_metadata`` function returning
      ``enabled``/``status``/``executionMode``/``readinessReady``/``reasonCodes``;
    * a canary-ladder promotion (selected scope digest + env allowlist + a
      per-gate promotion stage aligned with gate5 in ``api_canary_ladder``).

Rollout ladder (matches scheduler_executor_readiness):

    disabled
        ▼   gate enabled + selected scope matched + shadow enabled
    shadow      ── dry-run evidence recorded, no loop continuation dispatched
        ▼   promoted_gate >= _CANARY_LIVE_GATE AND canary_promotion_confirmed
    live        ── real continuation dispatch via goal_loop_control

Env gate: ``MAGI_GOAL_LOOP_ENABLED`` (default OFF).  When the env gate is OFF
the resolved mode is ``disabled`` regardless of config — exactly like
``scheduler_executor_readiness``'s env short-circuit.

Kill-switch: ``MAGI_GOAL_LOOP_KILL_SWITCH_ENABLED`` (default OFF, but the config
model defaults ``kill_switch_enabled=True`` so the default config blocks live).

Canary gate
-----------
The goal loop rides the same canary-ladder live-promotion threshold as the scheduler
mission gate (gate_id=5, ``gate5_scheduler_cron_mission`` in the main registry).
The ``_CANARY_LIVE_GATE = 5`` constant is the single reference point — live dispatch
requires ``promoted_gate >= _CANARY_LIVE_GATE AND canary_promotion_confirmed``.
No standalone factory is needed (and none exists, matching the scheduler precedent
which also only uses the constant).

Spawn-depth + ownership enforcement
------------------------------------
:func:`check_goal_loop_spawn_depth_gate` surfaces the spawn-depth cap (reuses
``DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH=2`` from ``goal_loop.py``).
:func:`check_goal_loop_ownership_assignment` gates OWNERSHIP ASSIGNMENT only: main
(``depth=0``) may own scheduling/persistence; child agents can NEVER own (participation
in iteration is a separate concern not gated here).  Both reuse models from
``goal_loop.py`` (``GoalLoopSpawnDepthPolicy``, ``GoalLoopOwnershipScope``) — no
duplication.

B1–B4 safety invariants asserted
---------------------------------
This readiness module is a DECISION/PROJECTION layer only.  It enables nothing:
``live_execution_allowed`` stays ``Literal[False]``; ``GoalLoopPolicy``'s
``traffic_attached``/``execution_attached`` remain ``Literal[False]``; no agent turn
is executed or spawned here.

Forbidden imports: urllib, socket, subprocess, http, requests, google.adk,
adk_bridge — none appear here.
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from magi_agent.harness.goal_loop import (
    DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH,
    GoalLoopAgentScope,
    GoalLoopSpawnDepthPolicy,
)


GoalLoopExecutionMode = Literal["disabled", "shadow", "live"]

_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")
_SAFE_ENVIRONMENTS = frozenset({"local", "development", "staging", "production"})

#: Env variable that enables the goal loop executor (default OFF).
_GOAL_LOOP_ENV_VAR: str = "MAGI_GOAL_LOOP_ENABLED"
#: Env variable for the kill-switch (default OFF — but config defaults to True).
_KILL_SWITCH_ENV_VAR: str = "MAGI_GOAL_LOOP_KILL_SWITCH_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})

#: The gate at which live (canary) dispatch first becomes eligible.  Mirrors
#: scheduler_executor_readiness: shadow runs through gate1-5, then canary at
#: gate5 flips live.  The goal loop is the mission-continuation analogue of the
#: scheduler mission gate and shares this promotion threshold.
_CANARY_LIVE_GATE: int = 5


def _goal_loop_env_enabled() -> bool:
    """Return True only when the env gate is explicitly set to a truthy value."""
    return os.environ.get(_GOAL_LOOP_ENV_VAR, "").lower() in _TRUE_STRINGS


class GoalLoopReadinessConfig(BaseModel):
    """Frozen rollout config for the goal-loop readiness gate.

    Authority is NOT taken from config — ``live_execution_allowed`` is locked to
    ``Literal[False]`` so a forged env flag cannot grant live execution.  The real
    decision is computed by :func:`goal_loop_readiness_health_metadata` from the
    selected scope and the promotion stage (mirrors
    ``SchedulerExecutorReadinessConfig``).
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


def goal_loop_readiness_health_metadata(
    config: GoalLoopReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> dict[str, object]:
    """Return the goal-loop rollout readiness metadata.

    Follows the ``*_health_metadata`` shape from
    ``scheduler_executor_readiness_health_metadata``.  ``executionMode`` is the
    resolved rollout stage (``disabled``/``shadow``/``live``); ``status``
    mirrors it for the healthz surface; ``liveExecutionAllowed`` is the single
    gate-derived authority flag (True only at/after the canary stage).

    The env gate ``MAGI_GOAL_LOOP_ENABLED`` is an additional hard short-circuit:
    when OFF the resolved mode is ALWAYS ``disabled`` regardless of config —
    keeping OFF byte-identical to pre-B5.
    """
    env_on = _goal_loop_env_enabled()
    reason_codes = _reason_codes(config, bot_id=bot_id, user_id=user_id, env_on=env_on)
    selected_scope_matched = _selected_scope_matched(
        config, bot_id=bot_id, user_id=user_id
    )

    if reason_codes == ("selected_canary_live_ready",):
        execution_mode: GoalLoopExecutionMode = "live"
        status = "live"
    elif reason_codes == ("selected_shadow_ready",):
        execution_mode = "shadow"
        status = "shadow"
    elif reason_codes in {("gate_disabled",), ("env_gate_disabled",)}:
        execution_mode = "disabled"
        status = "disabled"
    elif "env_gate_disabled" in reason_codes:
        # Env gate is off together with other blocking reasons (e.g. kill switch).
        # The loop is simply OFF — not in a conflict state — so use "disabled",
        # not "blocked".
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
        # Telemetry counter contract (populated at runtime by the loop driver;
        # surfaced here so ops dashboards know which counters to expect).
        "counterRequirements": [
            "continued",
            "stopped",
            "spend_capped",
            "judge_budget_exhausted",
            "evidence_unmet",
            "preempted",
        ],
        "reasonCodes": list(reason_codes),
    }


def resolve_goal_loop_execution_mode(
    config: GoalLoopReadinessConfig,
    *,
    bot_id: str,
    user_id: str,
) -> GoalLoopExecutionMode:
    """Convenience: resolve just the execution mode for the goal loop."""
    meta = goal_loop_readiness_health_metadata(
        config, bot_id=bot_id, user_id=user_id
    )
    mode = meta["executionMode"]
    if mode not in ("disabled", "shadow", "live"):
        raise ValueError(
            f"goal_loop_readiness_health_metadata returned unexpected"
            f" executionMode {mode!r}; expected 'disabled', 'shadow', or 'live'"
        )
    return mode  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Spawn-depth enforcement
# ---------------------------------------------------------------------------

def check_goal_loop_spawn_depth_gate(
    spawn_depth: int,
    *,
    policy: GoalLoopSpawnDepthPolicy | None = None,
) -> dict[str, object]:
    """Check that ``spawn_depth`` is within the allowed goal-loop spawn-depth cap.

    Reuses :data:`DEFAULT_GOAL_LOOP_MAX_SPAWN_DEPTH` and
    :class:`GoalLoopSpawnDepthPolicy` from ``goal_loop.py`` — no duplication.

    Returns a dict with:
      allowed      — True when depth is within bounds.
      spawnDepth   — the checked depth value.
      maxSpawnDepth — the effective max (from policy or default).
      reasonCode   — short code when not allowed.
    """
    resolved_policy = policy or GoalLoopSpawnDepthPolicy()
    max_depth = resolved_policy.max_depth

    if isinstance(spawn_depth, bool) or not isinstance(spawn_depth, int):
        return {
            "allowed": False,
            "spawnDepth": spawn_depth,
            "maxSpawnDepth": max_depth,
            "reasonCode": "invalid_spawn_depth_type",
        }
    if spawn_depth < 0:
        return {
            "allowed": False,
            "spawnDepth": spawn_depth,
            "maxSpawnDepth": max_depth,
            "reasonCode": "spawn_depth_negative",
        }
    if spawn_depth > max_depth:
        return {
            "allowed": False,
            "spawnDepth": spawn_depth,
            "maxSpawnDepth": max_depth,
            "reasonCode": "spawn_depth_exceeded",
        }
    return {
        "allowed": True,
        "spawnDepth": spawn_depth,
        "maxSpawnDepth": max_depth,
        "reasonCode": "",
    }


# ---------------------------------------------------------------------------
# Ownership enforcement
# ---------------------------------------------------------------------------

def check_goal_loop_ownership_assignment(
    agent_scope: GoalLoopAgentScope,
    spawn_depth: int,
) -> dict[str, object]:
    """Gate OWNERSHIP ASSIGNMENT for the goal loop.

    This function gates *ownership assignment only* — it does NOT gate
    participation in iteration (child agents may participate in a goal-loop
    iteration driven by the main agent; that is a separate concern not checked
    here).

    Ownership assignment rules:
      - ``main`` agent (``spawn_depth=0``) MAY own scheduling and persistence.
      - ``child`` agents (``spawn_depth > 0``) can NEVER own scheduling or
        persistence — only the main agent owns the goal loop.

    Reuses :class:`GoalLoopAgentScope` from ``goal_loop.py`` — no duplication.

    Returns a dict with:
      ownershipValid — True only when the agent is the main agent at depth 0.
      agentScope     — the provided agent scope.
      spawnDepth     — the provided spawn depth.
      reasonCode     — short code when ownership assignment is rejected.
    """
    if agent_scope == "main":
        if spawn_depth != 0:
            return {
                "ownershipValid": False,
                "agentScope": agent_scope,
                "spawnDepth": spawn_depth,
                "reasonCode": "main_agent_must_use_spawn_depth_0",
            }
        return {
            "ownershipValid": True,
            "agentScope": agent_scope,
            "spawnDepth": spawn_depth,
            "reasonCode": "",
        }
    # child scope
    if spawn_depth <= 0:
        return {
            "ownershipValid": False,
            "agentScope": agent_scope,
            "spawnDepth": spawn_depth,
            "reasonCode": "child_agents_must_use_spawn_depth_greater_than_0",
        }
    # Child agents may participate in iteration but CANNOT own scheduling or
    # persistence — that's the main agent's responsibility.
    return {
        "ownershipValid": False,
        "agentScope": agent_scope,
        "spawnDepth": spawn_depth,
        "reasonCode": "child_cannot_own_goal_loop_persistence_or_scheduling",
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _reason_codes(
    config: GoalLoopReadinessConfig,
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
        # reasons so that callers can see the full picture.  Mirrors
        # scheduler_executor_readiness._reason_codes.
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
    config: GoalLoopReadinessConfig,
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
    "GoalLoopExecutionMode",
    "GoalLoopReadinessConfig",
    "check_goal_loop_ownership_assignment",
    "check_goal_loop_spawn_depth_gate",
    "goal_loop_readiness_health_metadata",
    "resolve_goal_loop_execution_mode",
]
