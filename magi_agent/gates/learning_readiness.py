"""Learning reflection readiness gate — PR2.

Mirrors the pattern established by ``gates/workflow_executor_readiness.py``
(frozen pydantic config, ``Literal[False]`` authority flag, pure
``*_health_metadata`` function returning ``enabled``/``status``/
``readinessReady``/``reasonCodes``).

The ``reflect_authority`` flag is locked to ``Literal[False]`` regardless of
forged env so a misconfigured deployment cannot grant live reflection without
going through the proper promotion ladder (PR7+).

Rollout ladder (PR2 skeleton):

    disabled  ── gate.enabled = False (default)
        ▼
    enabled   ── gate.enabled = True (local-fake + test; no real writes)

Real transcript attachment and production write authority are deferred to PR7.
Default-OFF is preserved: an unconfigured gate resolves to ``disabled``.

Env gate: ``MAGI_LEARNING_REFLECTION_ENABLED`` (see ``harness/learning_executor.py``).
No ``Literal[False]`` authority flags are flipped here.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class LearningReadinessConfig(BaseModel):
    """Frozen readiness config for the learning reflection gate.

    Authority is NOT taken from config — ``reflect_authority`` is locked to
    ``Literal[False]`` so a forged env flag cannot grant reflection authority.
    The real promotion decision is deferred to PR7.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    enabled: bool = False
    kill_switch_enabled: bool = Field(default=True, alias="killSwitchEnabled")
    #: LOCKED authority — never grants reflection authority regardless of forged env.
    #: Real attachment is deferred to PR7.
    reflect_authority: Literal[False] = Field(
        default=False,
        alias="reflectAuthority",
    )

    @field_validator("reflect_authority", mode="before")
    @classmethod
    def _force_reflect_authority_false(cls, _value: object) -> bool:
        # Any forged truthy value is coerced to False — authority is gate-derived.
        return False

    @field_serializer("reflect_authority")
    def _serialize_reflect_authority_false(self, _value: object) -> bool:
        return False


#: Reflect-tier execution mode (PR9a).  Distinct from the authority-tier
#: ``disabled``/``shadow``/``live`` ladder in ``learning_live_readiness``.  The
#: reflect tier covers ONLY the safe operations: real LOCAL session read +
#: deterministic label + propose to the LOCAL store.  It carries NO live
#: authority — ``reflect_authority`` stays ``Literal[False]`` and the three
#: frozen attestation flags on ``LearningReflectionConfig`` stay False.
LearningReflectTierMode = Literal["disabled", "reflect"]


def resolve_learning_reflect_tier_mode(
    config: object | None = None,
) -> LearningReflectTierMode:
    """Resolve the reflect-tier execution mode under PR9a layered opt-out.

    The reflect tier (real local session read → deterministic label → propose to
    the LOCAL store) is **ready by default** when the safe tier is on, i.e. when
    the resolved :class:`~magi_agent.learning.config.LearningConfig` has
    ``enabled AND reflection_enabled`` (``reflection_effective``).  This is
    achieved WITHOUT flipping any ``Literal[False]`` flag —
    ``reflect_authority`` / ``llm_attached`` / ``production_write_enabled`` /
    ``real_transcript_source_attached`` all stay locked False.  The real
    transcript binding (PR9b) is attested through the existing DI + audit path,
    exactly like PR7; this resolver only reports the *tier* the bootstrap should
    run in.

    Returns ``"reflect"`` when the safe tier is on, else ``"disabled"``.

    Args:
        config: Optional resolved ``LearningConfig``.  When ``None`` it is
            resolved from the environment via ``resolve_learning_config()`` so
            an unconfigured install resolves to ``"reflect"`` (default-ready).
    """
    # Imported lazily to avoid a learning↔gates import cycle at module load.
    from magi_agent.learning.config import (
        LearningConfig,
        resolve_learning_config,
    )

    resolved: LearningConfig
    if config is None:
        resolved = resolve_learning_config()
    elif isinstance(config, LearningConfig):
        resolved = config
    else:  # pragma: no cover - defensive; callers pass LearningConfig or None
        raise TypeError(
            "resolve_learning_reflect_tier_mode expects a LearningConfig or None"
        )

    return "reflect" if resolved.reflection_effective else "disabled"


def learning_readiness_health_metadata(
    config: LearningReadinessConfig,
) -> dict[str, object]:
    """Return the learning-reflection readiness metadata.

    Follows the ``*_health_metadata`` shape from ``gate7_readiness`` and
    ``workflow_executor_readiness_health_metadata``.  ``status`` is the
    resolved stage (``disabled``/``enabled``); ``readinessReady`` is True
    only when the gate is enabled and the kill switch is not active.
    ``reflectAuthority`` is always False in PR2 (locked by config validator).
    """
    reason_codes = _reason_codes(config)

    if not config.enabled:
        status = "disabled"
        readiness_ready = False
    elif config.kill_switch_enabled:
        status = "disabled"
        readiness_ready = False
    else:
        status = "enabled"
        readiness_ready = True

    return {
        "enabled": config.enabled,
        "status": status,
        "readinessReady": readiness_ready,
        "reflectAuthority": False,  # always False in PR2
        "reasonCodes": list(reason_codes),
    }


def _reason_codes(config: LearningReadinessConfig) -> tuple[str, ...]:
    if not config.enabled:
        return ("gate_disabled",)
    reasons: list[str] = []
    if config.kill_switch_enabled:
        reasons.append("kill_switch_enabled")
    if reasons:
        return tuple(dict.fromkeys(reasons))
    return ("enabled",)


__all__ = [
    "LearningReadinessConfig",
    "LearningReflectTierMode",
    "learning_readiness_health_metadata",
    "resolve_learning_reflect_tier_mode",
]
