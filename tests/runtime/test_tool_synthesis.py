"""Tests for the Live-SWE-style tool-synthesis resolution + recipe block.

Covers ``magi_agent.runtime.tool_synthesis``:

- ``MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED`` is default OFF (strict truthy opt-in).
- Tier gating: even with the flag ON, only frontier-tier models
  (``sota`` / ``reasoning`` in the ``ModelTierRegistry``) activate the feature;
  cheap-tier and unknown models stay inactive (fail-closed).
- ``build_tool_synthesis_instruction_block`` returns "" when inactive and the
  "creating your own tools" block when active. The block steers toward
  ``.magi/tools/`` helpers and away from building edit tools.

Pure functions — no network, no model, env passed explicitly.
"""

from __future__ import annotations

from magi_agent.runtime.tool_synthesis import (
    TOOL_SYNTHESIS_NUDGE_TEXT,
    build_tool_synthesis_instruction_block,
    tool_synthesis_nudge_active,
)

_FRONTIER_LABEL = "anthropic/claude-sonnet-4-6"
_CHEAP_LABEL = "anthropic/haiku"
_ON = {"MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED": "1"}


class TestNudgeActiveResolution:
    def test_default_off_even_for_frontier_model(self) -> None:
        assert tool_synthesis_nudge_active(model_label=_FRONTIER_LABEL, env={}) is False

    def test_flag_on_frontier_tier_is_active(self) -> None:
        assert tool_synthesis_nudge_active(model_label=_FRONTIER_LABEL, env=_ON) is True

    def test_flag_on_cheap_tier_is_inactive(self) -> None:
        assert tool_synthesis_nudge_active(model_label=_CHEAP_LABEL, env=_ON) is False

    def test_flag_on_unknown_model_fails_closed(self) -> None:
        assert (
            tool_synthesis_nudge_active(
                model_label="anthropic/claude-omega-99", env=_ON
            )
            is False
        )

    def test_flag_on_openai_sota_is_active(self) -> None:
        assert tool_synthesis_nudge_active(model_label="openai/gpt-5.5", env=_ON) is True

    def test_flag_on_empty_label_fails_closed(self) -> None:
        assert tool_synthesis_nudge_active(model_label="", env=_ON) is False

    def test_flag_on_malformed_label_fails_closed(self) -> None:
        assert (
            tool_synthesis_nudge_active(model_label="not a model label!!", env=_ON)
            is False
        )

    def test_falsy_flag_values_stay_off(self) -> None:
        for value in ("0", "false", "no", "off", ""):
            env = {"MAGI_TOOL_SYNTHESIS_NUDGE_ENABLED": value}
            assert (
                tool_synthesis_nudge_active(model_label=_FRONTIER_LABEL, env=env)
                is False
            ), f"value {value!r} must not enable the nudge"


class TestNudgeText:
    def test_nudge_mentions_tools_dir(self) -> None:
        assert ".magi/tools/" in TOOL_SYNTHESIS_NUDGE_TEXT

    def test_nudge_is_short(self) -> None:
        # Per-step text is appended to EVERY tool observation; keep it lean.
        assert len(TOOL_SYNTHESIS_NUDGE_TEXT) < 600


class TestInstructionBlock:
    def test_block_empty_when_flag_off(self) -> None:
        assert (
            build_tool_synthesis_instruction_block(model_label=_FRONTIER_LABEL, env={})
            == ""
        )

    def test_block_empty_when_cheap_tier(self) -> None:
        assert (
            build_tool_synthesis_instruction_block(model_label=_CHEAP_LABEL, env=_ON)
            == ""
        )

    def test_block_present_when_on_and_frontier(self) -> None:
        block = build_tool_synthesis_instruction_block(
            model_label=_FRONTIER_LABEL, env=_ON
        )
        assert "<creating_your_own_tools>" in block
        assert "</creating_your_own_tools>" in block
        assert ".magi/tools/" in block

    def test_block_steers_away_from_edit_tools(self) -> None:
        block = build_tool_synthesis_instruction_block(
            model_label=_FRONTIER_LABEL, env=_ON
        )
        # Must NOT direct the model to build an edit tool — magi already ships
        # a native edit cascade (FileEdit/PatchApply).
        assert "FileEdit" in block
        assert "PatchApply" in block

    def test_block_keeps_scripts_out_of_target_repo(self) -> None:
        block = build_tool_synthesis_instruction_block(
            model_label=_FRONTIER_LABEL, env=_ON
        )
        lowered = block.lower()
        assert "never" in lowered
        assert "working tree" in lowered or "repository" in lowered
