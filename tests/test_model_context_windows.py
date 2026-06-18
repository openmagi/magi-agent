"""E-3: the flagship ``claude-opus-4-8`` must have an explicit context window.

``runtime/model_tiers.py`` defaults the flagship to ``claude-opus-4-8`` but it
was absent from both ``_KNOWN_TOKEN_LIMITS`` tables, so its window resolved only
via a silent fallback. The flagship (and its ``anthropic/`` prefixed form) must
have an explicit entry equal to its Opus-4-6 sibling.
"""

from __future__ import annotations

from magi_agent.context.token_tracker import (
    _KNOWN_TOKEN_LIMITS as CONTEXT_LIMITS,
)
from magi_agent.runtime.message_builder import (
    _KNOWN_TOKEN_LIMITS as MESSAGE_BUILDER_LIMITS,
)

_FLAGSHIP_IDS = ("claude-opus-4-8", "anthropic/claude-opus-4-8")


def test_message_builder_table_knows_opus_4_8() -> None:
    sibling = MESSAGE_BUILDER_LIMITS["claude-opus-4-6"]
    for model_id in _FLAGSHIP_IDS:
        assert model_id in MESSAGE_BUILDER_LIMITS, f"{model_id} missing from table"
        assert MESSAGE_BUILDER_LIMITS[model_id] == sibling


def test_context_token_tracker_table_knows_opus_4_8() -> None:
    sibling = CONTEXT_LIMITS["claude-opus-4-6"]
    for model_id in _FLAGSHIP_IDS:
        assert model_id in CONTEXT_LIMITS, f"{model_id} missing from table"
        assert CONTEXT_LIMITS[model_id] == sibling


def test_context_window_resolves_for_opus_4_8() -> None:
    from magi_agent.context.token_tracker import TokenBudgetTracker

    tracker = TokenBudgetTracker(model="claude-opus-4-8")
    sibling = TokenBudgetTracker(model="claude-opus-4-6")
    assert tracker.context_window == sibling.context_window
