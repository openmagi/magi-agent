"""LifecycleLlmCallAuditControl — PR-F-LIFE2 per-LLM-call audit fan-out.

ADK ``on_before_model`` + ``on_after_model`` adapter for
:mod:`magi_agent.customize.lifecycle_audit`. This is the BEFORE_LLM_CALL /
AFTER_LLM_CALL boundary discovery anchored to: the canonical place to fire
an audit on every LLM call within a turn is the ADK callback boundary
inside the runner stream, NOT the ``cli/engine.py`` entry/exit (which fires
once per *turn*, not per call). Wiring here is the production seam every
governed turn flows through.

Per-turn critic cost ceiling
----------------------------
Every emit fires on the hot per-call path so the OFF contract MUST be
byte-identical and zero-cost. Two layers protect cost:

1. The master flag :func:`llm_call_hooks_enabled` is checked FIRST on each
   call. When OFF the control performs no further work (no policy load, no
   criterion judge), the OFF path is byte-identical to the pre-PR runtime.
2. When the flag is ON the control maintains a per-(session_id, turn_id)
   counter capped at ``MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET`` (default 3).
   The counter is SHARED across before/after so the combined invocations
   never exceed the per-turn cap; each successful audit costs 1. When the
   budget is exhausted the fan-out short-circuits to a
   ``status="budget_exhausted"`` skip record (recorded once per call) and
   the criterion judge is NOT invoked — see
   :func:`magi_agent.customize.lifecycle_audit.run_before_llm_call_audit`.

Per-turn state lifecycle mirrors
:class:`magi_agent.adk_bridge.facts_replan_control.FactsReplanControl`
exactly (FIFO-bounded OrderedDict; per logical turn so goal-nudge /
continuation re-invocations reuse the same budget). The wires never
mutate ``llm_request`` / ``llm_response`` (audit-only contract); a
``try/except`` envelope around the entire body preserves the fail-soft
control-plane contract (an audit failure must never abort the model call).
"""

from __future__ import annotations

import logging
import os
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from google.adk.models import LlmResponse

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    _latest_event_invocation_id,
    _non_empty_str,
)

LLM_CALL_AUDIT_CONTROL_NAME = "magi_lifecycle_llm_call_audit"

# Env knob for the per-turn critic budget. Read raw (no flag_int wrapper) so
# operators can override per-turn cheaply without a new typed reader.
LLM_CALL_AUDIT_BUDGET_ENV = "MAGI_CUSTOMIZE_LLM_CALL_AUDIT_BUDGET"
DEFAULT_LLM_CALL_AUDIT_BUDGET = 3
# FIFO cap on tracked turns so a long-running session does not grow the
# state map unboundedly. Matches the facts-replan default.
MAX_TRACKED_TURNS_DEFAULT = 128

logger = logging.getLogger(__name__)


@dataclass
class _TurnBudgetState:
    """Mutable per-(session, turn) critic budget counter."""

    remaining: int


def _build_policy_blocked_llm_response(*, reason: str) -> LlmResponse:
    """Return a synthetic policy-blocked LlmResponse.

    Used by lifecycle gates (e.g. F-LIFE4b on_session_start) to short-circuit
    the LLM call when an llm_criterion verdict comes back as ``block``. The
    response carries the block reason in ``custom_metadata`` so downstream
    telemetry / audit can attribute the block; no model tokens are charged.
    """
    return LlmResponse(
        custom_metadata={
            "policy_blocked": True,
            "reason": reason,
        },
    )


def _parse_budget(env: Mapping[str, str] | None = None) -> int:
    """Resolve the per-turn critic budget from the environment.

    Read raw with a fail-open ``int()`` conversion: an unparsable / negative
    value snaps to the default so a malformed env never widens the budget.
    """
    source = env if env is not None else os.environ
    raw = source.get(LLM_CALL_AUDIT_BUDGET_ENV)
    if raw is None or not str(raw).strip():
        return DEFAULT_LLM_CALL_AUDIT_BUDGET
    try:
        parsed = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_LLM_CALL_AUDIT_BUDGET
    if parsed < 0:
        return DEFAULT_LLM_CALL_AUDIT_BUDGET
    return parsed


