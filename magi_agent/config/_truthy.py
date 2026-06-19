"""Dependency-free leaf for the canonical truthy convention + profile defaults.

This module is the I-3 fix: before it existed, ``config/flags.py`` eagerly
imported ``_is_true`` / ``_runtime_feature_enabled`` from ``config/env.py`` and
``config/env.py`` had to *defer* ``from .flags import …`` inside ~13 function
bodies to dodge the resulting managed import cycle. Promoting the shared
truthy primitives into this stdlib-only leaf lets both modules import them
one-directionally and breaks the cycle structurally.

Stability contract
------------------
* This module imports **only** the standard library. It must never import from
  any other ``magi_agent`` subpackage (not even ``magi_agent.config``). The
  meta-test ``tests/test_config_import_acyclic.py`` enforces this via AST scan
  so a regression cannot land silently.
* The constants and helpers below are byte-identical to the historic private
  helpers that lived at ``config/env.py:61-64`` and ``config/env.py:3263-3310``.
  ``config/env.py`` keeps the historic ``_TRUE_VALUES`` / ``_FALSE_VALUES`` /
  ``_SAFE_RUNTIME_PROFILES`` / ``_is_true`` / ``_runtime_feature_enabled`` /
  ``_runtime_profile_default_enabled`` / ``_env_bool_default_true`` names as
  re-export aliases so ~88 internal call sites stay unchanged.

The I-2 (one ``env_bool()`` everywhere) and I-1 (finish the flags.py
migration) follow-up PRs build on this leaf without touching it again.
"""

from __future__ import annotations

from collections.abc import Mapping

__all__ = [
    "TRUE_VALUES",
    "FALSE_VALUES",
    "RUNTIME_PROFILE_ENV",
    "SAFE_RUNTIME_PROFILES",
    "is_true",
    "env_bool",
    "env_bool_default_true",
    "runtime_profile_default_enabled",
    "runtime_feature_enabled",
]


# Canonical truthy convention. Matches the historic env.py sets verbatim;
# ``""`` (empty string) is deliberately in the FALSE set so an explicit empty
# env value reads as off rather than unknown.
TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES: frozenset[str] = frozenset({"0", "false", "no", "off", ""})

# The runtime-profile env var name. Pinned by the public ``docs/env-reference.md``
# contract; safe profiles flip profile-aware ``default-ON`` flags to off.
RUNTIME_PROFILE_ENV: str = "MAGI_RUNTIME_PROFILE"
SAFE_RUNTIME_PROFILES: frozenset[str] = frozenset(
    {"safe", "off", "minimal", "conservative", "eval"}
)


def is_true(value: str | None) -> bool:
    """Strict-truthy parse — case-insensitive, whitespace-trimmed.

    Returns ``True`` iff the (trimmed, lower-cased) value is in
    :data:`TRUE_VALUES`. Every other input — including ``None``, empty string,
    unknown words, and explicit falsey values — returns ``False``. This is the
    historic ``env._is_true`` body, moved verbatim.
    """

    return (value or "").strip().lower() in TRUE_VALUES


def env_bool(
    env: Mapping[str, str],
    name: str,
    *,
    default: bool = False,
) -> bool:
    """Single allowlist-semantics env reader.

    * Unset (``name`` not in ``env``) → ``default``.
    * Any other value → :func:`is_true` (so unknown / falsey explicit values
      read as ``False`` even when ``default=True``; that mirrors the
      historic ``_is_true(source.get(NAME))`` call pattern).

    This is the helper I-2 promotes to "the one truthy helper" for the
    flag-readers sweep. The strict-truthy semantics intentionally do *not*
    consult :data:`RUNTIME_PROFILE_ENV` — see :func:`runtime_feature_enabled`
    for the profile-aware default-ON shape.
    """

    raw = env.get(name)
    if raw is None:
        return default
    return is_true(raw)


def runtime_profile_default_enabled(env: Mapping[str, str]) -> bool:
    """Resolve the profile-default for profile-aware default-ON flags.

    Returns ``True`` for the full runtime profile (env var unset or set to any
    non-safe value) and ``False`` when :data:`RUNTIME_PROFILE_ENV` is one of
    :data:`SAFE_RUNTIME_PROFILES` (``safe`` / ``off`` / ``minimal`` /
    ``conservative`` / ``eval``). Trimmed + case-folded to match
    ``env.py``'s historic behavior.
    """

    profile = (env.get(RUNTIME_PROFILE_ENV) or "").strip().lower()
    return profile not in SAFE_RUNTIME_PROFILES


def runtime_feature_enabled(env: Mapping[str, str], name: str) -> bool:
    """Profile-aware default-ON flag reader.

    Resolution order:

    1. If the flag is explicitly set to a value in :data:`TRUE_VALUES` → ``True``.
    2. If the flag is explicitly set to a value in :data:`FALSE_VALUES`
       (including ``""``) → ``False``.
    3. Otherwise (unset, or unrecognised value) → :func:`runtime_profile_default_enabled`.

    This is what ``flags.flag_profile_bool`` delegates to so there is exactly
    one source of truth for the profile-default-ON resolution.
    """

    value = env.get(name)
    if value is None:
        return runtime_profile_default_enabled(env)
    normalized = value.strip().lower()
    if normalized in FALSE_VALUES:
        return False
    if normalized in TRUE_VALUES:
        return True
    return runtime_profile_default_enabled(env)


def env_bool_default_true(value: str | None) -> bool:
    """Default-ON parse used by ``native_receipts_honest`` and peers.

    * ``None`` (unset) → ``True``.
    * Value in :data:`FALSE_VALUES` (including ``""``) → ``False``.
    * Anything else (truthy, or unrecognised) → ``True``.

    This is the historic ``env._env_bool_default_true`` body, moved verbatim.
    """

    if value is None:
        return True
    normalized = (value or "").strip().lower()
    if normalized in FALSE_VALUES:
        return False
    return True
