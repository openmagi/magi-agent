"""Child-scoped missing-tool streak guard (Fix F backstop).

A weak child model can hallucinate tool names it was never given and loop on
them: each call returns a ``tool_not_found`` / ``tool_not_exposed`` corrective
response, the model tries another non-existent tool, and the spiral burns the
child's full turn budget (2 to 4 minutes on the live serve) before timing out.
The prompt-alignment root fix (PR-P) removes the INDUCED cause; this is the
cheap, child-scoped backstop for residual training-prior hallucination.

Signal: count CONSECUTIVE missing-tool tool responses within one child turn,
across tool NAMES (the live spiral cycles XLSXRead -> BrowserTask -> Bash, so a
per-name counter under-counts). ANY non-missing-tool response (a success, a
real tool error such as a missing file, a blocked/needs-approval result) RESETS
the streak: a child legitimately probing paths with FileRead/Glob must never
trip. Text/thinking deltas never touch the streak.

Parent scope: this module is imported ONLY by the child runner and the child
governed collector, so a top-level (parent) turn never runs it.
"""

from __future__ import annotations

from collections.abc import Mapping

#: The soft-fail corrective-response marker (mirrors
#: ``magi_agent.adk_bridge.tool_not_found_soft_fail.TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE``).
#: Inlined as a plain string constant so importing this guard does NOT pull the
#: heavy ``google.adk`` plugin surface into ``child_runner_live`` (whose import
#: is contract-tested to stay light). Guarded against drift by
#: ``test_child_missing_tool_guard.test_soft_fail_marker_matches_source``.
TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE = "MAGI_TOOL_NOT_FOUND_SOFT_FAIL"

#: The error codes that mark a hallucinated / unexposed tool call. Dispatcher
#: emits ``tool_not_found`` / ``tool_not_exposed`` (`tools/dispatcher.py`); the
#: ADK-unknown-tool soft-fail plugin emits ``tool_not_found`` on a corrective
#: dict tagged with ``TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE``.
MISSING_TOOL_ERROR_CODES = frozenset({"tool_not_found", "tool_not_exposed"})

#: Reason token surfaced when the streak trips (composes with #1435 B1 reason
#: surfacing and #1458 partialSummary).
MISSING_TOOL_STREAK_REASON = "child_llm_missing_tool_streak_exhausted"

#: Env knob for the consecutive missing-tool cap. Generous default: a healthy
#: child virtually never produces even two consecutive misses (each miss returns
#: the honest available-tools list), so 4 is unambiguous pathology at a cost of a
#: few seconds. ``0`` disables the guard.
MISSING_TOOL_STREAK_CAP_ENV = "MAGI_CHILD_MISSING_TOOL_STREAK_CAP"
_DEFAULT_MISSING_TOOL_STREAK_CAP = 4


def resolve_missing_tool_streak_cap(env: Mapping[str, str]) -> int:
    """Resolve the cap from ``env``. A non-negative int wins; anything else
    (unset, empty, non-numeric, negative) falls back to the default. ``0``
    disables the guard. Never raises."""
    raw = env.get(MISSING_TOOL_STREAK_CAP_ENV)
    if raw is None:
        return _DEFAULT_MISSING_TOOL_STREAK_CAP
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEFAULT_MISSING_TOOL_STREAK_CAP
    return parsed if parsed >= 0 else _DEFAULT_MISSING_TOOL_STREAK_CAP


def classify_missing_tool_response(response: object) -> bool | None:
    """Classify one tool response dict.

    Returns:
        ``True``  if it is a missing-tool marker (increments the streak),
        ``False`` if it is any other tool response (resets the streak),
        ``None``  if it is not a tool response dict at all (ignored).

    Never raises: an unrecognised shape returns ``None`` (fail-open, the streak
    is untouched, so a payload we cannot read never trips the guard).
    """
    if not isinstance(response, Mapping):
        return None
    # Accept both snake_case (soft-fail corrective dict / dispatcher) and the
    # camelCase the local tool_end projection uses.
    error_code = response.get("error_code")
    if error_code is None:
        error_code = response.get("errorCode")
    response_type = response.get("response_type") or response.get("responseType")

    is_soft_fail = response_type == TOOL_NOT_FOUND_SOFT_FAIL_RESPONSE_TYPE
    is_missing_code = isinstance(error_code, str) and error_code in MISSING_TOOL_ERROR_CODES
    if is_soft_fail or is_missing_code:
        return True
    return False


class MissingToolStreak:
    """Consecutive missing-tool response counter for ONE child turn.

    ``cap <= 0`` disables the guard (``update`` never trips), preserving
    byte-identical collection behaviour when the operator turns it off.
    """

    __slots__ = ("_cap", "_count")

    def __init__(self, cap: int) -> None:
        self._cap = int(cap)
        self._count = 0

    @property
    def count(self) -> int:
        return self._count

    def update(self, marker: bool | None) -> bool:
        """Fold one classification result into the streak.

        ``marker`` is the :func:`classify_missing_tool_response` return value:
        ``True`` increments, ``False`` resets, ``None`` is ignored. Returns
        ``True`` exactly once, on the update that REACHES the cap (a runaway
        spiral), so the caller can terminate the turn.
        """
        if self._cap <= 0:
            return False
        if marker is None:
            return False
        if marker is False:
            self._count = 0
            return False
        self._count += 1
        return self._count >= self._cap
