"""FactsReplanControl — interval-based facts-survey injection (default-OFF).

ADK ``on_before_model`` adapter for :mod:`magi_agent.runtime.facts_replan`.
Every model iteration of a turn increments a per-(session_id, turn_id) counter;
when a survey is due (every ``interval`` iterations after the first, capped at
``max_surveys_per_turn``) the control appends **one user-role survey
instruction** to ``llm_request.contents`` — the exact injection mechanism of
:class:`~magi_agent.adk_bridge.control_plane.GaConstraintReinjectionControl`
(genai ``types.Content`` first, plain-dict fallback, plus the
``isinstance(llm_request, dict)`` test-fake branch). Tools are **never**
cleared (unlike the max-steps brake): the survey adds context, never removes
capability.

Registered by ``build_default_plane`` (step 7) only when
``MAGI_FACTS_REPLAN_ENABLED`` is strict-truthy, so all existing callers are
byte-identical with the flag unset.

Fail-soft: ``ControlPlane._before_model`` has no per-control try/except — a
raise would abort the model call. ``on_before_model`` therefore wraps its
entire body in ``try/except Exception``; any failure (unresolvable
session/turn, mutation error) skips the injection, never the turn.

Same-runner constraint: no ``StepExecutor``, no worker handoffs, no
``meta_orchestration`` or ``recipes.ledger_orchestrator`` imports — this is
the in-context (non-decomposing) replanning form.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    _latest_event_invocation_id,
    _non_empty_str,
)
from magi_agent.runtime.facts_replan import (
    FactsReplanConfig,
    build_survey_message,
    parse_facts_replan_env,
    should_inject_survey,
)

FACTS_REPLAN_CONTROL_NAME = "magi_facts_replan"

logger = logging.getLogger(__name__)


@dataclass
class _TurnState:
    """Mutable per-(session, turn) counters."""

    model_calls: int = 0
    surveys_used: int = 0


class FactsReplanControl(BaseLoopControl):
    """Inject a periodic in-context facts survey into the live model loop."""

    name = FACTS_REPLAN_CONTROL_NAME

    def __init__(self, config: FactsReplanConfig) -> None:
        self._config = config
        # FIFO-bounded per-turn state: (session_id, turn_id) -> _TurnState.
        # Re-invocations within a turn (goal-nudge / continuation / recovery)
        # reuse the same turn_id, so the consolidation budget is per logical turn.
        self._turns: OrderedDict[tuple[str, str], _TurnState] = OrderedDict()

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        # P5 pattern: the ADK hook does only the privileged part — resolving
        # the active (session_id, turn_id) from the callback context's
        # session/event tree — then delegates the decision body to the
        # typed-context entry point with the pre-resolved identity. Fail-soft
        # is preserved on both halves: a resolution failure skips the survey,
        # never the turn.
        from magi_agent.packs.context import ControlPlaneContext  # noqa: PLC0415

        try:
            session = getattr(callback_context, "session", None)
            session_id = _non_empty_str(getattr(session, "id", None))
            turn_id = _non_empty_str(getattr(callback_context, "invocation_id", None))
            if turn_id is None:
                turn_id = _latest_event_invocation_id(session)
        except Exception:
            logger.debug(
                "facts-replan on_before_model failed; skipping survey injection",
                exc_info=True,
            )
            return None
        if session_id is None or turn_id is None:
            return None

        return await self.apply_before_model(
            ControlPlaneContext.minimal(),
            llm_request=llm_request,
            session_id=session_id,
            turn_id=turn_id,
        )

    async def apply_before_model(
        self,
        ctx: Any,
        *,
        llm_request: Any,
        session_id: str,
        turn_id: str,
    ) -> None:
        """Typed-context entry point (P5; template: MaxStepsBrakeControl).

        ``ctx`` is a :class:`ControlPlaneContext`; like the max-steps brake this
        control needs no seam capability off it — the decision reads only the
        outgoing request plus the pre-resolved turn identity the runtime
        supplies (so the control never traverses ``session.events`` itself). A
        user pack authoring an equivalent survey control receives the same
        context and identity. Behavior is byte-identical to the pre-migration
        body, including the fail-soft contract.

        The per-(session, turn) counters intentionally stay on the control
        (NOT ``ctx.per_invocation``): their lifecycle is per *logical turn*
        with the config-bounded FIFO — a clear-on-after_run sweep would reset
        the consolidation budget across goal-nudge/continuation/recovery
        re-invocations that reuse the same turn_id.
        """
        _ = ctx
        try:
            state = self._state_for(session_id, turn_id)
            state.model_calls += 1

            if not should_inject_survey(
                model_calls=state.model_calls,
                interval=self._config.interval,
                surveys_used=state.surveys_used,
                max_surveys=self._config.max_surveys_per_turn,
            ):
                return None

            message = build_survey_message(
                steps_so_far=state.model_calls - 1,
                survey_index=state.surveys_used + 1,
                max_surveys=self._config.max_surveys_per_turn,
            )
            if self._append_user_message(llm_request, message):
                state.surveys_used += 1
        except Exception:
            logger.debug(
                "facts-replan on_before_model failed; skipping survey injection",
                exc_info=True,
            )
        return None

    def _state_for(self, session_id: str, turn_id: str) -> _TurnState:
        key = (session_id, turn_id)
        state = self._turns.get(key)
        if state is None:
            state = _TurnState()
            self._turns[key] = state
            while len(self._turns) > self._config.max_tracked_turns:
                self._turns.popitem(last=False)
        return state

    @staticmethod
    def _append_user_message(llm_request: Any, text: str) -> bool:
        """Append a user-role message; mirror GaConstraintReinjectionControl.

        Tools are deliberately NOT touched.
        """
        contents = getattr(llm_request, "contents", None)
        if isinstance(contents, list):
            try:
                from google.genai import types as _genai_types  # noqa: PLC0415

                contents.append(
                    _genai_types.Content(
                        role="user",
                        parts=[_genai_types.Part(text=text)],
                    )
                )
            except Exception:
                contents.append({"role": "user", "content": text})
            return True
        if isinstance(llm_request, dict):
            llm_request.setdefault("contents", [])
            llm_request["contents"].append({"role": "user", "content": text})
            return True
        return False


def build_facts_replan_control(
    env: Mapping[str, str] | None = None,
) -> FactsReplanControl | None:
    """Build the control from the environment, or ``None`` when OFF.

    ``parse_facts_replan_env(env)`` returning ``None`` (flag unset/false, or an
    explicit non-positive interval/cap) means the caller skips registration.
    """
    config = parse_facts_replan_env(env)
    if config is None:
        return None
    return FactsReplanControl(config)


__all__ = [
    "FACTS_REPLAN_CONTROL_NAME",
    "FactsReplanControl",
    "build_facts_replan_control",
]
