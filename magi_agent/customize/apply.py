from __future__ import annotations

from typing import Any

from magi_agent.config.flags import flag_profile_bool


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


def apply_verification_overrides(runtime: Any, overrides: dict[str, Any] | None) -> None:
    """Translate persisted verification overrides into a runtime policy object.

    Sets ``runtime.customize_verification_policy`` so the enforcement wiring
    (Phases 2-4) can read which preset gates to contribute. No-op (sets nothing)
    unless ``MAGI_CUSTOMIZE_VERIFICATION_ENABLED`` is on. Never raises.
    """
    if not flag_profile_bool("MAGI_CUSTOMIZE_VERIFICATION_ENABLED"):
        return
    from magi_agent.customize.verification_policy import CustomizeVerificationPolicy

    try:
        runtime.customize_verification_policy = CustomizeVerificationPolicy.from_overrides(
            overrides or {}
        )
    except Exception:
        return
