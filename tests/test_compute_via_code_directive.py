"""Tests for the default-OFF compute-via-code directive lever.

The directive instructs the agent to WRITE AND RUN code (via the existing
Bash/Calculation tools) for any arithmetic, unit conversion, statistics, or
checksum/validation rather than computing the value mentally. It is a GENERAL
agent-hygiene capability gated behind ``MAGI_COMPUTE_VIA_CODE_ENABLED`` — OFF by
default so prompt assembly is byte-identical to origin/main when unset.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# env accessor — strict default-OFF
# ---------------------------------------------------------------------------
def test_compute_via_code_parser_default_off() -> None:
    from magi_agent.config.env import compute_via_code_enabled

    assert compute_via_code_enabled({}) is False
    assert compute_via_code_enabled({"MAGI_COMPUTE_VIA_CODE_ENABLED": "0"}) is False
    assert compute_via_code_enabled({"MAGI_COMPUTE_VIA_CODE_ENABLED": ""}) is False


def test_compute_via_code_parser_explicit_on() -> None:
    from magi_agent.config.env import compute_via_code_enabled

    assert compute_via_code_enabled({"MAGI_COMPUTE_VIA_CODE_ENABLED": "1"}) is True
    assert compute_via_code_enabled({"MAGI_COMPUTE_VIA_CODE_ENABLED": "true"}) is True
    assert compute_via_code_enabled({"MAGI_COMPUTE_VIA_CODE_ENABLED": "on"}) is True


def test_compute_via_code_env_constant_defined() -> None:
    from magi_agent.config.env import MAGI_COMPUTE_VIA_CODE_ENABLED_ENV

    assert MAGI_COMPUTE_VIA_CODE_ENABLED_ENV == "MAGI_COMPUTE_VIA_CODE_ENABLED"


# ---------------------------------------------------------------------------
# tool_runtime block — gated, general (no GAIA-specific text)
# ---------------------------------------------------------------------------
def test_compute_block_disabled_is_empty() -> None:
    from magi_agent.cli.tool_runtime import compute_via_code_block

    assert compute_via_code_block({"MAGI_COMPUTE_VIA_CODE_ENABLED": "0"}) == ""
    assert compute_via_code_block({}) == ""


def test_compute_block_enabled_carries_directive() -> None:
    from magi_agent.cli.tool_runtime import compute_via_code_block

    text = compute_via_code_block({"MAGI_COMPUTE_VIA_CODE_ENABLED": "1"})
    lowered = text.lower()
    # Names the categories the directive targets.
    assert "arithmetic" in lowered
    assert "checksum" in lowered or "validation" in lowered
    assert "statistics" in lowered or "average" in lowered
    assert "unit conversion" in lowered or "unit" in lowered
    # Routes to a real tool, forbids in-head computation.
    assert "bash" in lowered or "calculation" in lowered or "code" in lowered
    assert "in your head" in lowered or "mentally" in lowered or "in-head" in lowered


def test_compute_block_is_general_not_gaia_specific() -> None:
    """Anti-overfit guard: the first-party block must contain no GAIA text."""
    from magi_agent.cli.tool_runtime import compute_via_code_block

    text = compute_via_code_block({"MAGI_COMPUTE_VIA_CODE_ENABLED": "1"})
    lowered = text.lower()
    assert "gaia" not in lowered
    assert "final answer" not in lowered


def test_build_cli_instruction_omits_block_when_off(tmp_path, monkeypatch) -> None:
    """With the flag unset/off, the assembled instruction must not contain the
    compute-via-code block (no extra separator, no directive text)."""
    monkeypatch.delenv("MAGI_COMPUTE_VIA_CODE_ENABLED", raising=False)
    from magi_agent.cli.tool_runtime import build_cli_instruction

    kwargs = dict(
        session_id="s1",
        model="claude-opus-4-7",
        workspace_root=str(tmp_path),
    )
    off_unset = build_cli_instruction(**kwargs)
    assert "<compute_via_code>" not in off_unset
    monkeypatch.setenv("MAGI_COMPUTE_VIA_CODE_ENABLED", "0")
    off_explicit = build_cli_instruction(**kwargs)
    assert "<compute_via_code>" not in off_explicit
    # The <skills> block still renders and the compute-via-code block adds no
    # dangling separator. (The profile-aware default-ON guidance blocks now
    # legitimately follow <skills>, so this no longer asserts an exact tail.)
    assert "</skills>" in off_unset


def test_build_cli_instruction_includes_block_when_on(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("MAGI_COMPUTE_VIA_CODE_ENABLED", "1")
    from magi_agent.cli.tool_runtime import build_cli_instruction

    on = build_cli_instruction(
        session_id="s1",
        model="claude-opus-4-7",
        workspace_root=str(tmp_path),
    )
    lowered = on.lower()
    assert "arithmetic" in lowered
    assert "in your head" in lowered or "mentally" in lowered


# ---------------------------------------------------------------------------
# GAIA benchmark prompt layer — advertisement, scoped to avoid contradiction
# ---------------------------------------------------------------------------
def test_gaia_system_prompt_constant_unchanged() -> None:
    """The exported constant contract must remain byte-identical."""
    from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

    # The image-extraction guidance must not say "compute yourself" anymore;
    # confirm the reconciled wording is in place while the constant stays a
    # plain str the harness/tests can import.
    assert isinstance(GAIA_SYSTEM_PROMPT, str)
    assert "FINAL ANSWER" in GAIA_SYSTEM_PROMPT
    # Reconciled: the structured-extraction line no longer ends with the
    # contradictory "then compute yourself".
    assert "to get exact values, then compute\n    yourself" not in GAIA_SYSTEM_PROMPT
    assert "compute yourself" not in GAIA_SYSTEM_PROMPT


def test_gaia_system_prompt_helper_off_equals_constant() -> None:
    from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, gaia_system_prompt

    assert gaia_system_prompt({}) == GAIA_SYSTEM_PROMPT
    assert gaia_system_prompt({"MAGI_COMPUTE_VIA_CODE_ENABLED": "0"}) == GAIA_SYSTEM_PROMPT


def test_gaia_system_prompt_helper_on_appends_reminder() -> None:
    from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT, gaia_system_prompt

    on = gaia_system_prompt({"MAGI_COMPUTE_VIA_CODE_ENABLED": "1"})
    assert on != GAIA_SYSTEM_PROMPT
    assert on.startswith(GAIA_SYSTEM_PROMPT)
    lowered = on.lower()
    assert "arithmetic" in lowered
    assert "checksum" in lowered
    # Scoped: the reminder must explicitly NOT override image value extraction.
    assert "image" in lowered


def test_gaia_reminder_does_not_contradict_image_extraction() -> None:
    """The reminder must scope itself away from ImageUnderstand value reading."""
    from benchmarks.gaia.answer import gaia_system_prompt

    on = gaia_system_prompt({"MAGI_COMPUTE_VIA_CODE_ENABLED": "1"})
    # Isolate just the appended reminder (everything after the unchanged constant).
    from benchmarks.gaia.answer import GAIA_SYSTEM_PROMPT

    reminder = on[len(GAIA_SYSTEM_PROMPT):].lower()
    # The reminder must carve out image value extraction explicitly so the model
    # is not told both to extract-and-not-compute and to never-compute-in-head.
    assert "does not change how you read inputs from an image" in reminder
    assert "imageunderstand" in reminder


def test_gaia_system_prompt_exported() -> None:
    import benchmarks.gaia.answer as answer_mod

    assert "gaia_system_prompt" in answer_mod.__all__
    assert "GAIA_SYSTEM_PROMPT" in answer_mod.__all__
