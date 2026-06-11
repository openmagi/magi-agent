"""Deadline-awareness nudge for one-shot eval/headless runs.

The wall-clock budget is usually enforced externally (e.g. ``timeout`` in a
benchmark harness), so the runtime cannot see it. When the operator exports
``MAGI_EVAL_DEADLINE_SECONDS`` the toolhost appends a one-time nudge to a tool
result as each threshold of the budget passes, steering the agent to convert
its analysis into edits before the budget runs out. Unset => fully inert.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping

_THRESHOLDS: tuple[float, ...] = (0.6, 0.85)

_start_monotonic: float | None = None
_fired: set[float] = set()


def _now() -> float:
    return time.monotonic()


def reset_for_tests() -> None:
    global _start_monotonic
    _start_monotonic = None
    _fired.clear()


def deadline_note(
    env: Mapping[str, str] | None = None,
    *,
    now: float | None = None,
) -> str | None:
    """Return a one-time nudge string when a budget threshold has passed.

    Anchors the clock on the first call (≈ process/run start). Returns None
    when MAGI_EVAL_DEADLINE_SECONDS is unset/invalid or no new threshold has
    been crossed since the last call.
    """
    source = os.environ if env is None else env
    raw = (source.get("MAGI_EVAL_DEADLINE_SECONDS") or "").strip()
    if not raw:
        return None
    try:
        budget = float(raw)
    except ValueError:
        return None
    if budget <= 0:
        return None

    global _start_monotonic
    current = _now() if now is None else now
    if _start_monotonic is None:
        _start_monotonic = current
    elapsed = current - _start_monotonic
    fraction = elapsed / budget

    crossed = [t for t in _THRESHOLDS if fraction >= t and t not in _fired]
    if not crossed:
        return None
    threshold = max(crossed)
    for t in crossed:
        _fired.add(t)
    remaining = max(0.0, budget - elapsed)
    return (
        f"[deadline] ~{int(remaining // 60)}m of the run budget remain "
        f"({int(threshold * 100)}% elapsed). If you already have a working fix "
        "design, IMPLEMENT it now and verify; refine only with time left over. "
        "Do not start broad new explorations."
    )
