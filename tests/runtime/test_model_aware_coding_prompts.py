"""PR10: per-model semantic coding-hint blocks in the assembled system prompt.

When the model-aware flag is ON and the coding-agent path is active, the
assembled prompt carries a SMALL, family-keyed coding hint distilled from each
model family's known coding strengths/failure modes (OpenCode-style), e.g.

* GPT/o-series  -> verify stale-knowledge API/lib assumptions before trusting them
* Gemini        -> always use absolute file paths in tool calls
* Kimi/Moonshot -> code only takes effect when written via tools, not in reply text
* Claude        -> minimal note (Claude already follows the structured body well)

These tests assert:
  * each family's prompt contains ONLY its own hint (no cross-family bleed),
  * the hint lives in the STATIC (cacheable) region before the dynamic
    boundary sentinel,
  * the hard-safety protected sections still lead the prompt,
  * flag OFF  -> no hint (single shared body, zero regression),
  * non-coding agent -> no coding hint even with the flag ON.
"""

from __future__ import annotations

from datetime import UTC, datetime
import importlib
from types import ModuleType

import pytest


def _builder() -> ModuleType:
    try:
        return importlib.import_module("magi_agent.runtime.message_builder")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        pytest.fail(f"message_builder module is missing: {exc}")


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _build(builder: ModuleType, *, model: str, flag: bool, coding: bool = True) -> str:
    return builder.build_system_prompt(
        session_key="s1",
        turn_id="t1",
        identity={},
        now=_utc("2026-05-28T00:00:00Z"),
        coding_agent=coding,
        model=model,
        model_aware_prompts_enabled=flag,
    )


# The hint text fragments that uniquely identify each family's block.
_GPT_MARKER = "your training may be stale"
_GEMINI_MARKER = "absolute file paths"
_KIMI_MARKER = "is not saved to disk"
_CLAUDE_MARKER = "structured instruction blocks"

_HINT_OPEN = "<coding-model-hint"
_ALL_MARKERS = (_GPT_MARKER, _GEMINI_MARKER, _KIMI_MARKER, _CLAUDE_MARKER)


def _other_markers(own: str) -> tuple[str, ...]:
    return tuple(marker for marker in _ALL_MARKERS if marker != own)


@pytest.mark.parametrize(
    ("model", "own_marker"),
    [
        ("gpt-5", _GPT_MARKER),
        ("openai/gpt-5.5", _GPT_MARKER),
        ("gemini-3.5-flash", _GEMINI_MARKER),
        ("google/gemini-3.1-pro-preview", _GEMINI_MARKER),
        ("kimi-k2", _KIMI_MARKER),
        ("fireworks/kimi-k2p6", _KIMI_MARKER),
        ("claude-opus-4-6", _CLAUDE_MARKER),
        ("anthropic/claude-sonnet-4-6", _CLAUDE_MARKER),
    ],
)
def test_family_hint_present_and_isolated(model: str, own_marker: str) -> None:
    builder = _builder()
    out = _build(builder, model=model, flag=True, coding=True)

    assert _HINT_OPEN in out
    assert own_marker in out
    for other in _other_markers(own_marker):
        assert other not in out


def test_unknown_model_gets_generic_or_no_hint_but_no_family_bleed() -> None:
    builder = _builder()
    out = _build(builder, model="some-unknown-model", flag=True, coding=True)

    # The default family must not leak any other family's specific advice.
    for marker in _ALL_MARKERS:
        assert marker not in out


def test_flag_off_injects_no_hint_zero_regression() -> None:
    builder = _builder()
    on = _build(builder, model="gpt-5", flag=True, coding=True)
    off = _build(builder, model="gpt-5", flag=False, coding=True)

    assert _HINT_OPEN in on
    assert _HINT_OPEN not in off
    for marker in _ALL_MARKERS:
        assert marker not in off


def test_flag_off_prompt_is_model_independent() -> None:
    builder = _builder()
    gpt_off = _build(builder, model="gpt-5", flag=False, coding=True)
    gemini_off = _build(builder, model="gemini-3.5-flash", flag=False, coding=True)

    # Single shared body when the flag is off: identical regardless of model.
    assert gpt_off == gemini_off


def test_non_coding_agent_gets_no_hint_even_with_flag_on() -> None:
    builder = _builder()
    out = _build(builder, model="gpt-5", flag=True, coding=False)

    assert _HINT_OPEN not in out
    for marker in _ALL_MARKERS:
        assert marker not in out


def test_hint_lives_in_static_region_before_dynamic_boundary() -> None:
    builder = _builder()
    out = _build(builder, model="gemini-3.5-flash", flag=True, coding=True)

    boundary = builder.PROMPT_DYNAMIC_BOUNDARY
    assert boundary in out
    hint_at = out.index(_HINT_OPEN)
    boundary_at = out.index(boundary)
    assert hint_at < boundary_at


def test_protected_sections_still_lead_with_hint_present() -> None:
    builder = _builder()
    out = _build(builder, model="gpt-5", flag=True, coding=True)

    deferral_at = out.index(builder.DEFERRAL_PREVENTION_BLOCK)
    output_rules_at = out.index(builder.OUTPUT_RULES_BLOCK)
    action_safety_at = out.index(builder.ACTION_SAFETY_BLOCK)
    hint_at = out.index(_HINT_OPEN)

    # Protected hard-safety blocks remain at the very front, ahead of the hint.
    assert deferral_at < output_rules_at < action_safety_at
    assert action_safety_at < hint_at


def test_hint_sits_with_coding_blocks_in_static_region_for_blocks_builder() -> None:
    builder = _builder()
    # build_system_prompt_blocks hard-codes model_aware off for cache parity;
    # the per-family hint is a build_system_prompt feature. This guards that the
    # blocks builder remains regression-free (no hint, no crash) when coding.
    blocks = builder.build_system_prompt_blocks(
        session_key="s1",
        turn_id="t1",
        identity={},
        now=_utc("2026-05-28T00:00:00Z"),
        coding_agent=True,
        model="gpt-5",
    )
    text = "".join(str(block.get("text", "")) for block in blocks)
    assert _HINT_OPEN not in text
