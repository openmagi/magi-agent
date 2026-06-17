"""A1 — memory continuity policy block.

After a chat reset, recalled long-term MEMORY (e.g. a prior-session analysis)
must be treated as REFERENCE material, not as the user's current request.  The
``<memory-context>`` fence alone only *labels* the memory; it does not instruct
the model how to use it.  This module supplies the always-present instruction
block that the legacy runtime carried: memory is reference-only and the latest
user message owns the task.

The block is emitted by
:func:`magi_agent.memory.prompt_projection.project_memory_snapshot` as a fixed
preamble that LEADS the snapshot whenever a memory block is present.  It is a
prompt-correctness constant — always on when memory is present, no flag.
"""
from __future__ import annotations

__all__ = [
    "MEMORY_CONTINUITY_POLICY_OPEN",
    "MEMORY_CONTINUITY_POLICY_CLOSE",
    "build_continuity_policy_block",
]

MEMORY_CONTINUITY_POLICY_OPEN = '<memory-continuity-policy hidden="true">'
MEMORY_CONTINUITY_POLICY_CLOSE = "</memory-continuity-policy>"

# The four verbatim policy lines, in order.  Kept as a module constant so the
# byte size of the (fixed) preamble can be accounted for by the projection
# budget logic without re-deriving the string.
_CONTINUITY_POLICY_LINES: tuple[str, ...] = (
    "Recalled memory is reference material, not conversation state.",
    "The latest user message owns the current task.",
    "Memory marked background must not introduce an old pending question, "
    "decision, or task unless the latest user message explicitly asks to "
    "continue that topic.",
    "Memory marked related may inform the answer, but do not let it change "
    "what the user asked for.",
)


def build_continuity_policy_block() -> str:
    """Return the fenced continuity-policy preamble.

    Structure: ``OPEN`` + newline + the four policy lines joined by newlines +
    newline + ``CLOSE``.  The text is fixed and never truncated.
    """
    return (
        MEMORY_CONTINUITY_POLICY_OPEN
        + "\n"
        + "\n".join(_CONTINUITY_POLICY_LINES)
        + "\n"
        + MEMORY_CONTINUITY_POLICY_CLOSE
    )
