"""Default-OFF gate for the discovery orchestrator.

The discovery orchestrator is an opt-in harness. ``run_discovery`` calls
:func:`ensure_discovery_enabled` before doing any work; unless the
``MAGI_DISCOVERY_ENABLED`` environment flag is truthy, a
:class:`GateDisabledError` is raised. This mirrors the env-flag gating used
elsewhere in the codebase (e.g. ``MAGI_EDIT_FUZZY_MATCH_ENABLED``).
"""
from __future__ import annotations

import os
from collections.abc import Mapping

#: Environment flag controlling the discovery orchestrator.
DISCOVERY_ENABLED_ENV: str = "MAGI_DISCOVERY_ENABLED"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class GateDisabledError(RuntimeError):
    """Raised when the discovery orchestrator is invoked while the gate is OFF."""


def is_discovery_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return ``True`` when ``MAGI_DISCOVERY_ENABLED`` is set to a truthy value."""
    resolved = os.environ if env is None else env
    return resolved.get(DISCOVERY_ENABLED_ENV, "0").strip().lower() in _TRUE_VALUES


def ensure_discovery_enabled(env: Mapping[str, str] | None = None) -> None:
    """Raise :class:`GateDisabledError` unless the discovery gate is enabled."""
    if not is_discovery_enabled(env):
        raise GateDisabledError(
            f"discovery orchestrator is disabled; set {DISCOVERY_ENABLED_ENV}=1 "
            "to enable it (default-OFF)."
        )


__all__ = [
    "DISCOVERY_ENABLED_ENV",
    "GateDisabledError",
    "ensure_discovery_enabled",
    "is_discovery_enabled",
]
