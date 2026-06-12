"""Tests for the multi-file cross-reference robustness lever.

Lever: after ArchiveExtract, exhaustively enumerate ALL extracted files, read
structured data (XLSX/XML) in full, and perform the cross-file join/dedup
PROGRAMMATICALLY via Bash rather than by eye.

Default-OFF behind ``MAGI_MULTI_FILE_JOIN_ENABLED`` so behavior is byte-identical
when unset. The SAME domain-neutral block is emitted on both the production CLI
path (build_cli_instruction) and the GAIA bench path (run_gaia_question) so the
A/B plan measures the lever the flag actually exercises.

Run with:
    MAGI_CONFIG=$(mktemp) uv run --extra dev pytest tests/test_multi_file_join.py -q
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 1. env parser — strict default-OFF
# ---------------------------------------------------------------------------

from magi_agent.config.env import multi_file_join_enabled


def test_multi_file_join_parser_default_off() -> None:
    assert multi_file_join_enabled({}) is False
    assert multi_file_join_enabled({"MAGI_MULTI_FILE_JOIN_ENABLED": "0"}) is False
    assert multi_file_join_enabled({"MAGI_MULTI_FILE_JOIN_ENABLED": "false"}) is False
    assert multi_file_join_enabled({"MAGI_MULTI_FILE_JOIN_ENABLED": ""}) is False


def test_multi_file_join_parser_explicit_on() -> None:
    assert multi_file_join_enabled({"MAGI_MULTI_FILE_JOIN_ENABLED": "1"}) is True
    assert multi_file_join_enabled({"MAGI_MULTI_FILE_JOIN_ENABLED": "true"}) is True
    assert multi_file_join_enabled({"MAGI_MULTI_FILE_JOIN_ENABLED": "on"}) is True


def test_multi_file_join_parser_does_not_follow_runtime_profile() -> None:
    # Unlike file_tools_enabled, this is a strict opt-in gate (like
    # parse_eval_autonomy_enabled): a non-safe runtime profile must NOT flip it
    # on. Only explicit truthy values enable it.
    assert multi_file_join_enabled({"MAGI_RUNTIME_PROFILE": "eval"}) is False
    assert multi_file_join_enabled({"MAGI_RUNTIME_PROFILE": "full"}) is False


# ---------------------------------------------------------------------------
# 2. shared block builder — domain-neutral, gated
# ---------------------------------------------------------------------------

from magi_agent.cli.tool_runtime import multi_file_join_block


def test_block_empty_when_disabled() -> None:
    assert multi_file_join_block({}) == ""
    assert multi_file_join_block({"MAGI_MULTI_FILE_JOIN_ENABLED": "0"}) == ""


def test_block_content_when_enabled() -> None:
    text = multi_file_join_block({"MAGI_MULTI_FILE_JOIN_ENABLED": "1"})
    assert text
    lower = text.lower()
    # Exhaustive enumeration of ALL extracted files after archive extraction.
    assert "archiveextract" in lower
    assert "enumerate" in lower and "all" in lower
    # Read structured data (XLSX/XML) in full.
    assert "xlsx" in lower and "xml" in lower
    # Cross-file join/dedup performed PROGRAMMATICALLY via Bash, not by eye.
    assert "bash" in lower
    assert "programmatic" in lower
    assert "by eye" in lower or "eyeball" in lower
    # Wrapped in an identifiable tag.
    assert "<multi_file_join>" in text and "</multi_file_join>" in text


def test_block_is_domain_neutral_no_gaia_overfit() -> None:
    """Anti-overfit: the first-party block must NOT name a benchmark or any
    benchmark-specific entity. It is general multi-file-reasoning hygiene."""
    text = multi_file_join_block({"MAGI_MULTI_FILE_JOIN_ENABLED": "1"}).lower()
    for forbidden in ("gaia", "sweets", "soups", "stews", "final answer:"):
        assert forbidden not in text


# ---------------------------------------------------------------------------
# 3. build_cli_instruction wiring (production path)
# ---------------------------------------------------------------------------

import os
from unittest import mock


def _build_instruction(tmp_path) -> str:
    from magi_agent.cli.tool_runtime import build_cli_instruction

    return build_cli_instruction(
        session_id="s1",
        workspace_root=str(tmp_path),
    )


# build_system_prompt embeds a wall-clock timestamp, so two independent
# build_cli_instruction calls are NOT byte-identical to each other. To assert
# the full-string semantics required by the review without that nondeterminism,
# we hold the timestamp source fixed by patching message_builder's clock, then
# compare the two full strings directly.


def _build_instruction_fixed_clock(tmp_path, env: dict[str, str]) -> str:
    from datetime import datetime, timezone

    fixed = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: ANN001
            return fixed if tz is None else fixed.astimezone(tz)

    import magi_agent.runtime.message_builder as mb

    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(mb, "datetime", _FixedDatetime):
            return _build_instruction(tmp_path)


def test_build_cli_instruction_byte_identical_when_off(tmp_path) -> None:
    off = _build_instruction_fixed_clock(tmp_path, {"MAGI_MULTI_FILE_JOIN_ENABLED": "0"})
    unset = _build_instruction_fixed_clock(tmp_path, {})
    assert "<multi_file_join>" not in off
    # Full-string equality, not just substring absence (per review).
    assert off == unset


def test_build_cli_instruction_appends_block_when_on(tmp_path) -> None:
    off = _build_instruction_fixed_clock(tmp_path, {})
    on = _build_instruction_fixed_clock(tmp_path, {"MAGI_MULTI_FILE_JOIN_ENABLED": "1"})
    assert "<multi_file_join>" in on
    # The ON string is exactly the OFF string plus the appended block.
    appended = multi_file_join_block({"MAGI_MULTI_FILE_JOIN_ENABLED": "1"})
    assert on == off + "\n\n" + appended
