"""Public API for the prompt caching split package.

Re-exports all types, the splitter function, the section memoizer, and the
cache control injector so callers can import from
``magi_agent.prompt`` without knowing the internal module layout.

Example::

    from magi_agent.prompt import (
        PromptBlock,
        PromptCacheConfig,
        PromptSplitResult,
        PromptSectionCache,
        split_system_prompt,
        CacheControlInjector,
        detect_provider,
    )
"""

from __future__ import annotations

from .injection import CacheControlInjector, detect_provider
from .memoizer import PromptSectionCache
from .metrics import PromptCacheMetrics, load_cache_config
from .provider_adapter import (
    DefaultAdapter,
    GoogleAdapter,
    OpenAIAdapter,
    PromptAdapter,
    PromptRoutingConfig,
    ProviderFamily,
    adapt_identity_sections,
    detect_provider_family,
    get_adapter,
)
from .providers import (
    AnthropicCacheStrategy,
    GoogleCacheStrategy,
    OpenAICacheStrategy,
    ProviderCacheStrategy,
    get_strategy,
)
from .splitter import split_system_prompt
from .types import PromptBlock, PromptCacheConfig, PromptSplitResult

__all__ = [
    # PR 1 — types and splitter
    "PromptBlock",
    "PromptCacheConfig",
    "PromptSplitResult",
    "split_system_prompt",
    # PR 2 — section memoizer
    "PromptSectionCache",
    # PR 3 — provider strategies
    "ProviderCacheStrategy",
    "AnthropicCacheStrategy",
    "OpenAICacheStrategy",
    "GoogleCacheStrategy",
    "get_strategy",
    # PR 3 — injector
    "CacheControlInjector",
    "detect_provider",
    # PR 4 — metrics and config
    "PromptCacheMetrics",
    "load_cache_config",
    # Track 14 — model-aware prompt adaptation
    "ProviderFamily",
    "PromptAdapter",
    "PromptRoutingConfig",
    "DefaultAdapter",
    "OpenAIAdapter",
    "GoogleAdapter",
    "adapt_identity_sections",
    "detect_provider_family",
    "get_adapter",
]
