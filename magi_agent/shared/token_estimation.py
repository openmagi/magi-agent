from __future__ import annotations

import json

__all__ = ["estimate_message_tokens", "estimate_messages_tokens"]


def estimate_message_tokens(message: dict[str, object]) -> int:
    """Estimate token count for a single message dict.

    Uses len(json.dumps(msg)) // 4 as a rough but fast approximation.
    """
    return len(json.dumps(message, default=str)) // 4


def estimate_messages_tokens(messages: list[dict[str, object]]) -> int:
    """Estimate total tokens for a list of messages."""
    return sum(estimate_message_tokens(m) for m in messages)
