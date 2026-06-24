"""Billing / spend-governance — reference contract (NOT wired into the OSS runtime).

This package ships a fail-closed, redaction-correct, fully-tested specification
of the spend-cap / quota / reservation FSM that a hosted control plane would
implement on top of the OSS runtime. It is intentionally not invoked by any
live turn-loop or transport path in the OSS build:

* :class:`magi_agent.harness.goal_loop_control.SpendCapProbe` is the
  ``Protocol`` seam the OSS runtime calls. A hosted runtime injects a concrete
  implementation that delegates into the symbols re-exported here (or into an
  equivalent service); the OSS build injects nothing, and the loop control
  short-circuits to "not capped".
* :func:`reserve_spend` / :func:`commit_spend_reservation` /
  :func:`release_spend_reservation` / :func:`evaluate_quota` have **no** live
  call site in the OSS package — confirmed by grep. The unit tests under
  ``tests/billing/`` document the contract, not a wired runtime behaviour.

H-6 (P2 dead-code workstream) makes this dormancy unmistakable so a future
reader cannot mistake the package for live OSS spend governance. The opposite
move (wiring ``SpendCapProbe`` to ``evaluate_quota`` behind
``MAGI_SPEND_GUARD_ENABLED``) requires an explicit decision from the
maintainer per the AGENTS.md "ask before billing changes" guard.

The :data:`REFERENCE_CONTRACT` flag below is the machine-readable form of this
docstring: meta-tests assert it is ``True`` so the dormancy is intentional
rather than accidental dead code.
"""

from .quota import (
    QuotaDecision,
    QuotaEvaluationConfig,
    QuotaLimit,
    QuotaRequest,
    evaluate_quota,
)
from .spend_guard import (
    SpendAmount,
    SpendCommitRequest,
    SpendReleaseRequest,
    SpendReservationReceipt,
    SpendReservationRequest,
    commit_spend_reservation,
    release_spend_reservation,
    reserve_spend,
)

#: H-6: declares this package as a *reference contract* (a fail-closed
#: specification of a hosted control plane's spend FSM), not a wired
#: OSS-runtime spend-governance layer. Flip to ``False`` only in the same PR
#: that introduces a concrete ``SpendCapProbe`` implementation.
REFERENCE_CONTRACT: bool = True

__all__ = [
    "REFERENCE_CONTRACT",
    "QuotaDecision",
    "QuotaEvaluationConfig",
    "QuotaLimit",
    "QuotaRequest",
    "SpendAmount",
    "SpendCommitRequest",
    "SpendReleaseRequest",
    "SpendReservationReceipt",
    "SpendReservationRequest",
    "commit_spend_reservation",
    "evaluate_quota",
    "release_spend_reservation",
    "reserve_spend",
]
