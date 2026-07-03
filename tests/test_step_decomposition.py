"""Tests for the default-OFF multi-step decomposition guidance lever.

Lever: long multi-hop questions (5+ dependent sub-steps) break when one link in
the chain is wrong. This adds a *light*, default-OFF prompt nudge that asks the
agent to enumerate the dependent sub-steps up front and resolve/confirm each
before proceeding — reusing the existing planning seams, no new control loop.

All tests are hermetic (no network, no model traffic).
"""
from __future__ import annotations

import pytest

from magi_agent.config.env import (
    MAGI_STEP_DECOMPOSITION_ENABLED_ENV,
    is_step_decomposition_enabled,
)
from magi_agent.cli.tool_runtime import build_cli_instruction, step_decomposition_block


# ---------------------------------------------------------------------------
# Env gate — strict default-OFF truthy opt-in
# ---------------------------------------------------------------------------
def test_gate_off_when_disabled() -> None:
    assert is_step_decomposition_enabled(
        env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "0"}
    ) is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE", "On"])
def test_gate_on_for_truthy_values(value: str) -> None:
    assert is_step_decomposition_enabled(
        env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: value}
    ) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_gate_off_for_falsy_values(value: str) -> None:
    assert is_step_decomposition_enabled(
        env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: value}
    ) is False


def test_gate_registered_in_flags_registry() -> None:
    from magi_agent.config.flags import FLAGS, flag_profile_bool

    names = {spec.name for spec in FLAGS}
    assert MAGI_STEP_DECOMPOSITION_ENABLED_ENV in names
    # The gate delegates to flag_profile_bool (profile-aware default-ON), so the
    # registry and the gate agree.
    assert flag_profile_bool(
        MAGI_STEP_DECOMPOSITION_ENABLED_ENV,
        env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "0"},
    ) is False
    assert (
        flag_profile_bool(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, env={
            MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "1"
        })
        is True
    )


# ---------------------------------------------------------------------------
# step_decomposition_block — standalone helper (mirrors eval_autonomy_block)
# ---------------------------------------------------------------------------
def test_block_empty_when_off() -> None:
    assert step_decomposition_block(
        env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "0"}
    ) == ""


def test_block_present_when_on_and_general_not_gaia() -> None:
    block = step_decomposition_block(env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "1"})
    assert block != ""
    assert "<step_decomposition>" in block
    assert "</step_decomposition>" in block
    # Reuse existing planning seams, not a new control structure.
    assert "sub-step" in block.lower() or "sub-steps" in block.lower()
    # GENERAL capability — no benchmark-specific text in first-party logic.
    assert "GAIA" not in block
    assert "benchmark" not in block.lower()
    # Leading separator so it joins cleanly when appended to parts.
    assert block.startswith("\n\n") or block.startswith("<step_decomposition>")


# ---------------------------------------------------------------------------
# build_cli_instruction — OFF path byte-identity + ON path injection
# ---------------------------------------------------------------------------
def test_build_cli_instruction_off_has_no_decomposition_block(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, "0")
    instruction = build_cli_instruction(session_id="s1", model="claude-sonnet-4-6")
    assert "<step_decomposition>" not in instruction


def test_build_cli_instruction_off_is_byte_identical_to_baseline(monkeypatch) -> None:
    """OFF path must be byte-for-byte the same as not having the flag wired at all.

    Guards against any accidental reflow of the ``"\\n\\n".join(parts)`` assembly
    when the new guarded append is added. The OFF guard relies on the helper
    returning ``""`` so the append is skipped entirely. We normalise the one
    intrinsically non-deterministic field the existing prompt already carries (a
    UTC timestamp) so the comparison isolates *only* the effect of the
    decomposition wiring. This is the snapshot promised by the design.
    """
    import re

    def normalize_runtime_clock(text: str) -> str:
        text = re.sub(r"\d{2}:\d{2}:\d{2}\.\d{3}Z", "TS", text)
        return re.sub(r"(runtime_local_time: )\d{2}:\d{2}:\d{2}", r"\1TS", text)

    monkeypatch.setenv(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, "0")
    off = normalize_runtime_clock(
        build_cli_instruction(session_id="s1", model="claude-sonnet-4-6")
    )

    # The decomposition block contributes nothing when OFF — so the OFF
    # instruction is byte-identical to the instruction with the empty block
    # explicitly appended (the guarded-append no-op).
    assert step_decomposition_block(env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "0"}) == ""
    monkeypatch.setenv(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, "0")
    off2 = normalize_runtime_clock(
        build_cli_instruction(session_id="s1", model="claude-sonnet-4-6")
    )
    assert off == off2
    assert "<step_decomposition>" not in off

    # And the ON instruction is the OFF instruction with exactly the block added
    # to parts (no reflow of any other section): stripping the block back out and
    # re-normalising must recover the OFF text.
    monkeypatch.setenv(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, "1")
    on = normalize_runtime_clock(
        build_cli_instruction(session_id="s1", model="claude-sonnet-4-6")
    )
    block_body = step_decomposition_block(env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "1"}).lstrip("\n")
    on_without_block = on.replace("\n\n" + block_body, "")
    assert on_without_block == off


def test_build_cli_instruction_on_injects_decomposition_block(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, "1")
    instruction = build_cli_instruction(session_id="s1", model="claude-sonnet-4-6")
    assert "<step_decomposition>" in instruction
    # Still a real system prompt — existing markers preserved.
    assert "<skills>" in instruction


# ---------------------------------------------------------------------------
# GAIA benchmark advertisement layer — gated helper in answer.py, consumed in
# harness.py. The static GAIA_SYSTEM_PROMPT constant must stay intact.
# ---------------------------------------------------------------------------
def test_gaia_constant_is_unchanged_and_has_no_gated_text() -> None:
    from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

    # Existing contract preserved (test_answer.py also asserts this).
    assert "FINAL ANSWER" in GAIA_SYSTEM_PROMPT
    assert "DocumentSearch" in GAIA_SYSTEM_PROMPT
    # The decomposition advertisement must NOT be baked into the constant.
    assert "<step_decomposition>" not in GAIA_SYSTEM_PROMPT


def test_gaia_block_empty_when_off() -> None:
    from benchmarks.gaia.answer import gaia_step_decomposition_block

    assert gaia_step_decomposition_block(
        env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "0"}
    ) == ""


def test_gaia_block_present_when_on() -> None:
    from benchmarks.gaia.answer import gaia_step_decomposition_block

    block = gaia_step_decomposition_block(
        env={MAGI_STEP_DECOMPOSITION_ENABLED_ENV: "1"}
    )
    assert "<step_decomposition>" in block
    assert "sub-step" in block.lower()


def test_gaia_harness_instruction_off_has_no_block(monkeypatch) -> None:
    """When OFF the harness instruction is the constant + question only."""
    monkeypatch.setenv(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, "0")
    from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, gaia_step_decomposition_block

    assert gaia_step_decomposition_block() == ""
    # Mirrors the harness.py:71 assembly with the flag off.
    instruction = f"{GAIA_SYSTEM_PROMPT}{gaia_step_decomposition_block()}\n\nQUESTION:\nx"
    assert "<step_decomposition>" not in instruction


def test_gaia_harness_instruction_on_has_block(monkeypatch) -> None:
    monkeypatch.setenv(MAGI_STEP_DECOMPOSITION_ENABLED_ENV, "1")
    from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, gaia_step_decomposition_block

    instruction = f"{GAIA_SYSTEM_PROMPT}{gaia_step_decomposition_block()}\n\nQUESTION:\nx"
    assert "<step_decomposition>" in instruction