def _extract_response_text(llm_response: Any) -> str:
    """Best-effort text extraction from an ADK ``LlmResponse``.

    Returns the empty string when no text can be found; the audit fan-out
    then short-circuits to ``status="skipped"`` per the
    :mod:`magi_agent.customize.lifecycle_audit` ``draft_text`` empty guard.
    Fully fail-open: any extraction error returns ``""``.
    """
    if llm_response is None:
        return ""
    # ADK LlmResponse exposes a ``content`` with ``parts`` (list[Part]).
    try:
        content = getattr(llm_response, "content", None)
        if content is None and isinstance(llm_response, dict):
            content = llm_response.get("content")
        parts = getattr(content, "parts", None) if content is not None else None
        if parts is None and isinstance(content, dict):
            parts = content.get("parts")
        if not parts:
            return ""
        chunks: list[str] = []
        for part in parts:
            text = getattr(part, "text", None)
            if text is None and isinstance(part, dict):
                text = part.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
        return "".join(chunks)
    except Exception:
        return ""


def _extract_request_text(llm_request: Any) -> str:
    """Best-effort text extraction from an ADK ``LlmRequest`` for the
    before-call audit.

    The "draft" at BEFORE_LLM_CALL is the outgoing prompt assembly. We use
    the most-recent user-role content chunk (matches the audit semantic —
    "what is the model about to be asked"). Empty string on any failure.
    """
    if llm_request is None:
        return ""
    try:
        contents = getattr(llm_request, "contents", None)
        if contents is None and isinstance(llm_request, dict):
            contents = llm_request.get("contents")
        if not contents:
            return ""
        # Walk backwards to the most recent user-role content.
        for entry in reversed(contents):
            role = getattr(entry, "role", None)
            if role is None and isinstance(entry, dict):
                role = entry.get("role")
            if role != "user":
                continue
            parts = getattr(entry, "parts", None)
            if parts is None and isinstance(entry, dict):
                parts = entry.get("parts") or entry.get("content")
            if isinstance(parts, str):
                return parts
            if not parts:
                continue
            chunks: list[str] = []
            for part in parts:
                text = getattr(part, "text", None)
                if text is None and isinstance(part, dict):
                    text = part.get("text") or part.get("content")
                if isinstance(text, str) and text:
                    chunks.append(text)
            joined = "".join(chunks)
            if joined:
                return joined
        return ""
    except Exception:
        return ""


def _build_critic_factory() -> Any | None:
    """Build the Haiku-class critic model factory used by the per-call
    audits. Mirrors the helper in
    :mod:`magi_agent.runtime.governed_turn._build_lifecycle_critic_factory`
    so both turn-boundary and per-call fan-outs share the same critic.
    Returns ``None`` (the audit then records ``status="skipped"`` with
    reason ``no critic model available``) on any import / build failure.
    """
    try:
        from magi_agent.cli.wiring import (  # noqa: PLC0415
            _build_criterion_model_factory,
        )

        return _build_criterion_model_factory()
    except Exception:
        return None


