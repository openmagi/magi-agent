"""D1 — per-tool usage-guidance registry + apply function (default OFF)."""
from __future__ import annotations

from magi_agent.gates.tool_usage_guidance import (
    TOOL_USAGE_GUIDANCE,
    apply_usage_guidance,
)

_ON = {"MAGI_TOOL_USAGE_GUIDANCE_ENABLED": "1"}


def test_flag_off_returns_description_unchanged() -> None:
    desc = "Gate 5B selected full toolhost WebSearch tool."
    assert apply_usage_guidance("WebSearch", desc, {}) == desc
    assert apply_usage_guidance(
        "WebSearch", desc, {"MAGI_TOOL_USAGE_GUIDANCE_ENABLED": "0"}
    ) == desc


def test_flag_on_appends_guidance_for_registered_tool() -> None:
    desc = "Gate 5B selected full toolhost WebSearch tool."
    result = apply_usage_guidance("WebSearch", desc, _ON)
    assert result.startswith(desc)
    assert "Do NOT" in result
    assert result != desc


def test_flag_on_unregistered_tool_unchanged() -> None:
    desc = "Gate 5B selected full toolhost Clock tool."
    assert apply_usage_guidance("Clock", desc, _ON) == desc


def test_registry_keys_are_canonical_gate5b_names() -> None:
    from magi_agent.gates.gate5b_full_toolhost import (
        GATE5B_FULL_TOOLHOST_TOOL_NAMES,
    )

    for name in TOOL_USAGE_GUIDANCE:
        assert name in GATE5B_FULL_TOOLHOST_TOOL_NAMES, name


def test_registry_entries_are_lean() -> None:
    for name, guidance in TOOL_USAGE_GUIDANCE.items():
        assert len(guidance) <= 600, f"{name} guidance exceeds 600 chars"
        assert "Do NOT" in guidance, f"{name} guidance lacks a negative rule"


def test_apply_is_fail_open(monkeypatch) -> None:
    import magi_agent.gates.tool_usage_guidance as mod

    def boom(_env=None):  # noqa: ANN001
        raise RuntimeError("synthetic")

    monkeypatch.setattr(
        "magi_agent.config.env.is_tool_usage_guidance_enabled", boom
    )
    desc = "original"
    assert mod.apply_usage_guidance("WebSearch", desc, _ON) == desc
