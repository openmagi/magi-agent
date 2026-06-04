"""Tests for AutoCompactionEngine (PR4 — Tier 5 auto-compact)."""
from __future__ import annotations

import asyncio
import subprocess
import sys

import pytest

from magi_agent.context.auto_compact import AutoCompactResult, AutoCompactionEngine
from magi_agent.context.types import WarningLevel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def mock_classifier(prompt: str) -> str:
    return "Summary of conversation"


async def failing_classifier(prompt: str) -> str:
    raise RuntimeError("LLM unavailable")


def _make_messages(n_turns: int) -> list[dict]:
    """Build a simple alternating user/assistant conversation of n_turns."""
    msgs: list[dict] = []
    for i in range(n_turns):
        msgs.append({"role": "user", "content": f"User message {i}"})
        msgs.append({"role": "assistant", "content": f"Assistant reply {i}"})
    return msgs


def _run(coro):
    """Execute a coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Test cases 1–3: Non-CRITICAL levels → no activation
# ---------------------------------------------------------------------------

def test_normal_level_no_activation() -> None:
    """WarningLevel.NORMAL must never activate compaction."""
    engine = AutoCompactionEngine(mock_classifier)
    messages = _make_messages(10)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.NORMAL))
    assert result.activated is False
    assert result_msgs is messages  # original list returned unchanged


def test_moderate_level_no_activation() -> None:
    """WarningLevel.MODERATE must never activate compaction."""
    engine = AutoCompactionEngine(mock_classifier)
    messages = _make_messages(10)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.MODERATE))
    assert result.activated is False
    assert result_msgs is messages


def test_high_level_no_activation() -> None:
    """WarningLevel.HIGH must never activate compaction."""
    engine = AutoCompactionEngine(mock_classifier)
    messages = _make_messages(10)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.HIGH))
    assert result.activated is False
    assert result_msgs is messages


# ---------------------------------------------------------------------------
# Test case 4: CRITICAL → activation (enough turns)
# ---------------------------------------------------------------------------

def test_critical_level_activates() -> None:
    """WarningLevel.CRITICAL with enough turns must activate compaction."""
    engine = AutoCompactionEngine(mock_classifier)
    # 5 turns → more than default keep_recent_turns=3
    messages = _make_messages(5)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))
    assert result.activated is True
    assert result.boundary_id is not None


# ---------------------------------------------------------------------------
# Test case 5: Not enough turns (<=3 user messages) → no compaction
# ---------------------------------------------------------------------------

def test_not_enough_turns_no_compaction() -> None:
    """With only 3 user messages (== keep_recent_turns), nothing to compact."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=3)
    messages = _make_messages(3)  # exactly 3 user messages
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))
    assert result.activated is False
    assert result_msgs is messages


def test_fewer_than_keep_recent_turns_no_compaction() -> None:
    """Fewer turns than keep threshold → no compaction."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=3)
    messages = _make_messages(2)  # only 2 user messages
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))
    assert result.activated is False


# ---------------------------------------------------------------------------
# Test case 6: Enough turns → old summarized, recent N preserved
# ---------------------------------------------------------------------------

def test_recent_turns_preserved() -> None:
    """Keep last 3 user turns; earlier turns are replaced by summary."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=3)
    # 6 turns: user messages at indices 0,2,4,6,8,10
    messages = _make_messages(6)

    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))

    assert result.activated is True
    # First message must be the summary placeholder
    assert result_msgs[0]["role"] == "user"
    assert result_msgs[0]["content"].startswith("[Previous conversation summary]")

    # Count user messages in the tail (recent part)
    # The last 3 user messages from the original must all still be present
    recent_user_contents = [
        m["content"] for m in result_msgs[1:] if m["role"] == "user"
    ]
    assert len(recent_user_contents) == 3
    # They should be the LAST 3 user messages of the original
    original_user_msgs = [m["content"] for m in messages if m["role"] == "user"]
    assert recent_user_contents == original_user_msgs[-3:]


# ---------------------------------------------------------------------------
# Test case 7: Classifier failure → fail-open, original messages returned
# ---------------------------------------------------------------------------

def test_classifier_failure_fail_open() -> None:
    """When the classifier raises, return original messages unchanged."""
    engine = AutoCompactionEngine(failing_classifier)
    messages = _make_messages(6)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))
    assert result.activated is False
    assert result_msgs is messages
    assert result.boundary_id is None
    assert result.turns_summarized == 0


# ---------------------------------------------------------------------------
# Test case 8: AutoCompactResult stats correct
# ---------------------------------------------------------------------------

