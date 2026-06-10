"""PR4 (cluster 03 C4) — production wiring for the goal-nudge continuation.

The engine ``MagiEngineDriver`` already accepts a ``goal_nudge`` parameter and
implements the ``_drive`` continuation state machine. What was missing is the
*production* path: ``cli.wiring`` never constructed a
:class:`~magi_agent.runtime.goal_nudge.GoalNudge` from the environment, so the
serve/CLI engine always received ``goal_nudge=None`` and the continuation was
reachable only from tests.

This module is the env→``GoalNudge | None`` builder. It is intentionally tiny
and import-clean (no ADK / textual / provider imports) so importing
``cli.wiring`` stays cold-start safe.

Flags (registered in ``config.env``):
- ``MAGI_GOAL_NUDGE_ENABLED`` — master gate, **default OFF** (strict truthy).
- ``MAGI_GOAL_NUDGE_MODE`` — ``"goal"`` (default, conservative) | ``"grind"``.
- ``MAGI_GOAL_NUDGE_MAX`` — integer hard cap on re-invocations (default 3).
- ``MAGI_GOAL_NUDGE_GOAL`` — objective text embedded in the nudge message.

Default mode is ``"goal"`` (verify-once-per-stop) per the open-decision in the
cluster-03 spec — ``"grind"`` (re-nudge every clean stop) is opt-in.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from magi_agent.config.env import is_goal_nudge_enabled
from magi_agent.runtime.goal_nudge import GoalNudge

__all__ = ["build_goal_nudge_from_env"]

_MODE_ENV = "MAGI_GOAL_NUDGE_MODE"
_MAX_ENV = "MAGI_GOAL_NUDGE_MAX"
_GOAL_ENV = "MAGI_GOAL_NUDGE_GOAL"

_DEFAULT_GOAL = "Complete the user's request fully before finishing."


def build_goal_nudge_from_env(
    env: Mapping[str, str] | None = None,
) -> GoalNudge | None:
    """Build a :class:`GoalNudge` from the environment, or ``None`` when OFF.

    Returns ``None`` unless ``MAGI_GOAL_NUDGE_ENABLED`` is a truthy value. When
    enabled, reads the optional mode/max/goal flags and constructs a
    ``GoalNudge``. Invalid ``mode`` falls back to ``"goal"``; an invalid or
    negative ``max`` falls back to the ``GoalNudge`` default.
    """
    source = os.environ if env is None else env
    if not is_goal_nudge_enabled(source):
        return None

    mode = "grind" if (source.get(_MODE_ENV) or "").strip().lower() == "grind" else "goal"

    goal = (source.get(_GOAL_ENV) or "").strip() or _DEFAULT_GOAL

    raw_max = (source.get(_MAX_ENV) or "").strip()
    try:
        max_nudges = int(raw_max)
        if max_nudges < 0:
            raise ValueError
        return GoalNudge(goal=goal, mode=mode, max_nudges=max_nudges)
    except ValueError:
        return GoalNudge(goal=goal, mode=mode)
