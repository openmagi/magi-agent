from __future__ import annotations

from typing import Any


def apply_tool_overrides(runtime: Any, overrides: dict[str, Any]) -> None:
    """Apply persisted tool enable/disable overrides to the live tool registry.

    Defensive: unknown tools are skipped, never raises on bad input.
    """
    registry = getattr(runtime, "tool_registry", None)
    if registry is None:
        return
    tools = (overrides or {}).get("tools", {})
    if not isinstance(tools, dict):
        return
    for name, enabled in tools.items():
        try:
            if registry.resolve_registration(name) is None:
                continue
            if enabled:
                registry.enable(name)
            else:
                registry.disable(name)
        except Exception:
            continue
