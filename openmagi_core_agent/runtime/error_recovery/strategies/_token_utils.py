from __future__ import annotations

from openmagi_core_agent.shared.token_estimation import estimate_messages_tokens

__all__: list[str] = []


def _estimate_tokens(messages: list[dict[str, object]]) -> int:
    """Backward-compatible wrapper — delegates to shared module."""
    return estimate_messages_tokens(messages)
