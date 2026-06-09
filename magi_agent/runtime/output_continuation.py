"""Output continuation — resume a deliverable that hit the model's per-response
output-token cap.

Every model API caps the output tokens of a *single* response. When a long
deliverable (a multi-section report, a large file) exceeds that cap the model
returns a ``length``/``max_tokens`` finish reason and the answer is cut off
mid-sentence. Raising ``max_tokens`` only pushes the ceiling higher; it cannot
make one response unbounded. The only way to produce arbitrarily long output is
to re-invoke the model and *append* — exactly what the goal-nudge re-invocation
seam in ``cli.engine`` already does after a clean stop.

This module holds the pure (model-free, env-free) decision helpers so they can
be unit-tested in isolation; the wiring lives in ``cli.engine.run_turn_stream``.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "OutputContinuationConfig",
    "stop_reason_is_truncated",
    "should_continue",
    "build_continuation_message",
]


# Finish/stop-reason markers that mean "output was truncated at the token cap",
# normalized to lower case. Covers Anthropic (``max_tokens``), OpenAI/Fireworks
# (``length``) and their ADK/proxy spellings.
_TRUNCATED_STOP_REASONS = frozenset(
    {"max_tokens", "length", "max_output_tokens", "max_completion_tokens"}
)

_DEFAULT_MAX_CONTINUATIONS = 4


@dataclass(frozen=True)
class OutputContinuationConfig:
    """Resolved continuation policy. ``enabled=False`` makes the seam inert."""

    enabled: bool = False
    max_continuations: int = _DEFAULT_MAX_CONTINUATIONS


def stop_reason_is_truncated(stop_reason: object) -> bool:
    """True when ``stop_reason`` indicates the response hit the output cap."""
    return (
        isinstance(stop_reason, str)
        and stop_reason.strip().lower() in _TRUNCATED_STOP_REASONS
    )


def should_continue(
    config: OutputContinuationConfig | None,
    *,
    truncated: bool,
    output_seen: bool,
    continuations_used: int,
) -> bool:
    """Decide whether to re-invoke the model to resume a truncated response.

    * ``truncated`` — the just-finished response stopped at the output cap.
    * ``output_seen`` — the response actually emitted text (a truncation with no
      output is not a resumable deliverable; let the normal path handle it).
    * ``continuations_used`` — how many resumes already happened this turn (the
      budget guards against an unbounded loop).
    """
    if config is None or not config.enabled:
        return False
    if not truncated or not output_seen:
        return False
    return continuations_used < config.max_continuations


def build_continuation_message() -> str:
    """The user-role nudge that tells the model to resume, not restart."""
    return (
        "Your previous response was cut off because it reached the maximum "
        "output length. Continue exactly where you left off — resume from the "
        "next character, do not repeat any text you already wrote, and do not "
        "add a preamble. If the previous response ended mid-sentence or "
        "mid-word, continue that same sentence."
    )
