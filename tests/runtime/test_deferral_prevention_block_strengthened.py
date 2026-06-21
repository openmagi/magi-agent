"""DEFERRAL_PREVENTION_BLOCK must do real work — block the "I'll do X next"
patterns that strand a multi-step task after one turn (the 0.1.63 Kimi K2.6
SEC.gov repro: model wrote "Refreshed Plan" + "Next Action: Use WebFetch …"
and stopped without calling WebFetch).

Source guidance drawn from:
  - cc-workspace/claude-code/src/constants/prompts.ts — "Don't restate the
    plan or say what you will do next. Keep executing step by step until the
    entire task is fully complete."
  - cc-workspace/opencode/packages/opencode/src/session/prompt/default.txt —
    "Only use tools to complete tasks." / "Never use tools like Bash or code
    comments as means to communicate with the user during the session."
  - cc-workspace/opencode/packages/opencode/src/session/prompt/kimi.txt —
    "default to taking action with tools" / "Code that only appears in your
    text response is NOT saved to the file system and will not take effect."

The existing 3-line block in main is too soft for models with weak tool
discipline (Kimi K2.6 / MiniMax). This test pins the strengthened shape.
"""
from __future__ import annotations

import importlib
from types import ModuleType

import pytest


def _builder() -> ModuleType:
    return importlib.import_module("magi_agent.runtime.message_builder")


@pytest.fixture(scope="module")
def block() -> str:
    return _builder().DEFERRAL_PREVENTION_BLOCK


def test_block_is_tag_wrapped(block: str) -> None:
    assert "<deferral-prevention>" in block
    assert "</deferral-prevention>" in block


def test_block_names_action_default(block: str) -> None:
    # OpenCode kimi.txt: "default to taking action with tools".
    # The block must state that tool calls are the default, not narration.
    lowered = block.lower()
    assert ("default" in lowered and "tool" in lowered and "action" in lowered), block


def test_block_names_the_anti_patterns_explicitly(block: str) -> None:
    # English deferral phrasings the engine has seen models emit before stopping.
    for phrase in (
        "Refreshed Plan",
        "Next Action",
        "I'll",
        "Let me",
    ):
        assert phrase in block, f"{phrase!r} not named: {block}"


def test_block_names_korean_deferral_patterns(block: str) -> None:
    # Local serve is Korean-first; Korean deferral phrasings ("다음 단계 / 다음으로 / 할 것입니다 /
    # 하겠습니다") slipped past the prior block because they were not named.
    for phrase in (
        "다음 단계",
        "하겠습니다",
    ):
        assert phrase in block, f"{phrase!r} not named: {block}"


def test_block_names_describe_vs_execute_dichotomy(block: str) -> None:
    # The core principle: sentences that DESCRIBE the next step are forbidden;
    # tool calls that EXECUTE it are required.
    lowered = block.lower()
    assert "describe" in lowered, block
    assert "execute" in lowered, block


def test_block_includes_code_only_in_text_is_not_saved_rule(block: str) -> None:
    # Direct port from OpenCode kimi.txt: code in a text reply does NOT take
    # effect. This is the single most effective anti-deferral line we know of
    # for the Kimi family — it reframes "writing code in reply" as a no-op.
    lowered = block.lower()
    assert "not saved" in lowered or "will not take effect" in lowered, block
    assert "tool" in lowered, block


def test_block_demands_keep_executing_until_done(block: str) -> None:
    # From the existing hosted gate5b4c3 nudge: "Keep executing step by step
    # until the entire task is fully complete, then give the final answer."
    lowered = block.lower()
    assert (
        ("keep" in lowered or "until" in lowered)
        and ("complete" in lowered or "done" in lowered)
    ), block


def test_block_lists_concrete_blocker_as_legitimate_stop(block: str) -> None:
    # The only legitimate early stop is a concrete blocker. Without naming this,
    # the model has no escape valve and will fabricate a plan-only "completion".
    lowered = block.lower()
    assert "blocker" in lowered, block


def test_block_appears_in_assembled_system_prompt() -> None:
    # Make sure the strengthened block actually reaches the production prompt
    # on BOTH coding and non-coding paths (the deferral failure shows up in
    # general chat, not just code edits).
    builder = _builder()
    for coding_agent in (False, True):
        prompt = builder.build_system_prompt(
            session_key="test-session",
            turn_id="t-1",
            identity={},
            channel={},
            user_message="",
            coding_agent=coding_agent,
        )
        assert builder.DEFERRAL_PREVENTION_BLOCK in prompt, (coding_agent, prompt[-1500:])
