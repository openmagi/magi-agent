"""Single source of truth for Hipocampus memory activation (PR1).

This module resolves the one ``MemoryRuntimeConfig`` that every memory surface
reads — activation is decided in exactly one place via
:func:`resolve_memory_config`.  PR1 routes four surfaces through the resolver
(gates/memory_write_readiness, memory/policy, adapters/local_file_writable,
adapters/operator_soul_writer); the remaining consumers (harness/memory_recall
and config/env context-compaction) are converged in later PRs.

GOVERNANCE INVARIANT
--------------------
A flag gates *activation*, never *capability*:

  * ``MAGI_MEMORY_ENABLED`` (the new master switch) defaults **False** in PR1
    (it gets flipped to True in PR8).  When OFF every activation sub-flag is OFF
    and the memory path is inert and safe (no writes, no network, no provider /
    runtime activation).
  * When the master is ON the engine-activation sub-flags follow it, EXCEPT two
    documented opt-ins that stay False even under master-on: ``soul_write`` and
    ``vector_search``.

Resolution precedence for every sub-flag / tunable::

    explicit env / config override  >  MAGI_MEMORY_ENABLED master default  >  hardcoded default

The resolver carries NO runtime behaviour — it only computes booleans/ints.  The
two permanently-frozen authority fields (DB mutation, ADK-memory-service write)
are NOT modelled here; they stay ``Literal[False]`` on the harness configs by
design (Hipocampus is file-based and never writes a DB or the ADK MemoryService).

This module imports only stdlib + pydantic — no network/provider/runtime deps —
so it is safe to import from any memory surface or boundary test.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Env var names (single registry)
# ---------------------------------------------------------------------------

#: NEW master switch — default False in PR1, flipped to True in PR8.
MASTER_ENV_VAR: str = "MAGI_MEMORY_ENABLED"

#: Sub-flag env overrides.  These names match the pre-existing scattered reads
#: that this resolver replaces (see gates/memory_write_readiness.py and
#: adapters/local_file_writable.py) so callers can route through the resolver
#: WITHOUT changing the effective env contract.
WRITE_KILL_SWITCH_ENV_VAR: str = "MAGI_MEMORY_WRITE_KILL_SWITCH_ENABLED"
WRITE_READINESS_ENV_VAR: str = "MAGI_MEMORY_WRITE_READINESS_ENABLED"
WRITE_ENABLED_ENV_VAR: str = "MAGI_MEMORY_WRITE_ENABLED"
RECALL_ENABLED_ENV_VAR: str = "MAGI_MEMORY_RECALL_ENABLED"
PROJECTION_ENABLED_ENV_VAR: str = "MAGI_MEMORY_PROJECTION_ENABLED"
COMPACTION_ENABLED_ENV_VAR: str = "MAGI_MEMORY_COMPACTION_ENABLED"
SOUL_WRITE_ENABLED_ENV_VAR: str = "MAGI_SOUL_WRITE_ENABLED"
VECTOR_SEARCH_ENV_VAR: str = "MAGI_MEMORY_VECTOR_SEARCH"
#: Explicit opt-in (like ``vector_search``): allow the qmd backend to register a
#: NEW global qmd collection. Default False so a shared/multi-bot host is not
#: polluted with a global index just by turning memory on.
PREFER_QMD_AUTO_REGISTER_ENV_VAR: str = "MAGI_MEMORY_PREFER_QMD_AUTO_REGISTER"
#: Use the LOCAL ``memory/search`` backend (BM25/qmd) as the canonical recall
#: source for the read adapter's qmd-record path. Default False (the pre-existing
#: QmdClient HTTP → JSON-file path stays the behaviour).
PREFER_LOCAL_SEARCH_ENV_VAR: str = "MAGI_MEMORY_PREFER_LOCAL_SEARCH"

#: Tunable env overrides.
PREFER_QMD_ENV_VAR: str = "MAGI_MEMORY_PREFER_QMD"
COOLDOWN_HOURS_ENV_VAR: str = "MAGI_MEMORY_COOLDOWN_HOURS"
DAILY_THRESHOLD_ENV_VAR: str = "MAGI_MEMORY_DAILY_THRESHOLD"
WEEKLY_THRESHOLD_ENV_VAR: str = "MAGI_MEMORY_WEEKLY_THRESHOLD"
MONTHLY_THRESHOLD_ENV_VAR: str = "MAGI_MEMORY_MONTHLY_THRESHOLD"
ROOT_MAX_TOKENS_ENV_VAR: str = "MAGI_MEMORY_ROOT_MAX_TOKENS"
MODE_ENV_VAR: str = "MAGI_MEMORY_MODE"
RECALL_K_ENV_VAR: str = "MAGI_MEMORY_RECALL_K"
RECALL_MAX_BYTES_ENV_VAR: str = "MAGI_MEMORY_RECALL_MAX_BYTES"

#: config.toml table that mirrors the env names (snake_case keys).
CONFIG_TABLE: str = "memory"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

MemoryMode = Literal["normal", "aggressive", "conservative"]

#: Hardcoded defaults (apply when neither an explicit override nor the master
#: default selects a value).  Activation sub-flags default to False here; the
#: master-on default is layered on top in :func:`resolve_memory_config`.
_DEFAULT_PREFER_QMD = True
_DEFAULT_COOLDOWN_HOURS = 24
_DEFAULT_DAILY_THRESHOLD = 200
_DEFAULT_WEEKLY_THRESHOLD = 300
_DEFAULT_MONTHLY_THRESHOLD = 500
_DEFAULT_ROOT_MAX_TOKENS = 3000
_DEFAULT_MODE: MemoryMode = "normal"
_DEFAULT_RECALL_K = 8
_DEFAULT_RECALL_MAX_BYTES = 8192


class MemoryRuntimeConfig(BaseModel):
    """Frozen, immutable resolved memory activation config.

    Matches the repo's config-model style (frozen pydantic ``BaseModel`` with
    ``populate_by_name``).  Construction is normally via
    :func:`resolve_memory_config`; the model itself stores already-resolved
    values and carries no env logic.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    #: The new master switch.  Default False in PR1.
    master_enabled: bool = Field(default=False, alias="masterEnabled")

    # Activation sub-flags (follow master unless explicitly overridden).
    #: Kill-switch "engaged" => writes blocked.  Explicit-only: does NOT follow
    #: the master (master-on means writes alive), so it stays False unless set.
    write_kill_switch_enabled: bool = Field(default=False, alias="writeKillSwitchEnabled")
    write_readiness_enabled: bool = Field(default=False, alias="writeReadinessEnabled")
    write_enabled: bool = Field(default=False, alias="writeEnabled")
    recall_enabled: bool = Field(default=False, alias="recallEnabled")
    projection_enabled: bool = Field(default=False, alias="projectionEnabled")
    compaction_enabled: bool = Field(default=False, alias="compactionEnabled")
    #: Opt-in even under master-on — stays False unless explicitly enabled.
    soul_write_enabled: bool = Field(default=False, alias="soulWriteEnabled")
    #: Opt-in even under master-on — stays False unless explicitly enabled.
    vector_search: bool = Field(default=False, alias="vectorSearch")
    #: Opt-in even under master-on — stays False unless explicitly enabled.
    #: When False the qmd backend never registers a NEW global collection.
    prefer_qmd_auto_register: bool = Field(
        default=False, alias="preferQmdAutoRegister"
    )
    #: Follows the master (master-on => True) so per-turn dynamic recall is not
    #: silently double-gated off; an explicit env/config override still wins.
    #: The cost/multi-tenancy opt-ins (``vector_search``,
    #: ``prefer_qmd_auto_register``) remain explicit-only. When True the read
    #: adapter uses ``memory/search`` for qmd records.
    prefer_local_search: bool = Field(default=False, alias="preferLocalSearch")

    # Tunables.
    prefer_qmd: bool = Field(default=_DEFAULT_PREFER_QMD, alias="preferQmd")
    cooldown_hours: int = Field(default=_DEFAULT_COOLDOWN_HOURS, ge=0, alias="cooldownHours")
    daily_threshold: int = Field(default=_DEFAULT_DAILY_THRESHOLD, ge=1, alias="dailyThreshold")
    weekly_threshold: int = Field(default=_DEFAULT_WEEKLY_THRESHOLD, ge=1, alias="weeklyThreshold")
    monthly_threshold: int = Field(
        default=_DEFAULT_MONTHLY_THRESHOLD, ge=1, alias="monthlyThreshold"
    )
    root_max_tokens: int = Field(default=_DEFAULT_ROOT_MAX_TOKENS, ge=1, alias="rootMaxTokens")
    mode: MemoryMode = Field(default=_DEFAULT_MODE)
    recall_k: int = Field(default=_DEFAULT_RECALL_K, ge=1, alias="recallK")
    recall_max_bytes: int = Field(
        default=_DEFAULT_RECALL_MAX_BYTES, ge=1, alias="recallMaxBytes"
    )


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_memory_config(
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, object] | None = None,
) -> MemoryRuntimeConfig:
    """Resolve the single ``MemoryRuntimeConfig`` for this process.

    ``env`` and ``config`` are injectable for testing; they default to the real
    process environment and an empty config table (config.toml loading lives in
    the CLI layer — callers that have already loaded it pass the parsed dict).

    Precedence for every sub-flag / tunable:
    explicit env / config override > master default > hardcoded default.
    """
    env = os.environ if env is None else env
    table = _memory_table(config)

    master = _resolve_bool(
        env,
        table,
        env_var=MASTER_ENV_VAR,
        config_key="enabled",
        default=False,  # TODO(PR8): default=True
    )

    def sub_flag(env_var: str, config_key: str, *, master_default: bool) -> bool:
        # explicit override wins; otherwise fall back to the master default.
        override = _override_bool(env, table, env_var=env_var, config_key=config_key)
        if override is not None:
            return override
        return master_default

    return MemoryRuntimeConfig(
        masterEnabled=master,
        # The kill-switch is "engaged" (writes blocked) only when explicitly set;
        # it must NOT follow the master (master-on means writes ALIVE), so its
        # master_default is False regardless of the master switch.
        writeKillSwitchEnabled=sub_flag(
            WRITE_KILL_SWITCH_ENV_VAR, "write_kill_switch_enabled", master_default=False
        ),
        writeReadinessEnabled=sub_flag(
            WRITE_READINESS_ENV_VAR, "write_readiness_enabled", master_default=master
        ),
        writeEnabled=sub_flag(
            WRITE_ENABLED_ENV_VAR, "write_enabled", master_default=master
        ),
        recallEnabled=sub_flag(
            RECALL_ENABLED_ENV_VAR, "recall_enabled", master_default=master
        ),
        projectionEnabled=sub_flag(
            PROJECTION_ENABLED_ENV_VAR, "projection_enabled", master_default=master
        ),
        compactionEnabled=sub_flag(
            COMPACTION_ENABLED_ENV_VAR, "compaction_enabled", master_default=master
        ),
        # Opt-ins: stay False even when the master is on.
        soulWriteEnabled=sub_flag(
            SOUL_WRITE_ENABLED_ENV_VAR, "soul_write_enabled", master_default=False
        ),
        vectorSearch=sub_flag(
            VECTOR_SEARCH_ENV_VAR, "vector_search", master_default=False
        ),
        # Opt-ins: stay False even when the master is on (explicit-only).
        preferQmdAutoRegister=sub_flag(
            PREFER_QMD_AUTO_REGISTER_ENV_VAR,
            "prefer_qmd_auto_register",
            master_default=False,
        ),
        # Follows the master: per-turn dynamic recall (recall_enabled AND
        # prefer_local_search) must not be silently double-gated off once the
        # master is on. An explicit override (env/config) still wins, so an
        # operator can opt out under master-on.
        preferLocalSearch=sub_flag(
            PREFER_LOCAL_SEARCH_ENV_VAR, "prefer_local_search", master_default=master
        ),
        preferQmd=_resolve_bool(
            env, table, env_var=PREFER_QMD_ENV_VAR, config_key="prefer_qmd",
            default=_DEFAULT_PREFER_QMD,
        ),
        cooldownHours=_resolve_int(
            env, table, env_var=COOLDOWN_HOURS_ENV_VAR, config_key="cooldown_hours",
            default=_DEFAULT_COOLDOWN_HOURS, minimum=0,
        ),
        dailyThreshold=_resolve_int(
            env, table, env_var=DAILY_THRESHOLD_ENV_VAR, config_key="daily_threshold",
            default=_DEFAULT_DAILY_THRESHOLD, minimum=1,
        ),
        weeklyThreshold=_resolve_int(
            env, table, env_var=WEEKLY_THRESHOLD_ENV_VAR, config_key="weekly_threshold",
            default=_DEFAULT_WEEKLY_THRESHOLD, minimum=1,
        ),
        monthlyThreshold=_resolve_int(
            env, table, env_var=MONTHLY_THRESHOLD_ENV_VAR, config_key="monthly_threshold",
            default=_DEFAULT_MONTHLY_THRESHOLD, minimum=1,
        ),
        rootMaxTokens=_resolve_int(
            env, table, env_var=ROOT_MAX_TOKENS_ENV_VAR, config_key="root_max_tokens",
            default=_DEFAULT_ROOT_MAX_TOKENS, minimum=1,
        ),
        mode=_resolve_mode(env, table),
        recallK=_resolve_int(
            env, table, env_var=RECALL_K_ENV_VAR, config_key="recall_k",
            default=_DEFAULT_RECALL_K, minimum=1,
        ),
        recallMaxBytes=_resolve_int(
            env, table, env_var=RECALL_MAX_BYTES_ENV_VAR, config_key="recall_max_bytes",
            default=_DEFAULT_RECALL_MAX_BYTES, minimum=1,
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _memory_table(config: Mapping[str, object] | None) -> Mapping[str, object]:
    if not isinstance(config, Mapping):
        return {}
    section = config.get(CONFIG_TABLE)
    return section if isinstance(section, Mapping) else {}


def coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return None


def _override_bool(
    env: Mapping[str, str],
    table: Mapping[str, object],
    *,
    env_var: str,
    config_key: str,
) -> bool | None:
    """Return the explicit override (env beats config), or None if neither set."""
    if env_var in env:
        coerced = coerce_bool(env.get(env_var))
        if coerced is not None:
            return coerced
    if config_key in table:
        coerced = coerce_bool(table.get(config_key))
        if coerced is not None:
            return coerced
    return None


def _resolve_bool(
    env: Mapping[str, str],
    table: Mapping[str, object],
    *,
    env_var: str,
    config_key: str,
    default: bool,
) -> bool:
    override = _override_bool(env, table, env_var=env_var, config_key=config_key)
    return default if override is None else override


def _resolve_int(
    env: Mapping[str, str],
    table: Mapping[str, object],
    *,
    env_var: str,
    config_key: str,
    default: int,
    minimum: int,
) -> int:
    # An out-of-range value gets the SAME forgiveness as a malformed string:
    # clamp-to-default rather than let the pydantic ``ge=`` field constraint
    # raise a ValidationError out of resolve_memory_config() (which runs inside
    # gates/adapters — an operator typo must not crash a gate evaluation).
    def _checked(value: int) -> int:
        return value if value >= minimum else default

    raw = env.get(env_var)
    if raw is not None and str(raw).strip():
        try:
            return _checked(int(str(raw).strip()))
        except ValueError:
            return default
    if config_key in table:
        candidate = table.get(config_key)
        if isinstance(candidate, bool):
            return default
        if isinstance(candidate, int):
            return _checked(candidate)
        if isinstance(candidate, str) and candidate.strip():
            try:
                return _checked(int(candidate.strip()))
            except ValueError:
                return default
    return default


def _resolve_mode(
    env: Mapping[str, str],
    table: Mapping[str, object],
) -> MemoryMode:
    raw = env.get(MODE_ENV_VAR)
    if raw is None or not str(raw).strip():
        candidate = table.get("mode")
        raw = candidate if isinstance(candidate, str) else None
    if raw is None:
        return _DEFAULT_MODE
    normalized = str(raw).strip().lower()
    if normalized in {"normal", "aggressive", "conservative"}:
        return normalized  # type: ignore[return-value]
    return _DEFAULT_MODE


__all__ = [
    "CONFIG_TABLE",
    "MASTER_ENV_VAR",
    "PREFER_LOCAL_SEARCH_ENV_VAR",
    "PREFER_QMD_AUTO_REGISTER_ENV_VAR",
    "MemoryMode",
    "MemoryRuntimeConfig",
    "coerce_bool",
    "resolve_memory_config",
]
