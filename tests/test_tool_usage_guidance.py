"""D1 — per-tool usage-guidance registry + apply function (default OFF)."""
from __future__ import annotations

from magi_agent.gates.tool_usage_guidance import (
    TOOL_USAGE_GUIDANCE,
    apply_usage_guidance,
)

_ON = {"MAGI_TOOL_USAGE_GUIDANCE_ENABLED": "1"}
# Promoted _pb: unset now resolves ON, so the disabled path is an explicit "0".
_OFF = {"MAGI_TOOL_USAGE_GUIDANCE_ENABLED": "0"}


def test_flag_off_returns_description_unchanged() -> None:
    desc = "Gate 5B selected full toolhost WebSearch tool."
    assert apply_usage_guidance("WebSearch", desc, _OFF) == desc
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


def test_spawn_agent_lists_registry_routes_when_on() -> None:
    desc = "Gate 5B selected full toolhost SpawnAgent tool."
    result = apply_usage_guidance("SpawnAgent", desc, _ON)
    assert "Available model routes" in result
    # A built-in registry SOTA route the child can target.
    assert "anthropic:claude-sonnet-4-6" in result
    assert "provider" in result and "model" in result


def test_spawn_agent_includes_operator_allowlist_routes() -> None:
    from magi_agent.config.env import _ALLOWED_MODEL_ROUTES_ENV

    env = {
        "MAGI_TOOL_USAGE_GUIDANCE_ENABLED": "1",
        _ALLOWED_MODEL_ROUTES_ENV: "anthropic:claude-opus-4-8",
    }
    result = apply_usage_guidance("SpawnAgent", "x", env)
    # An operator-vetted route absent from the built-in registry is surfaced.
    assert "anthropic:claude-opus-4-8" in result


def test_spawn_agent_routes_absent_when_flag_off() -> None:
    desc = "Gate 5B selected full toolhost SpawnAgent tool."
    assert apply_usage_guidance("SpawnAgent", desc, _OFF) == desc


def test_apply_is_fail_open(monkeypatch) -> None:
    import magi_agent.gates.tool_usage_guidance as mod

    def boom(_env=None):  # noqa: ANN001
        raise RuntimeError("synthetic")

    monkeypatch.setattr(
        "magi_agent.config.env.is_tool_usage_guidance_enabled", boom
    )
    desc = "original"
    assert mod.apply_usage_guidance("WebSearch", desc, _ON) == desc


def _build_named_tool(name: str):
    from magi_agent.gates.gate5b_full_toolhost import _function_tool

    async def invoke(tool_context: object | None = None) -> dict[str, object]:
        return {}

    return _function_tool(name, invoke)


def test_function_tool_flag_off_docstring_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TOOL_USAGE_GUIDANCE_ENABLED", "0")
    tool = _build_named_tool("WebSearch")
    assert tool.func.__doc__ == "Gate 5B selected full toolhost WebSearch tool."


def test_function_tool_flag_on_appends_guidance(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TOOL_USAGE_GUIDANCE_ENABLED", "1")
    tool = _build_named_tool("WebSearch")
    doc = tool.func.__doc__ or ""
    assert doc.startswith("Gate 5B selected full toolhost WebSearch tool.")
    assert "Do NOT" in doc


def test_function_tool_flag_on_unregistered_unchanged(monkeypatch) -> None:
    monkeypatch.setenv("MAGI_TOOL_USAGE_GUIDANCE_ENABLED", "1")
    tool = _build_named_tool("Clock")
    assert tool.func.__doc__ == "Gate 5B selected full toolhost Clock tool."
