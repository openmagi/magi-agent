"""Tests for the full_text_sink seam in RealLocalChildRunner (B1 — U3).

Tests the invariant:
- With sink=None → byte-identical envelope output as before this change.
- With sink callable → called with the untrimmed final text BEFORE
  _MAX_SUMMARY_CHARS truncation.

These tests are IMPORT-ONLY for run_child internals; they inject a fake
runner at construction time so no LLM/network is needed.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_LONG_TEXT = "A" * 5000  # Longer than _MAX_SUMMARY_CHARS (2000).


def _make_fake_turn_result(final_text: str) -> tuple[str, tuple[str, ...]]:
    """Return (final_text, evidence_refs) as _drive_one_turn would."""
    return final_text, ()


def _make_runner(
    *,
    final_text: str = "hello",
    full_text_sink: Any = None,
    env: Mapping[str, str] | None = None,
) -> Any:
    from magi_agent.runtime.child_runner_live import RealLocalChildRunner

    env = env or {"MAGI_CHILD_RUNNER_LIVE_ENABLED": "1"}
    return RealLocalChildRunner(
        full_text_sink=full_text_sink,
        env=env,
    )


class _FakeLlm:
    """Minimal fake LLM that returns a single streamed text token."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def run_async(self, *args: Any, **kwargs: Any) -> Any:
        # Return a fake run result with a final text
        return MagicMock(final_text=self._text)


# ---------------------------------------------------------------------------
# Test: sink=None → envelope summary is clamped to _MAX_SUMMARY_CHARS
# ---------------------------------------------------------------------------

class TestFullTextSinkNone:
    """With no sink, the envelope must be byte-identical to the pre-seam path."""

    def test_runner_accepts_no_sink_kwarg(self) -> None:
        """Constructor must accept full_text_sink=None without error."""
        from magi_agent.runtime.child_runner_live import RealLocalChildRunner

        runner = RealLocalChildRunner(full_text_sink=None)
        assert runner is not None

    def test_runner_accepts_no_sink_positional_omitted(self) -> None:
        """Constructor must work without full_text_sink argument at all."""
        from magi_agent.runtime.child_runner_live import RealLocalChildRunner

        runner = RealLocalChildRunner()
        assert runner is not None

    @pytest.mark.asyncio
    async def test_envelope_summary_is_truncated_to_max_chars_when_sink_none(self) -> None:
        """Envelope summary is clamped to _MAX_SUMMARY_CHARS when sink is None."""
        from magi_agent.runtime.child_runner_live import (
            RealLocalChildRunner,
            _MAX_SUMMARY_CHARS,
        )

        captured_summary: list[str] = []

        async def _fake_drive(config: Any, request: Any) -> tuple[str, tuple[str, ...]]:
            return _LONG_TEXT, ()

        runner = RealLocalChildRunner(full_text_sink=None)

        # Patch _resolve_provider_config so the test is hermetic and does not
        # depend on an ambient provider key (which varies across CI shards);
        # a truthy config lets run_child reach _drive_one_turn (patched below).
        with (
            patch.object(runner, "_resolve_provider_config", return_value=object()),
            patch.object(runner, "_drive_one_turn", side_effect=_fake_drive),
        ):
            # Build a minimal fake request
            request = MagicMock()
            request.task_id = "test-task-id"
            request.parentExecutionId = "test-parent"
            request.turnId = "test-turn"
            request.objective = "test objective"
            request.metadata = {}
            request.provider = None
            request.model = None
            request.budgetMs = 0
            request.spawnCap = None
            result = await runner.run_child(request)

        summary = result.get("summary", "")
        assert isinstance(summary, str)
        assert len(summary) <= _MAX_SUMMARY_CHARS, (
            f"summary length {len(summary)} exceeds _MAX_SUMMARY_CHARS={_MAX_SUMMARY_CHARS}"
        )
        # The full text is 5000 chars; truncated summary must be exactly _MAX_SUMMARY_CHARS
        assert len(summary) == _MAX_SUMMARY_CHARS


