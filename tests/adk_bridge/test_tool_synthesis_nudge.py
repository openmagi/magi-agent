"""Tests for the Live-SWE per-step tool-synthesis reflection nudge plugin.

Covers ``magi_agent.adk_bridge.tool_synthesis_nudge`` and its control-plane
wiring in ``build_default_plane``:

- ``after_tool_callback`` appends the static nudge to mapping-shaped tool
  results (the ADK function-response dict the model sees next turn).
- It NEVER fires on: non-mapping results, results already carrying the nudge,
  synthetic injected responses from other plugins (``response_type`` marker),
  or truncated/oversized observations (mirrors Live-SWE: no nudge on elided
  output).
- ``build_tool_synthesis_nudge_plugin(enabled=False)`` returns ``None``.
- ``build_default_plane``: the nudge control is registered ONLY when the flag
  is ON, a model label is provided, AND the model resolves to a frontier tier.
  With the flag OFF (default) the plane is byte-identical to before.
"""

from __future__ import annotations

import asyncio

from magi_agent.adk_bridge.control_plane import build_default_plane
from magi_agent.adk_bridge.tool_synthesis_nudge import (
    TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME,
    TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY,
    MagiToolSynthesisNudgePlugin,
    build_tool_synthesis_nudge_plugin,
)
from magi_agent.runtime.tool_synthesis import TOOL_SYNTHESIS_NUDGE_TEXT

_FRONTIER_LABEL = "anthropic/claude-sonnet-4-6"
_CHEAP_LABEL = "anthropic/haiku"
_FLAG = "MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED"


def _run(coro):
    return asyncio.run(coro)


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


def _after(plugin: MagiToolSynthesisNudgePlugin, result: object):
    return _run(
        plugin.after_tool_callback(
            tool=_FakeTool("Bash"),
            tool_args={"command": "ls"},
            tool_context=None,
            result=result,
        )
    )


class TestPluginAppendsNudge:
    def test_appends_nudge_to_mapping_result(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        result = _after(plugin, {"status": "ok", "output": "hello"})
        assert result is not None
        assert result[TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY] == TOOL_SYNTHESIS_NUDGE_TEXT

    def test_preserves_original_result_fields(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        result = _after(plugin, {"status": "ok", "output": "hello", "durationMs": 3})
        assert result["status"] == "ok"
        assert result["output"] == "hello"
        assert result["durationMs"] == 3

    def test_does_not_mutate_original_dict(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        original = {"status": "ok", "output": "hello"}
        _after(plugin, original)
        assert TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY not in original


class TestPluginSkips:
    def test_skips_non_mapping_result(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        assert _after(plugin, "plain text") is None
        assert _after(plugin, None) is None

    def test_skips_result_already_carrying_nudge(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        result = {"status": "ok", TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY: "x"}
        assert _after(plugin, result) is None

    def test_skips_synthetic_injected_responses(self) -> None:
        # Other plugins (edit-retry reflection) replace the tool response with a
        # synthetic dict marked by ``response_type``; never stack a nudge on it.
        plugin = MagiToolSynthesisNudgePlugin()
        result = {
            "response_type": "MAGI_EDIT_RETRY_REFLECTION",
            "reflection_guidance": "fix the edit",
        }
        assert _after(plugin, result) is None

    def test_skips_top_level_truncated_result(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        assert _after(plugin, {"status": "ok", "truncated": True}) is None

    def test_skips_nested_truncated_output(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        result = {"status": "ok", "output": {"content": "abc", "truncated": True}}
        assert _after(plugin, result) is None

    def test_skips_bash_elision_marker_output(self) -> None:
        # gate5b Bash signals truncation with an inline head/tail elision marker.
        plugin = MagiToolSynthesisNudgePlugin()
        marker = (
            "head\n[... 4242 bytes elided - output truncated; re-run with "
            "head/tail/grep filters to see the elided region ...]\ntail"
        )
        assert _after(plugin, {"status": "ok", "output": marker}) is None

    def test_untruncated_nested_output_still_nudged(self) -> None:
        plugin = MagiToolSynthesisNudgePlugin()
        result = _after(
            plugin, {"status": "ok", "output": {"content": "abc", "truncated": False}}
        )
        assert result is not None
        assert TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY in result


class TestBuilder:
    def test_builder_returns_none_when_disabled(self) -> None:
        assert build_tool_synthesis_nudge_plugin(enabled=False) is None

    def test_builder_returns_plugin_when_enabled(self) -> None:
        plugin = build_tool_synthesis_nudge_plugin(enabled=True)
        assert isinstance(plugin, MagiToolSynthesisNudgePlugin)
        assert plugin.name == TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME


def _control_names(plane) -> list[str]:
    return [getattr(ctrl, "name", "") for ctrl in plane._controls]


class TestPlaneWiring:
    def test_flag_off_no_control_registered(self) -> None:
        plane = build_default_plane(
            os_environ={}, tool_synthesis_model_label=_FRONTIER_LABEL
        )
        assert TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME not in _control_names(plane)

    def test_flag_off_plane_identical_to_no_label(self) -> None:
        # Hard requirement: flag OFF (default) == ZERO behavior change.
        without_label = build_default_plane(os_environ={})
        with_label = build_default_plane(
            os_environ={}, tool_synthesis_model_label=_FRONTIER_LABEL
        )
        assert _control_names(with_label) == _control_names(without_label)

    def test_flag_on_frontier_registers_control(self) -> None:
        plane = build_default_plane(
            os_environ={_FLAG: "1"}, tool_synthesis_model_label=_FRONTIER_LABEL
        )
        assert TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME in _control_names(plane)

    def test_flag_on_cheap_tier_does_not_register(self) -> None:
        plane = build_default_plane(
            os_environ={_FLAG: "1"}, tool_synthesis_model_label=_CHEAP_LABEL
        )
        assert TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME not in _control_names(plane)

    def test_flag_on_without_label_does_not_register(self) -> None:
        plane = build_default_plane(os_environ={_FLAG: "1"})
        assert TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME not in _control_names(plane)

    def test_nudge_control_registered_last(self) -> None:
        # Edit-retry / resilience overrides must win the plane's
        # first-non-None-wins after-tool fan-out; the nudge goes LAST.
        plane = build_default_plane(
            os_environ={
                _FLAG: "1",
                "MAGI_EDIT_RETRY_REFLECTION_ENABLED": "1",
            },
            tool_synthesis_model_label=_FRONTIER_LABEL,
        )
        names = _control_names(plane)
        assert names[-1] == TOOL_SYNTHESIS_NUDGE_PLUGIN_NAME

    def test_registered_control_appends_nudge_via_plane(self) -> None:
        plane = build_default_plane(
            os_environ={_FLAG: "1"}, tool_synthesis_model_label=_FRONTIER_LABEL
        )
        override = _run(
            plane._after_tool(
                tool=_FakeTool("Bash"),
                args={"command": "ls"},
                tool_context=None,
                result={"status": "ok", "output": "hello"},
            )
        )
        assert override is not None
        assert override[TOOL_SYNTHESIS_NUDGE_RESPONSE_KEY] == TOOL_SYNTHESIS_NUDGE_TEXT
