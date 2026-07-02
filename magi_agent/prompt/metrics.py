"""Prompt cache metrics and environment config loader.

Tracks cache effectiveness across turns by recording
``cache_read_input_tokens`` and ``cache_creation_input_tokens`` from
Anthropic API response usage fields.

Usage::

    from magi_agent.prompt.metrics import PromptCacheMetrics, load_cache_config

    enabled, provider = load_cache_config()

    metrics = PromptCacheMetrics()
    metrics.record_api_usage(response.usage.model_dump())
    print(metrics.to_evidence())
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass
class PromptCacheMetrics:
    """Tracks prompt cache effectiveness across turns.

    Records ``cache_read_input_tokens`` and ``cache_creation_input_tokens``
    from Anthropic API responses to measure cache hit rate and token savings.
    """

    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    total_input_tokens: int = 0
    turns_recorded: int = 0

    def record_api_usage(self, usage: dict) -> None:
        """Record cache stats from an API response's usage field.

        Anthropic responses include:
        - ``cache_creation_input_tokens``: tokens written to cache this turn.
        - ``cache_read_input_tokens``: tokens read from cache this turn.
        - ``input_tokens``: total input tokens this turn.

        Missing keys default to 0 (safe for non-Anthropic providers).

        Args:
            usage: A ``dict`` representation of the API usage object.
        """
        self.cache_creation_tokens += usage.get("cache_creation_input_tokens", 0)
        self.cache_read_tokens += usage.get("cache_read_input_tokens", 0)
        self.total_input_tokens += usage.get("input_tokens", 0)
        self.turns_recorded += 1

    @property
    def cache_hit_rate(self) -> float:
        """Fraction of input tokens served from cache.

        Returns 0.0 when no turns have been recorded (avoids division by zero).
        """
        if self.total_input_tokens == 0:
            return 0.0
        return self.cache_read_tokens / self.total_input_tokens

    @property
    def tokens_saved(self) -> int:
        """Total tokens served from cache."""
        return self.cache_read_tokens

    def to_evidence(self) -> dict:
        """Export as an evidence record compatible with the harness evidence gate.

        Returns:
            A dict with ``type="prompt_cache_metrics"`` and all accumulated
            counters plus derived metrics.
        """
        return {
            "type": "prompt_cache_metrics",
            "cache_creation_tokens": self.cache_creation_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "total_input_tokens": self.total_input_tokens,
            "turns_recorded": self.turns_recorded,
            "cache_hit_rate": round(self.cache_hit_rate, 4),
            "tokens_saved": self.tokens_saved,
        }


def load_cache_config(
    env: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    """Load prompt cache configuration from the flag registry.

    ``MAGI_PROMPT_CACHE_ENABLED`` is a profile-aware default-ON flag: unset
    resolves to ON under the full runtime profile and OFF under
    ``safe``/``eval``/``minimal``/``conservative``/``off``. An explicit
    ``"0"/"false"/...`` or ``"1"/"true"/yes/on"`` always wins.
    ``MAGI_PROMPT_CACHE_PROVIDER`` is a provider hint (``"anthropic"``,
    ``"openai"``, ``"google"``, or ``"auto"``); it defaults to ``"auto"``.

    Args:
        env: Optional environment mapping. ``None`` reads ``os.environ``.

    Returns:
        A ``(enabled, provider)`` tuple.
    """
    from magi_agent.config.flags import (  # noqa: PLC0415
        flag_profile_bool,
        flag_str,
    )

    enabled = flag_profile_bool("MAGI_PROMPT_CACHE_ENABLED", env=env)
    provider = flag_str("MAGI_PROMPT_CACHE_PROVIDER", env=env) or "auto"
    return enabled, provider