# ---------------------------------------------------------------------------
# Test: sink callable → receives untrimmed text
# ---------------------------------------------------------------------------

class TestFullTextSinkCallable:
    """With a sink, it must receive the untrimmed final text."""

    @pytest.mark.asyncio
    async def test_sink_receives_full_text_before_truncation(self) -> None:
        """Sink must be called with the untrimmed final_text (5000 chars), not 2000."""
        from magi_agent.runtime.child_runner_live import RealLocalChildRunner

        received: list[str] = []

        def sink(text: str) -> None:
            received.append(text)

        async def _fake_drive(config: Any, request: Any) -> tuple[str, tuple[str, ...]]:
            return _LONG_TEXT, ()

        runner = RealLocalChildRunner(full_text_sink=sink)

        with (
            patch.object(runner, "_resolve_provider_config", return_value=object()),
            patch.object(runner, "_drive_one_turn", side_effect=_fake_drive),
        ):
            request = MagicMock()
            request.task_id = "test-task-id"
            request.parentExecutionId = "test-parent"
            request.turnId = "test-turn"
            request.objective = "test objective"
            request.metadata = {}
            request.provider = None
            request.model = None
            request.budgetMs = 0
            request.spawnCap = None
            result = await runner.run_child(request)

        assert len(received) == 1, f"Expected sink called once, got {len(received)}"
        assert received[0] == _LONG_TEXT, (
            f"Sink received {len(received[0])} chars, expected {len(_LONG_TEXT)} "
            f"(full untrimmed text)"
        )

    @pytest.mark.asyncio
    async def test_sink_called_before_envelope_summary_is_trimmed(self) -> None:
        """Envelope summary is still trimmed even when sink is present."""
        from magi_agent.runtime.child_runner_live import (
            RealLocalChildRunner,
            _MAX_SUMMARY_CHARS,
        )

        received: list[str] = []

        def sink(text: str) -> None:
            received.append(text)

        async def _fake_drive(config: Any, request: Any) -> tuple[str, tuple[str, ...]]:
            return _LONG_TEXT, ()

        runner = RealLocalChildRunner(full_text_sink=sink)

        with (
            patch.object(runner, "_resolve_provider_config", return_value=object()),
            patch.object(runner, "_drive_one_turn", side_effect=_fake_drive),
        ):
            request = MagicMock()
            request.task_id = "test-task-id"
            request.parentExecutionId = "test-parent"
            request.turnId = "test-turn"
            request.objective = "test objective"
            request.metadata = {}
            request.provider = None
            request.model = None
            request.budgetMs = 0
            request.spawnCap = None
            result = await runner.run_child(request)

        # Sink gets full text
        assert received[0] == _LONG_TEXT
        # Envelope still trimmed
        summary = result.get("summary", "")
        assert len(summary) == _MAX_SUMMARY_CHARS

    @pytest.mark.asyncio
    async def test_sink_exception_does_not_propagate(self) -> None:
        """A raising sink must not crash run_child (never-raise contract)."""
        from magi_agent.runtime.child_runner_live import RealLocalChildRunner

        def bad_sink(text: str) -> None:
            raise RuntimeError("sink crash")

        async def _fake_drive(config: Any, request: Any) -> tuple[str, tuple[str, ...]]:
            return "some text", ()

        runner = RealLocalChildRunner(full_text_sink=bad_sink)

        with patch.object(runner, "_drive_one_turn", side_effect=_fake_drive):
            request = MagicMock()
            request.task_id = "test-task-id"
            request.parentExecutionId = "test-parent"
            request.turnId = "test-turn"
            request.objective = "test objective"
            request.metadata = {}
            request.provider = None
            request.model = None
            request.budgetMs = 0
            request.spawnCap = None
            # Must not raise
            result = await runner.run_child(request)

        # Result should still be ok (completed) since the error is in the sink
        # and run_child's outer try/except swallows it
        assert result is not None
