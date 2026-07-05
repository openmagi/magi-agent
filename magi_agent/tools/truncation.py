"""Shared head+tail ("middle") truncation for tool outputs.

Default-OFF behind ``MAGI_HEADTAIL_TRUNCATION_ENABLED``. When the flag is
unset or falsy, :func:`cap_text` is byte-identical to the legacy head-only
slice (``content[:max_chars]``), so no existing behavior changes.

Why: long documents, tables, logs, and scraped pages carry answers in their
tails (totals rows, final errors, signature blocks, conclusions). Head-only
caps silently drop them. ``Bash``/``TestRun`` capture already keeps a 3/5
head + 2/5 tail with an elision marker (``_BoundedPipeCapture`` in
``gates/gate5b_full_toolhost.py``); this module brings the remaining
head-only text caps up to the same pattern.

Stdlib-only at import time (deliberately no ``magi_agent.config.env``
import) — keeps the module safe for the import-boundary tests and for
``web_search_tools.py``, which is dependency-light by design.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

__all__ = [
    "HEADTAIL_TRUNCATION_ENV",
    "cap_text",
    "is_headtail_truncation_enabled",
    "truncate_middle",
]

HEADTAIL_TRUNCATION_ENV = "MAGI_HEADTAIL_TRUNCATION_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})  # mirrors config/env.py


def is_headtail_truncation_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Profile-aware default-ON: head+tail (middle-elision) truncation is used
    under the full/lab (non-safe) runtime profile and disabled under the
    safe-family or an explicit ``"0"``. Promoted from the former strict opt-in
    so document/page tails stay visible by default.
    """
    from magi_agent.config.flags import flag_profile_bool  # noqa: PLC0415

    source = os.environ if env is None else env
    return flag_profile_bool(HEADTAIL_TRUNCATION_ENV, env=source)


def truncate_middle(content: str, max_chars: int) -> str:
    """Keep head (3/5) + tail (2/5) of *max_chars* with an elision marker.

    Mirrors ``_BoundedPipeCapture`` in ``gates/gate5b_full_toolhost.py``
    (smolagents ``truncate_content`` pattern). The marker is **additive** on
    top of *max_chars* (result length ≤ ``max_chars + ~110``), matching the
    Bash-capture precedent — callers with hard wire-limits should budget for
    it. Never raises; non-positive *max_chars* is clamped to 1 and content
    within budget is returned unchanged (no marker).
    """
    max_chars_clamped = max(1, max_chars)
    if len(content) <= max_chars_clamped:
        return content
    head_budget = max(1, (max_chars_clamped * 3) // 5)
    tail_budget = max(0, max_chars_clamped - head_budget)
    elided = len(content) - head_budget - tail_budget
    marker = (
        f"\n[... {elided} chars elided - output truncated; refine the request "
        "(offset/range/grep) to see the elided region ...]\n"
    )
    tail = content[-tail_budget:] if tail_budget else ""
    return content[:head_budget] + marker + tail


def cap_text(
    content: str,
    max_chars: int,
    *,
    env: Mapping[str, str] | None = None,
) -> tuple[str, bool]:
    """Drop-in replacement for ``content[:max_chars]`` call sites.

    Returns ``(capped_text, truncated)``. The *truncated* flag is identical
    between modes (``len(content) > max(1, max_chars)``). Flag OFF →
    ``(content[:max_chars], truncated)`` — byte-identical to the legacy
    head-only slice. Flag ON and over budget →
    ``(truncate_middle(content, max_chars), True)``.
    """
    max_chars_clamped = max(1, max_chars)
    if len(content) <= max_chars_clamped:
        return content, False
    if is_headtail_truncation_enabled(env):
        return truncate_middle(content, max_chars_clamped), True
    return content[:max_chars_clamped], True
