"""Regression tests for memory snapshot wiring into the system prompt.

Contract:
  1. build_system_prompt(..., memory_snapshot_block=block) includes the block
     text in the returned prompt.
  2. build_system_prompt(...) without memory_snapshot_block has no
     <memory-context in the returned prompt.
  3. build_cli_instruction() passes the memory snapshot block from
     MemorySnapshotCache into build_system_prompt when the gate is on.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from magi_agent.runtime.message_builder import build_system_prompt
from magi_agent.memory.prompt_projection import MEMORY_CONTEXT_OPEN

MEMORY_PROJECTION_ENV = "MAGI_MEMORY_PROJECTION_ENABLED"
_BLOCK = '<memory-context hidden="true">\n<!-- MEMORY.md -->\nRecall: Test user.\n</memory-context>'


# ---------------------------------------------------------------------------
# 1. build_system_prompt with block includes it
# ---------------------------------------------------------------------------


def test_build_system_prompt_includes_memory_snapshot_block() -> None:
    """Passing memory_snapshot_block= includes the block text in the prompt."""
    prompt = build_system_prompt(
        session_key="s1",
        turn_id="t1",
        memory_snapshot_block=_BLOCK,
    )
    assert _BLOCK in prompt


def test_build_system_prompt_includes_memory_context_tag() -> None:
    """The <memory-context> open tag appears in the prompt when block is set."""
    prompt = build_system_prompt(
        session_key="s1",
        turn_id="t1",
        memory_snapshot_block=_BLOCK,
    )
    assert MEMORY_CONTEXT_OPEN in prompt


# ---------------------------------------------------------------------------
# 2. build_system_prompt without block has no <memory-context
# ---------------------------------------------------------------------------


def test_build_system_prompt_without_block_has_no_memory_context() -> None:
    """Omitting memory_snapshot_block yields no <memory-context tag."""
    prompt = build_system_prompt(
        session_key="s1",
        turn_id="t1",
    )
    assert "<memory-context" not in prompt


# ---------------------------------------------------------------------------
# 3. build_cli_instruction wires cache into prompt
# ---------------------------------------------------------------------------


def test_build_cli_instruction_injects_memory_block_when_gate_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_cli_instruction() passes the cache block when gate is on."""
    (tmp_path / "MEMORY.md").write_text("# Memory\nImportant recall data.", encoding="utf-8")
    monkeypatch.setenv(MEMORY_PROJECTION_ENV, "1")

    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="cli-test-session",
        model="",
        workspace_root=str(tmp_path),
    )
    assert "<memory-context" in instruction
    assert "Important recall data" in instruction


def test_build_cli_instruction_no_memory_block_when_gate_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """build_cli_instruction() has no <memory-context when gate is off."""
    (tmp_path / "MEMORY.md").write_text("# Memory\nImportant recall data.", encoding="utf-8")
    monkeypatch.delenv(MEMORY_PROJECTION_ENV, raising=False)

    from magi_agent.cli.tool_runtime import build_cli_instruction

    instruction = build_cli_instruction(
        session_id="cli-test-session",
        model="",
        workspace_root=str(tmp_path),
    )
    assert "<memory-context" not in instruction