class LifecycleLlmCallAuditControl(BaseLoopControl):
    """Per-LLM-call audit fan-out for ``before_llm_call`` + ``after_llm_call``.

    Maintains a per-(session_id, turn_id) critic budget initialised from
    :data:`LLM_CALL_AUDIT_BUDGET_ENV` (default ``3``) and shared across
    before/after invocations. Audit-only: never mutates ``llm_request`` /
    ``llm_response`` and always returns ``None`` to the surrounding
    ``ControlPlane._before_model`` / ``_after_model`` dispatch.
    """

    name = LLM_CALL_AUDIT_CONTROL_NAME

    def __init__(
        self,
        *,
        budget_default: int = DEFAULT_LLM_CALL_AUDIT_BUDGET,
        max_tracked_turns: int = MAX_TRACKED_TURNS_DEFAULT,
    ) -> None:
        self._budget_default = budget_default
        self._max_tracked_turns = max_tracked_turns
        self._turns: OrderedDict[tuple[str, str], _TurnBudgetState] = OrderedDict()

    def _state_for(self, session_id: str, turn_id: str) -> _TurnBudgetState:
        key = (session_id, turn_id)
        state = self._turns.get(key)
        if state is None:
            # Initialise budget from the env on FIRST observation of this
            # logical turn so an operator override applied mid-session
            # takes effect on the next turn rather than the lifetime of
            # the process.
            state = _TurnBudgetState(remaining=_parse_budget())
            self._turns[key] = state
            while len(self._turns) > self._max_tracked_turns:
                self._turns.popitem(last=False)
        return state

    @staticmethod
    def _resolve_identity(
        callback_context: Any,
    ) -> tuple[str | None, str | None]:
        """Resolve ``(session_id, turn_id)`` off the ADK callback context.

        Identical to the facts-replan resolver — falls back to the latest
        event's invocation_id when ``callback_context.invocation_id`` is
        unavailable. Returns ``(None, None)`` on any extraction failure.
        """
        try:
            session = getattr(callback_context, "session", None)
            session_id = _non_empty_str(getattr(session, "id", None))
            turn_id = _non_empty_str(getattr(callback_context, "invocation_id", None))
            if turn_id is None:
                turn_id = _latest_event_invocation_id(session)
            return session_id, turn_id
        except Exception:
            return None, None

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> None:
        # Fast OFF-path: triple-gate check FIRST so the per-call cost when
        # the master flag is OFF is one helper call + one comparison. No
        # policy load, no factory build, no audit work.
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            llm_call_hooks_enabled,
            run_before_llm_call_audit,
        )

        try:
            if not llm_call_hooks_enabled():
                return None
            session_id, turn_id = self._resolve_identity(callback_context)
            if session_id is None or turn_id is None:
                return None
            state = self._state_for(session_id, turn_id)
            remaining_before = state.remaining
            prompt_text = _extract_request_text(llm_request)
            # Lazy-build the critic factory only when the budget allows an
            # actual critic invocation. When remaining_before <= 0, the audit
            # helper short-circuits to a budget_exhausted record without ever
            # touching the factory, so paying for the (lazy import +
            # litellm/provider resolution) construction would be wasted work
            # on the hot per-call path.
            factory = _build_critic_factory() if remaining_before > 0 else None
            audits = await run_before_llm_call_audit(
                prompt_text=prompt_text,
                model_factory=factory,
                critic_budget_remaining=remaining_before,
            )
            # Only decrement when the fan-out actually invoked the critic
            # (status="evaluated" / "error"). A budget_exhausted skip or an
            # empty audit list (flag off / no rules / empty prompt skip)
            # costs zero budget so the operator's quota tracks real critic
            # invocations.
            for audit in audits:
                status = audit.get("status") if isinstance(audit, dict) else None
                if status in {"evaluated", "error"}:
                    state.remaining -= 1
                    if state.remaining < 0:
                        state.remaining = 0
        except Exception:
            logger.debug(
                "lifecycle-llm-call before_model audit failed; skipping",
                exc_info=True,
            )
        return None

    async def on_after_model(
        self,
        *,
        callback_context: Any,
        llm_response: Any,
    ) -> None:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            llm_call_hooks_enabled,
            run_after_llm_call_audit,
        )

        try:
            if not llm_call_hooks_enabled():
                return None
            session_id, turn_id = self._resolve_identity(callback_context)
            if session_id is None or turn_id is None:
                return None
            state = self._state_for(session_id, turn_id)
            remaining_before = state.remaining
            draft_text = _extract_response_text(llm_response)
            # Lazy-build the critic factory only when budget allows; see the
            # symmetric note in on_before_model.
            factory = _build_critic_factory() if remaining_before > 0 else None
            audits = await run_after_llm_call_audit(
                draft_text=draft_text,
                model_factory=factory,
                critic_budget_remaining=remaining_before,
            )
            for audit in audits:
                status = audit.get("status") if isinstance(audit, dict) else None
                if status in {"evaluated", "error"}:
                    state.remaining -= 1
                    if state.remaining < 0:
                        state.remaining = 0
        except Exception:
            logger.debug(
                "lifecycle-llm-call after_model audit failed; skipping",
                exc_info=True,
            )
        return None


def build_lifecycle_llm_call_control(
    env: Mapping[str, str] | None = None,
) -> LifecycleLlmCallAuditControl | None:
    """Build the control when the F-LIFE2 master flag is ON; else ``None``.

    Returning ``None`` keeps the control plane byte-identical to today for
    OFF callers (the build helper in
    :mod:`magi_agent.adk_bridge.control_plane` skips registration). The
    per-call helper :func:`llm_call_hooks_enabled` is also checked inside
    the ``on_before_model`` / ``on_after_model`` paths so a runtime flip
    after registration also short-circuits cleanly.
    """
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            llm_call_hooks_enabled,
        )
    except Exception:
        return None
    if not llm_call_hooks_enabled(env=dict(env) if env is not None else None):
        return None
    return LifecycleLlmCallAuditControl(budget_default=_parse_budget(env))


__all__ = [
    "DEFAULT_LLM_CALL_AUDIT_BUDGET",
    "LLM_CALL_AUDIT_BUDGET_ENV",
    "LLM_CALL_AUDIT_CONTROL_NAME",
    "LifecycleLlmCallAuditControl",
    "build_lifecycle_llm_call_control",
]
