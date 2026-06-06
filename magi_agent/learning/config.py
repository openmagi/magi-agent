"""Learning Layer configuration + resolution — PR9a (layered opt-out).

PR1–PR8 shipped the whole Learning Layer **default-OFF** behind a flat set of
``MAGI_LEARNING_*`` env gates.  PR9a flips that to a **layered opt-out** model:

    * a SAFE tier that is ON by default at install — reflection scheduling +
      structural signal extraction + the **deterministic** labeler + writing
      ``proposed`` items to the LOCAL learning store + the dashboard API mount +
      telemetry.  None of that sends anything externally, costs model tokens, or
      changes prompt/agent behaviour; human approval is still required before an
      item goes active.
    * an OPT-IN tier that stays default-OFF — the **LLM** labeler (model cost),
      **auto prompt injection** (behaviour change), and **production/live writes
      + real authority** (the three frozen ``Literal[False]`` attestation flags).

A single master switch (:attr:`LearningConfig.enabled`) turns the whole layer
inert.  When ``enabled=False`` every gate that consults this config resolves to
OFF and the runtime surface is **byte-identical** to the PR1–PR8 all-OFF state.

This module is the *config + resolution* layer ONLY.  It does NOT wire runtime
startup, cron scheduling, or the real-source binding — that is PR9b.  And it
NEVER flips any ``Literal[False]`` authority flag: the safe-tier default-ON is
achieved purely by gate/readiness **defaults** and config resolution.  The real
live binding still flows through the existing DI + audit path
(``gates/learning_live_readiness.py`` + ``learning/live.py``).

Resolution precedence (highest wins)::

    explicit override  >  env var (if explicitly set)  >  opt-out default

"explicitly set" means the env var is PRESENT in the environment, even if its
value is falsy.  An UNSET env var means "use the opt-out default" (which is now
ON for the safe tier).  This is the whole point: a fresh install with no
``MAGI_LEARNING_*`` env vars gets the safe tier ON; an operator who wants it off
sets ``MAGI_LEARNING_ENABLED=false`` (master) or a specific tier's gate to a
falsy value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Env var names (kept identical to PR1–PR8 so existing deployments keep working
# as OVERRIDES; new master/labeler/injection gates are added alongside).
# ---------------------------------------------------------------------------

#: NEW master switch — default ON (opt-out).  Falsy ⇒ entire layer inert.
ENV_MASTER = "MAGI_LEARNING_ENABLED"
#: Existing safe-tier gates (now opt-out: unset ⇒ ON).
ENV_REFLECTION = "MAGI_LEARNING_REFLECTION_ENABLED"
ENV_REFLECTION_INTERVAL = "MAGI_LEARNING_REFLECTION_INTERVAL"
ENV_DASHBOARD = "MAGI_LEARNING_DASHBOARD_ENABLED"
ENV_TELEMETRY = "MAGI_LEARNING_TELEMETRY_ENABLED"
#: NEW labeler selector — ``deterministic`` (default, safe) | ``llm`` (opt-in).
ENV_LABELER = "MAGI_LEARNING_LABELER"
#: NEW auto-injection gate — default OFF (opt-in; behaviour change).
ENV_INJECTION = "MAGI_LEARNING_INJECTION_ENABLED"
#: Existing live/authority gate — default OFF (opt-in; real authority).
ENV_LIVE = "MAGI_LEARNING_LIVE_ENABLED"

_TRUE_STRINGS = frozenset({"1", "true", "yes", "on"})
_FALSE_STRINGS = frozenset({"0", "false", "no", "off", ""})

LearningLabeler = Literal["deterministic", "llm"]

_DEFAULT_REFLECTION_INTERVAL_HOURS = 24


def _parse_bool_env(value: str | None) -> bool | None:
    """Parse an env-var string into a tri-state bool.

    Returns ``True``/``False`` when the value is a recognised truthy/falsy
    token, or ``None`` when the var is UNSET (``value is None``) so the caller
    can fall back to the opt-out default.  An unrecognised non-empty token is
    treated as ``False`` (conservative — an operator typo never silently enables
    a cost/behaviour-changing tier, and for the safe tier a typo is harmless).
    """
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in _TRUE_STRINGS:
        return True
    return False


def _parse_labeler_env(value: str | None) -> LearningLabeler | None:
    """Parse the labeler selector env var; ``None`` when unset/unrecognised."""
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered == "llm":
        return "llm"
    if lowered == "deterministic":
        return "deterministic"
    # Unrecognised token ⇒ fall back to the safe default rather than guessing.
    return None


def _parse_int_env(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


class LearningConfig(BaseModel):
    """Resolved Learning Layer configuration (layered opt-out).

    Frozen pydantic v2, camelCase aliases, ``extra="forbid"``.

    Tiers:

    * **master** — :attr:`enabled` (default ``True``).  ``False`` ⇒ the whole
      layer is inert; every consuming gate resolves OFF, byte-identical to the
      PR1–PR8 all-OFF state.
    * **SAFE / default-ON** — :attr:`reflection_enabled`,
      :attr:`dashboard_enabled`, :attr:`telemetry_enabled`, and
      :attr:`labeler` == ``"deterministic"``.  No external sends, no model cost,
      no prompt/behaviour change; human approval still required.
    * **OPT-IN / default-OFF** — :attr:`labeler` == ``"llm"`` (model cost),
      :attr:`injection_enabled` (behaviour change), and :attr:`live_enabled`
      (real authority; maps to the existing LIVE/authority tier).

    This config NEVER carries or flips a ``Literal[False]`` attestation flag.
    The frozen flags live on the readiness/live configs and are promoted only
    through the existing audit path; here :attr:`live_enabled` merely selects
    *whether* that opt-in authority ladder is consulted at all.
    """

    model_config = ConfigDict(
        frozen=True,
        populate_by_name=True,
        extra="forbid",
        validate_default=True,
    )

    # --- master ---
    enabled: bool = True

    # --- SAFE tier (default-ON) ---
    reflection_enabled: bool = Field(default=True, alias="reflectionEnabled")
    reflection_interval_hours: int = Field(
        default=_DEFAULT_REFLECTION_INTERVAL_HOURS,
        gt=0,
        alias="reflectionIntervalHours",
    )
    dashboard_enabled: bool = Field(default=True, alias="dashboardEnabled")
    telemetry_enabled: bool = Field(default=True, alias="telemetryEnabled")
    labeler: LearningLabeler = "deterministic"

    # --- OPT-IN tier (default-OFF) ---
    injection_enabled: bool = Field(default=False, alias="injectionEnabled")
    live_enabled: bool = Field(default=False, alias="liveEnabled")

    # ------------------------------------------------------------------
    # Effective-gate helpers (master AND tier).  These are the values the
    # consuming read points must use so that master-off forces every tier off.
    # ------------------------------------------------------------------

    @property
    def reflection_effective(self) -> bool:
        """``enabled AND reflection_enabled`` — the effective reflection gate."""
        return self.enabled and self.reflection_enabled

    @property
    def dashboard_effective(self) -> bool:
        return self.enabled and self.dashboard_enabled

    @property
    def telemetry_effective(self) -> bool:
        return self.enabled and self.telemetry_enabled

    @property
    def injection_effective(self) -> bool:
        return self.enabled and self.injection_enabled

    @property
    def live_effective(self) -> bool:
        return self.enabled and self.live_enabled

    @property
    def llm_labeler_effective(self) -> bool:
        """LLM labeler is opt-in AND requires the master switch on."""
        return self.enabled and self.labeler == "llm"


def resolve_learning_config(
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, object] | None = None,
) -> LearningConfig:
    """Resolve a :class:`LearningConfig` with **opt-out** defaults.

    Precedence (highest first):

        1. ``overrides`` — an explicit value supplied by a caller (e.g. a parsed
           config file or a programmatic override) ALWAYS wins.
        2. env var — when the corresponding ``MAGI_LEARNING_*`` var is PRESENT
           in *env* (even with a falsy value), it forces the field.
        3. opt-out default — when neither an override nor an env var is given,
           the field takes its :class:`LearningConfig` default (ON for the safe
           tier, OFF for the opt-in tier).

    Existing env var names keep working as overrides; an UNSET env var means
    "use the default", which is now ON for the safe tier (the whole point of the
    flip).

    Args:
        env: Environment mapping (defaults to ``os.environ``).
        overrides: Optional explicit field overrides, keyed by the snake_case
            field name (e.g. ``{"reflection_enabled": False}``) or its camelCase
            alias.  These win over env and default.

    Returns:
        A frozen :class:`LearningConfig`.
    """
    if env is None:
        env = os.environ
    overrides = dict(overrides or {})

    # --- env layer: collect only the explicitly-set vars (present overrides
    # the opt-out default; unset leaves pydantic to apply the default). ---
    env_layer: dict[str, object] = {}

    def _env_bool(field: str, env_name: str) -> None:
        env_val = _parse_bool_env(env.get(env_name))
        if env_val is not None:
            env_layer[field] = env_val

    _env_bool("enabled", ENV_MASTER)
    _env_bool("reflection_enabled", ENV_REFLECTION)
    _env_bool("dashboard_enabled", ENV_DASHBOARD)
    _env_bool("telemetry_enabled", ENV_TELEMETRY)
    _env_bool("injection_enabled", ENV_INJECTION)
    _env_bool("live_enabled", ENV_LIVE)

    env_labeler = _parse_labeler_env(env.get(ENV_LABELER))
    if env_labeler is not None:
        env_layer["labeler"] = env_labeler

    env_interval = _parse_int_env(env.get(ENV_REFLECTION_INTERVAL))
    if env_interval is not None:
        env_layer["reflection_interval_hours"] = env_interval

    # --- precedence: overrides (snake or camelCase alias) win over env, which
    # wins over the opt-out default.  pydantic resolves alias keys; the env
    # layer always uses canonical snake_case field names, so an override given
    # under either form supersedes the corresponding env value.  Drop any env
    # value whose field the overrides also set (under either name) so the
    # override is authoritative. ---
    _alias_to_field = {
        f.alias: name
        for name, f in LearningConfig.model_fields.items()
        if f.alias is not None
    }
    overridden_fields = {
        _alias_to_field.get(key, key) for key in overrides
    }
    merged: dict[str, object] = {
        field: value
        for field, value in env_layer.items()
        if field not in overridden_fields
    }
    merged.update(overrides)

    return LearningConfig.model_validate(merged)


__all__ = [
    "ENV_DASHBOARD",
    "ENV_INJECTION",
    "ENV_LABELER",
    "ENV_LIVE",
    "ENV_MASTER",
    "ENV_REFLECTION",
    "ENV_REFLECTION_INTERVAL",
    "ENV_TELEMETRY",
    "LearningConfig",
    "LearningLabeler",
    "resolve_learning_config",
]