def test_result_stats_correct() -> None:
    """Verify activated, turns_summarized, boundary_id, tokens_before/after."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=3)
    # 5 turns → 2 old turns summarized, 3 kept
    messages = _make_messages(5)
    _, result = _run(engine.apply(messages, WarningLevel.CRITICAL))

    assert result.activated is True
    assert result.turns_summarized == 2  # first 2 user messages compacted
    assert result.boundary_id is not None
    assert len(result.boundary_id) == 32  # uuid4().hex
    assert result.tokens_before > 0
    assert result.tokens_after > 0


# ---------------------------------------------------------------------------
# Test case 9: Summary message has correct format
# ---------------------------------------------------------------------------

def test_summary_message_format() -> None:
    """Summary message content must be '[Previous conversation summary]\\n\\n<summary>'."""
    engine = AutoCompactionEngine(mock_classifier)
    messages = _make_messages(5)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))

    assert result.activated is True
    summary_msg = result_msgs[0]
    assert summary_msg["role"] == "user"
    expected_content = "[Previous conversation summary]\n\nSummary of conversation"
    assert summary_msg["content"] == expected_content


# ---------------------------------------------------------------------------
# Test case 10: _find_boundary correctly identifies turn boundaries
# ---------------------------------------------------------------------------

def test_find_boundary_user_messages() -> None:
    """_find_boundary must split at the Nth-from-last user message index."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=3)

    # 5 user messages at indices: 0, 2, 4, 6, 8  (alternating with assistant)
    messages = _make_messages(5)
    # turn_starts = [0, 2, 4, 6, 8]
    # keep last 3 → boundary = turn_starts[-3] = 4
    boundary = engine._find_boundary(messages)
    assert boundary == 4
    # The message at index 4 should be the 3rd-from-last user message
    assert messages[boundary]["role"] == "user"
    assert messages[boundary]["content"] == "User message 2"


def test_find_boundary_not_enough_turns_returns_zero() -> None:
    """_find_boundary returns 0 when there are not enough turns."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=3)
    messages = _make_messages(2)  # only 2 user messages
    assert engine._find_boundary(messages) == 0


def test_find_boundary_exactly_keep_recent_turns_returns_zero() -> None:
    """_find_boundary returns 0 when turns == keep_recent_turns."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=3)
    messages = _make_messages(3)  # exactly 3 user messages
    assert engine._find_boundary(messages) == 0


# ---------------------------------------------------------------------------
# Additional: list content blocks handled in _format_conversation
# ---------------------------------------------------------------------------

def test_list_content_blocks_formatted() -> None:
    """Messages with list-style content blocks are flattened to text."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=1)

    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Hello"}, {"type": "text", "text": "World"}]},
        {"role": "assistant", "content": "OK"},
        {"role": "user", "content": "Follow-up"},
    ]
    # 2 user messages with keep_recent_turns=1 → first user message gets compacted
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))
    assert result.activated is True
    # The summary placeholder replaces the first user message
    assert result_msgs[0]["content"].startswith("[Previous conversation summary]")


# ---------------------------------------------------------------------------
# Additional: custom keep_recent_turns respected
# ---------------------------------------------------------------------------

def test_custom_keep_recent_turns() -> None:
    """keep_recent_turns=1 should keep only the last user turn."""
    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=1)
    messages = _make_messages(4)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))

    assert result.activated is True
    assert result.turns_summarized == 3  # 3 old turns summarized

    recent_user_msgs = [m for m in result_msgs[1:] if m["role"] == "user"]
    assert len(recent_user_msgs) == 1
    # Must be the very last user message
    assert recent_user_msgs[0]["content"] == "User message 3"


# ---------------------------------------------------------------------------
# PR8 fix: tokens_after includes protected messages (item 2)
# ---------------------------------------------------------------------------

def test_tokens_after_includes_protected_messages() -> None:
    """tokens_after must account for re-attached protected messages, not just the summary."""
    from magi_agent.harness.general_automation.constants import LOAD_GA_RECIPE_TOOL_NAME

    protected_body = "p" * 10_000
    protected_msg = {
        "role": "tool",
        "name": LOAD_GA_RECIPE_TOOL_NAME,
        "tool_use_id": "t-protected",
        "content": protected_body,
    }
    # Build 6 turns; protected result is in old region (turn 1)
    messages: list[dict] = []
    for i in range(6):
        messages.append({"role": "user", "content": f"user message {i}"})
        if i == 1:
            messages.append(protected_msg)
        messages.append({"role": "assistant", "content": f"assistant reply {i}"})

    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=2)
    _out, result = _run(engine.apply(messages, WarningLevel.CRITICAL))

    assert result.activated is True
    # tokens_after must be strictly greater than just the summary message alone
    # because the large protected body is re-attached verbatim.
    summary_msg_only = {"role": "user", "content": "[Previous conversation summary]\n\nSummary of conversation"}
    tokens_summary_only = len(__import__("json").dumps(summary_msg_only, default=str)) // 4
    assert result.tokens_after > tokens_summary_only, (
        f"tokens_after ({result.tokens_after}) should include protected body "
        f"(summary-only estimate: {tokens_summary_only})"
    )
    # Also verify it includes the protected body tokens
    tokens_protected = len(__import__("json").dumps(protected_msg, default=str)) // 4
    assert result.tokens_after >= tokens_summary_only + tokens_protected


# ---------------------------------------------------------------------------
# PR8 import boundary: microcompact + protected_tools must NOT load
# magi_agent.transport or magi_agent.recipes.* at module import time (item 1)
# ---------------------------------------------------------------------------

def _run_import_check(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )


def test_microcompact_import_does_not_load_transport_or_recipes() -> None:
    """Importing microcompact must NOT transitively load transport or recipes."""
    completed = _run_import_check(
        """
