"""Prompt split logic: partition a flat list of prompt parts into static and
dynamic :class:`PromptBlock` instances.

Design contract
---------------
The system prompt assembled by ``message_builder.build_system_prompt()``
produces a ``list[str]`` called *prompt_parts*.  The assembly order is
**cache-optimised**: all stable content precedes the dynamic boundary so the
prompt prefix is byte-identical across turns and maximises cache hits.

0. ``rendered_identity``         — STATIC   (bootstrap/soul/learning/identity/
                                             user/agents/tools joined into one
                                             string by ``_render_identity_system``)
1. ``DEFERRAL_PREVENTION_BLOCK`` — STATIC
2. ``OUTPUT_RULES_BLOCK``        — STATIC
── ``__MAGI_PROMPT_DYNAMIC_BOUNDARY__`` ──
3. ``session_header``            — DYNAMIC  (turn id, timestamp, channel)
4. ``temporal_context``          — DYNAMIC  (per-turn clock)
5. ``memory_mode_block``         — DYNAMIC  (present only when non-normal mode)
6. ``system_prompt_addendum``    — DYNAMIC  (present only when metadata carries one)

The actual list length varies (5–8 items including the boundary marker)
depending on whether the optional parts are present.  Static parts occupy the
first N indices (contiguous prefix), followed by the boundary marker, then
dynamic parts.

This module does NOT attempt to parse the content of each part.  The caller
explicitly declares which indices are static via ``static_indices``.

``split_system_prompt(parts, static_indices)`` marks each part whose index
appears in ``static_indices`` as ``cache_scope="global"``; all other parts
receive ``cache_scope=None``.  Out-of-bounds indices are silently ignored.
"""

from __future__ import annotations

from .types import PromptBlock, PromptSplitResult


def split_system_prompt(
    parts: list[str],
    static_indices: frozenset[int],
) -> PromptSplitResult:
    """Split *parts* into static and dynamic :class:`PromptBlock` instances.

    Args:
        parts: Ordered list of prompt section strings in assembly order.
        static_indices: Set of indices (into *parts*) that are stable across
            turns and may be cached.  Parts at indices **not** in this set are
            treated as dynamic (``cache_scope=None``).  Indices outside the
            range ``[0, len(parts))`` are silently ignored.

    Returns:
        A :class:`PromptSplitResult` whose ``blocks`` tuple mirrors *parts*
        in order, each annotated with the appropriate ``cache_scope``.

    Example::

        # Actual build_system_prompt() layout: static at positions 2, 5, 6.
        result = split_system_prompt(parts, static_indices=frozenset({2, 5, 6}))
    """
    blocks: list[PromptBlock] = []
    for index, text in enumerate(parts):
        scope: str | None = "global" if index in static_indices else None
        blocks.append(PromptBlock(text=text, cache_scope=scope))
    return PromptSplitResult(blocks=tuple(blocks))
