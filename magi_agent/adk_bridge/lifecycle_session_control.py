"""LifecycleSessionControl — PR-F-LIFE4b first-fire-per-session adapter.

ADK ``on_before_model`` adapter for the F-LIFE4b ``on_session_start`` slot.
The signal source is the FIRST model call observed for a given
``session_id`` within this process — subsequent model calls within the
same session do NOT re-fire (the F-LIFE3 ``on_task_checkpoint`` /
F-LIFE2 ``before_llm_call`` slots already cover per-turn / per-call
audit needs).

State bookkeeping
-----------------
The control maintains a FIFO-bounded :class:`OrderedDict` keyed by
``session_id`` — first observation populates the set and triggers the
audit fan-out + gate consult; subsequent observations short-circuit at
the membership check. Mirror of the F-LIFE2 per-(session, turn) budget
bookkeeping shape so the eviction strategy (drop the oldest entry when
the cap is exceeded) is consistent across the lifecycle controls.

Per-session OFF-path contract
-----------------------------
Every emit fires on the per-call boundary so the OFF path MUST be
byte-identical and zero-cost. Two layers protect cost:

1. The master flag :func:`session_task_emitters_enabled` is checked
   FIRST on each call. When OFF the control performs no further work
   (no OrderedDict bookkeeping, no policy load, no criterion judge);
   the OFF path is byte-identical to the pre-PR runtime.
2. When the flag is ON the membership check short-circuits subsequent
   model calls for the same session before any policy load.

The wire never mutates ``llm_request`` (audit-only contract) except on
a block-action gate verdict, where it returns a synthetic policy-
blocked ``LlmResponse`` so the model call is honestly suppressed
(mirrors :mod:`magi_agent.adk_bridge.lifecycle_llm_call_control`'s
block surface). A ``try/except`` envelope around the body preserves
the fail-soft control-plane contract.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Mapping
from typing import Any

from magi_agent.adk_bridge.control_plane import (
    BaseLoopControl,
    _non_empty_str,
)
from magi_agent.adk_bridge.lifecycle_llm_call_control import (
    _build_policy_blocked_llm_response,
    _extract_request_text,
)

SESSION_LIFECYCLE_CONTROL_NAME = "magi_lifecycle_session_start"

# FIFO cap on tracked sessions so a long-running process does not grow
# the state map unboundedly. Matches the F-LIFE2 per-turn budget cap.
MAX_TRACKED_SESSIONS_DEFAULT = 128

logger = logging.getLogger(__name__)


def _build_critic_factory() -> Any | None:
    """Build the Haiku-class critic model factory used by the session-
    start audit. Mirrors the helper in
    :mod:`magi_agent.adk_bridge.lifecycle_llm_call_control` so both
    per-call and session-start fan-outs share the same critic. Returns
    ``None`` (the audit then records ``status="skipped"`` with reason
    ``no critic model available``) on any import / build failure.
    """
    try:
        from magi_agent.cli.wiring import (  # noqa: PLC0415
            _build_criterion_model_factory,
        )

        return _build_criterion_model_factory()
    except Exception:
        return None


class LifecycleSessionControl(BaseLoopControl):
    """First-fire-per-session audit + gate fan-out for ``on_session_start``.

    Maintains a FIFO-bounded :class:`OrderedDict` of session ids seen
    so far in this process. The FIRST observation per session fires the
    audit (and consults the gate); subsequent observations short-circuit
    cheaply at the membership check. Default-OFF byte-identical when the
    master flag is OFF.
    """

    name = SESSION_LIFECYCLE_CONTROL_NAME

    def __init__(
        self,
        *,
        max_tracked_sessions: int = MAX_TRACKED_SESSIONS_DEFAULT,
    ) -> None:
        self._max_tracked_sessions = max_tracked_sessions
        # OrderedDict so popitem(last=False) gives FIFO eviction.
        self._seen: OrderedDict[str, None] = OrderedDict()

    def _note_session(self, session_id: str) -> bool:
        """Returns True iff this is the FIRST observation of session_id.

        Mutates the OrderedDict only on first observation so the steady-
        state per-call cost is one dict membership check.
        """
        if session_id in self._seen:
            return False
        self._seen[session_id] = None
        while len(self._seen) > self._max_tracked_sessions:
            self._seen.popitem(last=False)
        return True

    @staticmethod
    def _resolve_session_id(callback_context: Any) -> str | None:
        """Resolve ``session_id`` off the ADK callback context.

        Mirrors the F-LIFE2 identity resolver but only needs the session
        id (the per-session "seen" set has no per-turn axis). Returns
        ``None`` on any extraction failure so the wire bails fail-open.

        PR-F-LIFE4b review pass: an earlier draft fell back to
        ``invocation_id`` when ``session.id`` was missing, but
        ``invocation_id`` is per-turn — not per-conversation — so the
        OrderedDict "seen" set would have re-fired on every turn rather
        than once per session, silently breaking the at-most-once
        contract. We now return ``None`` instead so the strict semantic
        ("fire once per session") is honored or the audit is skipped.
        In practice ADK populates ``session.id`` on every callback so
        the fallback path is rarely hit.
        """
        try:
            session = getattr(callback_context, "session", None)
            session_id = _non_empty_str(getattr(session, "id", None))
            return session_id
        except Exception:
            return None

    async def on_before_model(
        self,
        *,
        callback_context: Any,
        llm_request: Any,
    ) -> Any:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            derive_gate_verdict_from_audits,
            run_session_start_audit,
            session_task_emitters_enabled,
        )

        try:
            # Fast OFF-path: triple-gate check FIRST so the per-call cost
            # when the master flag is OFF is one helper call + one
            # comparison. No OrderedDict mutation, no policy load.
            if not session_task_emitters_enabled():
                return None
            session_id = self._resolve_session_id(callback_context)
            if session_id is None:
                return None
            # FIRST-fire check. The check + mutation are sequential so a
            # concurrent re-entry into the same session on a second
            # asyncio task could in principle fire twice; the cost ceiling
            # we care about is per-process not per-task, so locking is
            # not worth the contention.
            if not self._note_session(session_id):
                return None
            prompt_text = _extract_request_text(llm_request)
            factory = _build_critic_factory()
            audits = await run_session_start_audit(
                prompt_text=prompt_text,
                session_id=session_id,
                model_factory=factory,
            )
            # Block-action gate derivation reads off the audits we just
            # computed — no second criterion-judge call. On block we
            # return a synthetic policy-blocked LlmResponse so the
            # session-start model call is honestly suppressed (the
            # follow-up turns will not re-fire because the session is
            # already in the "seen" set).
            try:
                gate_verdict = derive_gate_verdict_from_audits(
                    audits,
                    fires_at="on_session_start",
                    allowed_actions=frozenset({"block"}),
                    enabled_fn=session_task_emitters_enabled,
                )
                if gate_verdict == "block":
                    return _build_policy_blocked_llm_response(
                        reason="on_session_start llm_criterion verdict=block",
                    )
            except Exception:
                # Fail-open: gate evaluation errors never block a call.
                pass
        except Exception:
            logger.debug(
                "lifecycle-session before_model audit failed; skipping",
                exc_info=True,
            )
        return None


def build_lifecycle_session_control(
    env: Mapping[str, str] | None = None,
) -> LifecycleSessionControl | None:
    """Build the control when the F-LIFE4b master flag is ON; else ``None``.

    Returning ``None`` keeps the control plane byte-identical to today
    for OFF callers (the build helper in
    :mod:`magi_agent.adk_bridge.control_plane` skips registration). The
    per-call helper :func:`session_task_emitters_enabled` is also
    checked inside the ``on_before_model`` path so a runtime flip after
    registration also short-circuits cleanly.
    """
    try:
        from magi_agent.customize.lifecycle_audit import (  # noqa: PLC0415
            session_task_emitters_enabled,
        )
    except Exception:
        return None
    if not session_task_emitters_enabled(
        env=dict(env) if env is not None else None
    ):
        return None
    return LifecycleSessionControl()


__all__ = [
    "MAX_TRACKED_SESSIONS_DEFAULT",
    "SESSION_LIFECYCLE_CONTROL_NAME",
    "LifecycleSessionControl",
    "build_lifecycle_session_control",
]