import importlib
import sys

importlib.import_module("magi_agent.context.microcompact")

forbidden_prefixes = (
    "magi_agent.transport",
    "magi_agent.recipes",
)
loaded = [
    name for name in sys.modules
    if any(name == p or name.startswith(p + ".") for p in forbidden_prefixes)
]
if loaded:
    raise AssertionError(
        f"magi_agent.context.microcompact import loaded forbidden modules: {loaded}"
    )
"""
    )
    assert completed.returncode == 0, completed.stderr


def test_protected_tools_import_does_not_load_transport_or_recipes() -> None:
    """Importing protected_tools must NOT transitively load transport or recipes."""
    completed = _run_import_check(
        """
import importlib
import sys

importlib.import_module("magi_agent.context.protected_tools")

forbidden_prefixes = (
    "magi_agent.transport",
    "magi_agent.recipes",
)
loaded = [
    name for name in sys.modules
    if any(name == p or name.startswith(p + ".") for p in forbidden_prefixes)
]
if loaded:
    raise AssertionError(
        f"magi_agent.context.protected_tools import loaded forbidden modules: {loaded}"
    )
"""
    )
    assert completed.returncode == 0, completed.stderr


# ---------------------------------------------------------------------------
# PR8 fix: role-gate in auto_compact protected_messages comprehension
# A non-tool-role message that merely carries a protected tool name must NOT
# be preserved (should be compacted); only genuine tool-results are protected.
# ---------------------------------------------------------------------------

def test_non_tool_role_message_with_protected_name_is_not_preserved() -> None:
    """A user/assistant message with name=LOAD_GA_RECIPE_TOOL_NAME must NOT be protected."""
    from magi_agent.harness.general_automation.constants import LOAD_GA_RECIPE_TOOL_NAME

    # A non-tool-role message that carries a protected tool name field
    imposter_msg = {
        "role": "user",
        "name": LOAD_GA_RECIPE_TOOL_NAME,
        "content": "imposter body " * 500,
    }
    messages: list[dict] = []
    for i in range(6):
        messages.append({"role": "user", "content": f"user message {i}"})
        if i == 1:
            messages.append(imposter_msg)
        messages.append({"role": "assistant", "content": f"assistant reply {i}"})

    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=2)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))

    assert result.activated is True
    # The imposter message must NOT appear verbatim in the output
    # (it falls in the old region and must be summarized away, not re-attached)
    for msg in result_msgs:
        assert msg is not imposter_msg, (
            "Non-tool-role message with protected name must not be preserved verbatim"
        )


def test_genuine_tool_result_with_protected_name_is_preserved() -> None:
    """A genuine tool-result (role=tool) with protected name IS preserved verbatim."""
    from magi_agent.harness.general_automation.constants import LOAD_GA_RECIPE_TOOL_NAME

    protected_msg = {
        "role": "tool",
        "name": LOAD_GA_RECIPE_TOOL_NAME,
        "tool_use_id": "t-real",
        "content": "protected playbook body " * 200,
    }
    messages: list[dict] = []
    for i in range(6):
        messages.append({"role": "user", "content": f"user message {i}"})
        if i == 1:
            messages.append(protected_msg)
        messages.append({"role": "assistant", "content": f"assistant reply {i}"})

    engine = AutoCompactionEngine(mock_classifier, keep_recent_turns=2)
    result_msgs, result = _run(engine.apply(messages, WarningLevel.CRITICAL))

    assert result.activated is True
    # The genuine protected tool result MUST appear verbatim in the output
    assert any(msg is protected_msg for msg in result_msgs), (
        "Genuine tool-result with protected name must be preserved verbatim"
    )
