"""No-tool finalizer: never end a turn blank after a tool loop (B9 backstop).

A reasoning model can run a tool loop where tools fail (or produce only
function-response events) and then STOP with no final answer text. The turn
commits blank and the user sees no answer. The hosted gate5b boundary already
guards this with ``_run_no_tool_finalizer`` (one tool-less pass that forces a
final answer from the session evidence). The local/governed ``MagiEngineDriver``
had no equivalent, so this module ports the decision + message into a pure,
model-free, env-free helper the driver wires at its post-loop seam.

This is the B9 gap deferred in the turn-engine convergence work: because local
serve and the governed hosted flip run the SAME driver, one driver-owned
finalizer fixes both.

``config=None`` or ``enabled=False`` makes ``should_run_no_tool_finalizer``
return ``False`` so the engine's control flow stays byte-identical when the flag
is OFF (fail-open by construction: the helpers are pure and total).

Divergences from the hosted implementation (deliberate, see the design doc
docs/plans/2026-07-07-local-no-tool-finalizer-design.md):

* The driver owns a live runner, not agent kwargs, so it cannot build a
  tool-less Agent per turn. The instruction content is folded into the single
  finalizer user message, and the driver enforces tool-less execution with a
  deny-all before_tool_callback overlay.
* Bounded by a driver-side event allowance (default 64) instead of a
  RunConfig(max_llm_calls), because external run_config is blocked at the
  adapter (B5 anti-side-channel).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "NoToolFinalizerConfig",
    "should_run_no_tool_finalizer",
    "build_no_tool_finalizer_message",
]

# Generous default; hosted used an 8-event cap plus max_llm_calls=2. The driver
# adds this to (not resets) its own event budget so the one finalizer pass is
# not immediately cut, mirroring the grace allowance pattern.
_DEFAULT_EVENT_ALLOWANCE = 64


@dataclass(frozen=True)
class NoToolFinalizerConfig:
    """Resolved finalizer policy. ``enabled=False`` makes the seam inert."""

    enabled: bool = True
    event_allowance: int = _DEFAULT_EVENT_ALLOWANCE


def should_run_no_tool_finalizer(
    config: "NoToolFinalizerConfig | None",
    *,
    emitted_text: str,
    recoveries_used: int,
) -> bool:
    """Return True when the driver should run one tool-less finalizer pass.

    Fires only on a turn that is about to commit blank: no visible answer text
    was emitted this turn, and empty-response recovery did not already own the
    blank turn. ``config=None`` / disabled / a non-blank turn / a
    recovery-handled turn all return False (byte-identical OFF path).
    """
    if config is None or not config.enabled:
        return False
    if emitted_text.strip():
        return False
    if recoveries_used > 0:
        # Empty-response recovery (operator opted in) owns the blank turn,
        # including its escalation notice; the finalizer defers to it.
        return False
    return True


def build_no_tool_finalizer_message() -> str:
    """The single user-role message driving the tool-less finalizer pass.

    Folds the hosted finalizer's instruction and prompt into one message,
    because the driver cannot swap the live agent's instruction per turn. Mirror
    of ``_no_tool_finalizer_instruction`` + ``_no_tool_finalizer_message`` in the
    gate5b boundary.
    """
    return (
        "This turn ended with tool or reasoning activity but no text answer. "
        "Produce the final user-visible answer now, using only the conversation "
        "and the tool or function response events already present in this "
        "session. Do not call any tools in this pass. If the gathered evidence "
        "is insufficient, say plainly what is missing and what you did instead "
        "of calling more tools."
    )
