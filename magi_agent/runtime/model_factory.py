"""E-7 — single seam for per-turn model construction.

Two surfaces (`cli/real_runner._build_litellm_model` and the hosted
`shadow/gate5b4c3_live_runner_boundary._gate1a_correlated_model_or_label`)
previously each carried an independent "is this an Anthropic route? →
build_cache_aware_claude(...)" decision. The two were one merge away
from drifting (e.g. one updating to a new cache-marker version or guard
condition, the other staying behind).

This module is the single seam. A meta-test
(``tests/runtime/test_model_factory_cache.py::test_only_factory_and_definition_call_build_cache_aware_claude``)
forbids any new caller of ``build_cache_aware_claude`` outside this
module + the definition in ``adk_bridge/anthropic_cache_model.py``.

The reasoning-kwargs (E-6) gap on the Anthropic cache-aware path is
known but out-of-scope for E-7: ``build_cache_aware_claude`` returns an
ADK ``AnthropicLlm`` subclass that takes ``thinking`` from
``GenerateContentConfig`` rather than from constructor kwargs — wiring
E-6 there needs a separate change. See the follow-up note in the
companion plan.
"""

from __future__ import annotations

import os
from typing import Mapping, Protocol

from magi_agent.adk_bridge.anthropic_cache_model import build_cache_aware_claude
from magi_agent.config.env import is_message_cache_enabled


class _SupportsModelAndProvider(Protocol):
    provider: str
    model: str
    api_key: str


def maybe_build_cache_aware_anthropic(
    config: _SupportsModelAndProvider,
    env: Mapping[str, str] | None = None,
    *,
    gate_on_flag: bool = True,
    custom_endpoint: bool = False,
) -> object | None:
    """Return a cache-aware ADK Anthropic model OR None.

    None signals "use the standard path" (a LiteLlm at the CLI seam, or
    a bare model label at the shadow boundary — each caller decides what
    "standard" looks like for its surface). Never raises: every unsafe
    or disabled condition falls back to ``None`` so the worst case is
    today's non-cache path.

    Returns ``None`` when:

    - ``config.provider != "anthropic"``;
    - ``gate_on_flag`` is True and ``MAGI_MESSAGE_CACHE_ENABLED`` is OFF;
    - ``custom_endpoint`` is True (the native Anthropic client may not
      honor a custom API base; CLI passes this when
      ``_model_api_base_kwargs(env)`` yields an ``api_base``);
    - the cache-aware build itself raises (e.g. the optional
      ``anthropic`` package is absent or the ADK Anthropic import fails).

    ``gate_on_flag``:
        - True (CLI/headless/TUI) — only build cache-aware when the
          flag is ON. CLI behavior is byte-identical to the pre-E-7
          path.
        - False (hosted serve ``shadow/gate5b4c3`` boundary) — always
          build for Anthropic. The hosted runtime treats prompt caching
          as an unconditional optimisation for Claude routes (the flag
          gates only the local surface today).
    """

    if config.provider != "anthropic":
        return None
    try:
        if gate_on_flag and not is_message_cache_enabled(env):
            return None
        if custom_endpoint:
            return None
        # Backfill ANTHROPIC_API_KEY in ``os.environ`` so the underlying
        # AnthropicLlm client picks it up. The legacy CLI path did the
        # same — preserve byte-identical credential surfacing.
        api_key = getattr(config, "api_key", None)
        if api_key and not os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = api_key
        return build_cache_aware_claude(config.model)
    except Exception:
        return None


__all__ = ["build_cache_aware_claude", "maybe_build_cache_aware_anthropic"]
