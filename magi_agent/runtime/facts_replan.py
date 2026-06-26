"""In-context facts-survey replanning — pure logic (HAL/smolagents-inspired).

Long multi-step tasks drift when intermediate findings live only in scattered
tool outputs. smolagents' ``planning_interval`` (used by HAL's GAIA generalist)
shows that periodically asking the model to consolidate state *in the same
loop* — a facts survey ("facts given / learned / still to look up / to derive")
plus a refreshed plan — beats decomposing orchestration for this purpose.

This module is the pure half of that mechanism: a config dataclass, the env
parser, the injection-due decision, and the survey message builder. It mirrors
:mod:`magi_agent.runtime.goal_nudge` — import-clean, no ADK imports — and is
consumed by :class:`magi_agent.adk_bridge.facts_replan_control.FactsReplanControl`
at the live ADK ``on_before_model`` seam.

Fact-schema reuse boundary
--------------------------
The survey section names reuse the :class:`~magi_agent.recipes.ledger_task.LedgerFactKind`
*vocabulary* (known fact / working guess / open question). We deliberately do
NOT construct ``LedgerFact`` objects and do NOT parse the model's survey text:
the ledger contract's public-text validators reject URLs and source references
by design, so free-text survey output cannot round-trip through those models.
Structured fact extraction is a follow-up, not this module.

Flags (registered in ``config.flags``; default OFF):

- ``MAGI_FACTS_REPLAN_ENABLED`` — master gate (strict truthy opt-in).
- ``MAGI_FACTS_REPLAN_INTERVAL`` — model iterations between surveys (default 4).
- ``MAGI_FACTS_REPLAN_MAX_PER_TURN`` — survey cap per (session, turn) (default 5).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

MAGI_FACTS_REPLAN_ENABLED_ENV = "MAGI_FACTS_REPLAN_ENABLED"
MAGI_FACTS_REPLAN_INTERVAL_ENV = "MAGI_FACTS_REPLAN_INTERVAL"
MAGI_FACTS_REPLAN_MAX_PER_TURN_ENV = "MAGI_FACTS_REPLAN_MAX_PER_TURN"


@dataclass(frozen=True)
class FactsReplanConfig:
    """Configuration for interval-based facts-survey replanning.

    Parameters
    ----------
    interval:
        Model iterations between surveys (>= 1). With the smolagents step
        definition (one step = one LLM call + its tool calls), counting at
        ``on_before_model`` means ``model_calls - 1`` equals executed working
        steps, so ``interval=4`` injects before model calls 5, 9, 13, ...
    max_surveys_per_turn:
        Hard cap on injected surveys per (session_id, turn_id) (>= 1).
    max_tracked_turns:
        FIFO cap on per-turn counter state entries (memory bound).
    """

    interval: int = 4
    max_surveys_per_turn: int = 5
    max_tracked_turns: int = 128


def should_inject_survey(
    *,
    model_calls: int,
    interval: int,
    surveys_used: int,
    max_surveys: int,
) -> bool:
    """Return ``True`` iff a survey instruction is due before this model call.

    True iff ``model_calls > 1``, ``surveys_used < max_surveys``, and
    ``(model_calls - 1) % interval == 0``. ``interval=4`` → inject before model
    calls 5, 9, 13, ... (after working steps 4, 8, 12). Single-call chat turns
    never reach ``model_calls > 1`` and are untouched.
    """
    if interval < 1 or model_calls <= 1:
        return False
    if surveys_used >= max_surveys:
        return False
    return (model_calls - 1) % interval == 0


def build_survey_message(
    *,
    steps_so_far: int,
    survey_index: int,
    max_surveys: int,
) -> str:
    """Render the survey instruction injected as a user-role message.

    A static template interpolating only integers — nothing user- or
    env-derived enters the injected text.
    """
    return (
        "Pause before your next action and write a facts survey:\n"
        "1. Facts GIVEN in the task.\n"
        "2. Facts LEARNED so far — note where each came from; mark unverified "
        "ones as working guesses.\n"
        "3. Facts still to LOOK UP (open questions).\n"
        "4. Facts to DERIVE or compute.\n"
        "Then write a refreshed short plan covering the REMAINING work only.\n"
        "This survey supersedes any earlier plan or survey — do not restate "
        "or defend earlier plans; rebuild from evidence so far.\n"
        f"You have used {steps_so_far} working steps; this is consolidation "
        f"{survey_index} of at most {max_surveys}.\n"
        "Then continue with the next concrete action."
    )


def parse_facts_replan_env(
    env: Mapping[str, str] | None = None,
) -> FactsReplanConfig | None:
    """Build a :class:`FactsReplanConfig` from the environment, or ``None`` when OFF.

    Returns ``None`` unless ``MAGI_FACTS_REPLAN_ENABLED`` is strict-truthy
    (``"1"/"true"/"yes"/"on"``, via :func:`magi_agent.config.env.is_facts_replan_enabled`).
    Invalid / non-integer interval or max values fall back to the field default
    (the ``goal_nudge_wiring`` fallback convention — never raise). An explicit
    ``interval <= 0`` or ``max <= 0`` is treated as OFF (returns ``None``).
    """
    # Lazy import: config.env re-exports this function, so a top-level import
    # would be circular.
    from magi_agent.config.env import is_facts_replan_enabled  # noqa: PLC0415

    source = os.environ if env is None else env
    if not is_facts_replan_enabled(source):
        return None

    # I-1: route both interval knobs through the typed flag registry.
    # ``flag_int`` returns the registered ``FlagSpec`` default for unset
    # AND malformed values; both registered defaults already match the
    # ``FactsReplanConfig`` dataclass defaults (4 / 5) so the prior
    # ``_int_or_default`` shape is byte-identical.
    from magi_agent.config.flags import flag_int  # noqa: PLC0415

    interval = flag_int(MAGI_FACTS_REPLAN_INTERVAL_ENV, env=source)
    max_surveys = flag_int(MAGI_FACTS_REPLAN_MAX_PER_TURN_ENV, env=source)
    if interval <= 0 or max_surveys <= 0:
        return None
    return FactsReplanConfig(interval=interval, max_surveys_per_turn=max_surveys)


__all__ = [
    "FactsReplanConfig",
    "MAGI_FACTS_REPLAN_ENABLED_ENV",
    "MAGI_FACTS_REPLAN_INTERVAL_ENV",
    "MAGI_FACTS_REPLAN_MAX_PER_TURN_ENV",
    "build_survey_message",
    "parse_facts_replan_env",
    "should_inject_survey",
]
