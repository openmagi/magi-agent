"""Tests for Fix C — system prompt mentions file/image tools + fallback strategy.

Checks:
- GAIA_SYSTEM_PROMPT mentions ImageUnderstand and DocumentRead.
- build_cli_instruction returns a prompt that mentions file tools.
- The fallback strategy (try Bash/Python on blocked result) is mentioned.
"""

from __future__ import annotations


class TestGaiaSystemPrompt:
    def test_gaia_system_prompt_mentions_image_understand(self) -> None:
        from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

        assert "ImageUnderstand" in GAIA_SYSTEM_PROMPT, (
            "GAIA_SYSTEM_PROMPT must mention ImageUnderstand tool; "
            f"current prompt: {GAIA_SYSTEM_PROMPT!r}"
        )

    def test_gaia_system_prompt_mentions_document_read(self) -> None:
        from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

        assert "DocumentRead" in GAIA_SYSTEM_PROMPT, (
            "GAIA_SYSTEM_PROMPT must mention DocumentRead tool; "
            f"current prompt: {GAIA_SYSTEM_PROMPT!r}"
        )

    def test_gaia_system_prompt_mentions_xlsx_read(self) -> None:
        from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

        assert "XLSXRead" in GAIA_SYSTEM_PROMPT, (
            "GAIA_SYSTEM_PROMPT must mention XLSXRead tool"
        )

    def test_gaia_system_prompt_fallback_when_tool_errors(self) -> None:
        from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

        prompt_lower = GAIA_SYSTEM_PROMPT.lower()
        has_fallback = (
            "error" in prompt_lower
            or "alternative" in prompt_lower
            or "bash" in prompt_lower
            or "python" in prompt_lower
        )
        assert has_fallback, (
            "GAIA_SYSTEM_PROMPT should mention fallback strategy when tools fail; "
            f"current prompt: {GAIA_SYSTEM_PROMPT!r}"
        )

    def test_gaia_system_prompt_final_answer_format_preserved(self) -> None:
        """The FINAL ANSWER: format must still be present after the edit."""
        from magi_agent.benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

        assert "FINAL ANSWER:" in GAIA_SYSTEM_PROMPT, (
            "GAIA_SYSTEM_PROMPT must still contain 'FINAL ANSWER:' marker"
        )


class TestBuildCliInstructionFileTools:
    def test_build_cli_instruction_mentions_image_understand(self) -> None:
        from magi_agent.cli.tool_runtime import build_cli_instruction

        instruction = build_cli_instruction(session_id="test-session")
        assert "ImageUnderstand" in instruction, (
            "build_cli_instruction must mention ImageUnderstand; "
            f"got: {instruction[:500]!r}"
        )

    def test_build_cli_instruction_mentions_document_read(self) -> None:
        from magi_agent.cli.tool_runtime import build_cli_instruction

        instruction = build_cli_instruction(session_id="test-session")
        assert "DocumentRead" in instruction, (
            "build_cli_instruction must mention DocumentRead"
        )

    def test_build_cli_instruction_mentions_fallback_strategy(self) -> None:
        from magi_agent.cli.tool_runtime import build_cli_instruction

        instruction = build_cli_instruction(session_id="test-session")
        instr_lower = instruction.lower()
        has_fallback = (
            "blocked" in instr_lower
            or "alternative" in instr_lower
            or "fallback" in instr_lower
        )
        assert has_fallback, (
            "build_cli_instruction should include fallback guidance for blocked tool results"
        )
