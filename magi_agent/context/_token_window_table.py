"""E-4 — single canonical home for the model→context-window lookup table.

This module is intentionally a stdlib-only leaf (no project imports, no
``asyncio``/``socket``/``subprocess``). Both ``context/token_tracker.py``
(which adds the optional ``estimate_message_tokens`` backend) and
``runtime/message_builder.py`` (which adds OpenAI-compat fallback policy
on top) re-export ``_KNOWN_TOKEN_LIMITS`` from here. The two used to be
byte-identical 35-entry dicts kept in lockstep by a comment.

The structural follow-up routes everyone through ``ModelCatalog`` (E-1);
this leaf is the dedup half of E-4.
"""

from __future__ import annotations

_KNOWN_TOKEN_LIMITS: dict[str, int] = {
    "claude-opus-4-8": 150_000,
    "claude-opus-4-6": 150_000,
    "claude-sonnet-4-6": 150_000,
    "claude-haiku-4-5-20251001": 150_000,
    "claude-haiku-4-5": 150_000,
    "anthropic/claude-opus-4-8": 150_000,
    "anthropic/claude-opus-4-6": 150_000,
    "anthropic/claude-sonnet-4-6": 150_000,
    "anthropic/claude-haiku-4-5": 150_000,
    "openai/gpt-5.4-nano": 96_000,
    "gpt-5.4-nano": 96_000,
    "gpt-5-nano": 300_000,
    "gpt-5-mini": 300_000,
    "gpt-5.1": 300_000,
    "gpt-5.4": 300_000,
    "openai/gpt-5.4-mini": 96_000,
    "gpt-5.4-mini": 96_000,
    "openai/gpt-5.5": 750_000,
    "gpt-5.5": 750_000,
    "magi-smart-router/auto": 750_000,
    "big-dic-router/auto": 196_608,
    "openai/gpt-5.5-pro": 787_500,
    "openai-codex/gpt-5.5": 750_000,
    "fireworks/kimi-k2p6": 196_608,
    "kimi-k2p6": 192_000,
    "fireworks/minimax-m2p7": 147_456,
    "minimax-m2p7": 192_000,
    "google/gemini-3.5-flash": 786_432,
    "gemini-3.5-flash": 786_432,
    "google/gemini-3.1-flash-lite-preview": 786_432,
    "gemini-3.1-flash-lite-preview": 750_000,
    "google/gemini-3.1-pro-preview": 786_432,
    "gemini-3.1-pro-preview": 750_000,
    "local/gemma-fast": 98_304,
    "local/gemma-max": 98_304,
    "local/qwen-uncensored": 98_304,
    # E-3 closes registry/window gaps that previously silently fell to the
    # 150k default:
    #   - ``haiku`` alias used by model_tiers.with_defaults
    #   - ``kimi-k2p5`` Fireworks legacy id still in the registry
    #   - ``gpt-5.5-pro`` (bare form; the ``openai/`` prefixed form already
    #     resolved). The bare form is what model_tiers returns from
    #     resolve_child_route, so it must resolve too.
    "haiku": 150_000,
    "kimi-k2p5": 196_608,
    "gpt-5.5-pro": 787_500,
}

__all__ = ["_KNOWN_TOKEN_LIMITS"]
