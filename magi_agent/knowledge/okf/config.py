"""Single source of truth for OKF knowledge-bundle activation (PR1).

Resolves the one :class:`OkfConfig` that every OKF surface reads.  Mirrors
``magi_agent/memory/config.py``: a master switch + cascade sub-flags + tunables,
all resolved through one injectable-``env`` function so the resolver is hermetic
and carries NO runtime behaviour (it only computes booleans/ints/strings).

GOVERNANCE INVARIANT
--------------------
A flag gates *activation*, never *capability*:

  * ``MAGI_KNOWLEDGE_OKF_ENABLED`` (master) defaults **False**.  When OFF the OKF
    loader/tool/injection path is inert (no reads, no tool surface, no prompt
    injection).
  * When the master is ON the ``lookup`` sub-flag follows it, EXCEPT
    ``index_inject`` which is a documented opt-in that stays False even under
    master-on (Mode B index injection mutates the prompt; opt-in only).

Resolution precedence for every sub-flag / tunable::

    explicit env / config override  >  MAGI_KNOWLEDGE_OKF_ENABLED master default
    >  hardcoded default

This module imports only stdlib + pydantic + the ``config._bool_resolution``
leaf (config-owned, itself stdlib-only) — no network/provider/runtime deps,
and crucially nothing from ``magi_agent.memory`` (the OKF trust path is kept
decoupled from the memory subsystem per the design's boundary invariants).
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from magi_agent.config._bool_resolution import (
    coerce_bool,
    override_bool,
    resolve_bool,
)

# ---------------------------------------------------------------------------
# Env var names (single registry)
# ---------------------------------------------------------------------------

#: Master switch — default False.  Gates the entire OKF path.
MASTER_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_ENABLED"

#: Sub-flag env overrides.
LOOKUP_ENABLED_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_LOOKUP_ENABLED"
INDEX_INJECT_ENABLED_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_INDEX_INJECT_ENABLED"

#: Auto-type capability — index docs lacking a valid ``type`` as ``document``.
#: Follows the master (``master_default=master``): a capability that rescues
#: otherwise-skipped docs without changing existing (typed) results.
AUTO_TYPE_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_AUTO_TYPE"

#: Default fallback scope — opt-in string (does NOT follow master). ``knowledge_root``
#: widens the fallback bundle root from ``knowledge/okf`` to the whole ``knowledge/``.
SCOPE_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_SCOPE"

#: Bundle path list (colon-separated directories).
BUNDLE_PATHS_ENV_VAR: str = "MAGI_OKF_BUNDLE_PATHS"

#: Tunable env overrides.
MAX_RECORDS_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_MAX_RECORDS"
MAX_PREVIEW_CHARS_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_MAX_PREVIEW_CHARS"
MAX_DOCS_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_MAX_DOCS"
MAX_TOTAL_BYTES_ENV_VAR: str = "MAGI_KNOWLEDGE_OKF_MAX_TOTAL_BYTES"

#: config.toml table that mirrors the env names (snake_case keys).
CONFIG_TABLE: str = "knowledge_okf"

#: Allowed values for ``default_scope``.  Anything else falls back to the default.
_SCOPE_OKF_SUBDIR = "okf_subdir"
_SCOPE_KNOWLEDGE_ROOT = "knowledge_root"
_ALLOWED_SCOPES = frozenset({_SCOPE_OKF_SUBDIR, _SCOPE_KNOWLEDGE_ROOT})
_DEFAULT_SCOPE = _SCOPE_OKF_SUBDIR

# ---------------------------------------------------------------------------
# Hardcoded defaults / bounds
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RECORDS = 8
_MIN_MAX_RECORDS = 1
_MAX_MAX_RECORDS = 20

_DEFAULT_MAX_PREVIEW_CHARS = 2000
_MIN_MAX_PREVIEW_CHARS = 0
_MAX_MAX_PREVIEW_CHARS = 8000

_DEFAULT_MAX_DOCS = 500
_DEFAULT_MAX_TOTAL_BYTES = 33554432  # 32 MiB

#: Per-document body byte cap (256 KiB).  A document whose body exceeds this is
#: truncated by the loader with ``truncated=True`` (never dropped silently).
MAX_DOC_BYTES: int = 262144


class OkfConfig(BaseModel):
    """Frozen, immutable resolved OKF activation config.

    Construction is normally via :func:`resolve_okf_config`; the model stores
    already-resolved values and carries no env logic.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True, extra="forbid")

    #: Master switch.  Default False.
    master_enabled: bool = Field(default=False, alias="masterEnabled")

    #: Mode A ``OkfLookup`` tool exposure — follows the master.
    lookup_enabled: bool = Field(default=False, alias="lookupEnabled")
    #: Mode B index injection — opt-in even under master-on.
    index_inject_enabled: bool = Field(default=False, alias="indexInjectEnabled")
    #: Auto-type capability — follows the master. When True the loader indexes
    #: docs lacking a valid ``type`` as ``document`` instead of skipping them.
    auto_type: bool = Field(default=False, alias="autoType")

    #: Resolved bundle directories (colon-split, blanks dropped).
    bundle_paths: tuple[str, ...] = Field(default=(), alias="bundlePaths")

    #: Fallback bundle-root scope.  ``"okf_subdir"`` (default) → ``knowledge/okf``;
    #: ``"knowledge_root"`` → the whole ``knowledge/`` dir.  Opt-in (does NOT follow
    #: the master) so existing OKF users' search surface never changes silently.
    default_scope: str = Field(default=_DEFAULT_SCOPE, alias="defaultScope")

    #: Tunables.
    max_records: int = Field(
        default=_DEFAULT_MAX_RECORDS,
        ge=_MIN_MAX_RECORDS,
        le=_MAX_MAX_RECORDS,
        alias="maxRecords",
    )
    max_preview_chars: int = Field(
        default=_DEFAULT_MAX_PREVIEW_CHARS,
        ge=_MIN_MAX_PREVIEW_CHARS,
        le=_MAX_MAX_PREVIEW_CHARS,
        alias="maxPreviewChars",
    )
    max_docs: int = Field(default=_DEFAULT_MAX_DOCS, ge=1, alias="maxDocs")
    max_total_bytes: int = Field(
        default=_DEFAULT_MAX_TOTAL_BYTES, ge=1, alias="maxTotalBytes"
    )

    #: Per-doc body byte cap (exposed for loader callers; not env-tunable in v1).
    max_doc_bytes: int = Field(default=MAX_DOC_BYTES, ge=1, alias="maxDocBytes")


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def resolve_okf_config(
    *,
    env: Mapping[str, str] | None = None,
    config: Mapping[str, object] | None = None,
) -> OkfConfig:
    """Resolve the single :class:`OkfConfig` for this process.

    ``env`` and ``config`` are injectable for testing; they default to the real
    process environment and an empty config table.

    Precedence for every sub-flag / tunable:
    explicit env / config override > master default > hardcoded default.
    """
    env = os.environ if env is None else env
    table = _okf_table(config)

    master = _resolve_bool(
        env, table, env_var=MASTER_ENV_VAR, config_key="enabled", default=False
    )

    def sub_flag(env_var: str, config_key: str, *, master_default: bool) -> bool:
        override = _override_bool(env, table, env_var=env_var, config_key=config_key)
        if override is not None:
            return override
        return master_default

    return OkfConfig(
        masterEnabled=master,
        # lookup follows the master.
        lookupEnabled=sub_flag(
            LOOKUP_ENABLED_ENV_VAR, "lookup_enabled", master_default=master
        ),
        # index-inject is opt-in even under master-on (mutates the prompt).
        indexInjectEnabled=sub_flag(
            INDEX_INJECT_ENABLED_ENV_VAR, "index_inject_enabled", master_default=False
        ),
        # auto_type is a capability that follows the master (rescues untyped docs).
        autoType=sub_flag(
            AUTO_TYPE_ENV_VAR, "auto_type", master_default=master
        ),
        # scope is opt-in: a non-bool string that does NOT follow the master.
        defaultScope=_resolve_scope(env, table),
        bundlePaths=_resolve_bundle_paths(env, table),
        maxRecords=_resolve_int(
            env, table, env_var=MAX_RECORDS_ENV_VAR, config_key="max_records",
            default=_DEFAULT_MAX_RECORDS,
            minimum=_MIN_MAX_RECORDS, maximum=_MAX_MAX_RECORDS,
        ),
        maxPreviewChars=_resolve_int(
            env, table, env_var=MAX_PREVIEW_CHARS_ENV_VAR,
            config_key="max_preview_chars",
            default=_DEFAULT_MAX_PREVIEW_CHARS,
            minimum=_MIN_MAX_PREVIEW_CHARS, maximum=_MAX_MAX_PREVIEW_CHARS,
        ),
        maxDocs=_resolve_int(
            env, table, env_var=MAX_DOCS_ENV_VAR, config_key="max_docs",
            default=_DEFAULT_MAX_DOCS, minimum=1,
        ),
        maxTotalBytes=_resolve_int(
            env, table, env_var=MAX_TOTAL_BYTES_ENV_VAR, config_key="max_total_bytes",
            default=_DEFAULT_MAX_TOTAL_BYTES, minimum=1,
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _okf_table(config: Mapping[str, object] | None) -> Mapping[str, object]:
    if not isinstance(config, Mapping):
        return {}
    section = config.get(CONFIG_TABLE)
    return section if isinstance(section, Mapping) else {}


#: N-36 dedup: the resolution trio now lives in the ``config._bool_resolution``
#: leaf. ``coerce_bool`` is re-exported under its public name; the private
#: ``_override_bool`` / ``_resolve_bool`` aliases keep the in-module call sites
#: unchanged. The leaf is config-owned, so the "nothing from magi_agent.memory"
#: trust invariant stays intact.
_override_bool = override_bool
_resolve_bool = resolve_bool


def _resolve_int(
    env: Mapping[str, str],
    table: Mapping[str, object],
    *,
    env_var: str,
    config_key: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    # A malformed or out-of-range value clamps to the default rather than raising
    # a pydantic ValidationError out of the resolver (an operator typo must not
    # crash callers that resolve config inline).
    def _checked(value: int) -> int:
        if value < minimum:
            return default
        if maximum is not None and value > maximum:
            return default
        return value

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


def _resolve_scope(
    env: Mapping[str, str],
    table: Mapping[str, object],
) -> str:
    """Resolve ``default_scope`` as an opt-in string (env beats config).

    Unlike the bool sub-flags this does NOT cascade off the master: widening the
    scope is a deliberate opt-in so an existing OKF user's search surface never
    changes silently.  An unknown/garbage value falls back to the default rather
    than raising (an operator typo must not crash callers).
    """
    raw = env.get(SCOPE_ENV_VAR)
    if raw is None or not str(raw).strip():
        candidate = table.get("default_scope")
        raw = candidate if isinstance(candidate, str) else None
    if raw is None:
        return _DEFAULT_SCOPE
    value = str(raw).strip().lower()
    return value if value in _ALLOWED_SCOPES else _DEFAULT_SCOPE


def _resolve_bundle_paths(
    env: Mapping[str, str],
    table: Mapping[str, object],
) -> tuple[str, ...]:
    raw = env.get(BUNDLE_PATHS_ENV_VAR)
    if raw is None or not str(raw).strip():
        candidate = table.get("bundle_paths")
        if isinstance(candidate, (list, tuple)):
            return tuple(str(p).strip() for p in candidate if str(p).strip())
        raw = candidate if isinstance(candidate, str) else None
    if raw is None:
        return ()
    return tuple(segment.strip() for segment in str(raw).split(":") if segment.strip())


__all__ = [
    "AUTO_TYPE_ENV_VAR",
    "BUNDLE_PATHS_ENV_VAR",
    "CONFIG_TABLE",
    "INDEX_INJECT_ENABLED_ENV_VAR",
    "LOOKUP_ENABLED_ENV_VAR",
    "MASTER_ENV_VAR",
    "MAX_DOC_BYTES",
    "SCOPE_ENV_VAR",
    "OkfConfig",
    "coerce_bool",
    "resolve_okf_config",
]
