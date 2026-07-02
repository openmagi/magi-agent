"""N-36 leaf: single home for the env/config boolean resolution trio.

``coerce_bool`` / ``_override_bool`` / ``_resolve_bool`` used to be
byte-for-byte duplicated between ``magi_agent/memory/config.py`` and
``magi_agent/knowledge/okf/config.py`` (both re-declaring the
``_TRUE_VALUES`` / ``_FALSE_VALUES`` sets that already live in the canonical
``config/_truthy.py`` leaf). This leaf holds the one canonical trio; both
config modules import it and keep ``_override_bool`` / ``_resolve_bool``
back-compat aliases plus a ``coerce_bool`` re-export.

The value sets are imported from ``config/_truthy`` so the truthy convention
literal continues to live in exactly one place (the immutable ``_truthy``
leaf, guarded by ``tests/test_config_import_acyclic.py``).
"""

from __future__ import annotations

from collections.abc import Mapping

from magi_agent.config._truthy import FALSE_VALUES, TRUE_VALUES

__all__ = [
    "coerce_bool",
    "override_bool",
    "resolve_bool",
]


def coerce_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return True
    if normalized in FALSE_VALUES:
        return False
    return None


def override_bool(
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


def resolve_bool(
    env: Mapping[str, str],
    table: Mapping[str, object],
    *,
    env_var: str,
    config_key: str,
    default: bool,
) -> bool:
    override = override_bool(env, table, env_var=env_var, config_key=config_key)
    return default if override is None else override
