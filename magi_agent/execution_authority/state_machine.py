from __future__ import annotations

from enum import StrEnum


class ActionState(StrEnum):
    PROPOSED = "proposed"
    DENIED = "denied"
    AUTHORIZED = "authorized"
    PREPARED = "prepared"
    EXECUTING = "executing"
    OBSERVED = "observed"
    COMMITTED = "committed"
    ABORTED = "aborted"
    PARTIAL = "partial"
    UNKNOWN = "unknown"
    VERIFIED = "verified"


class VerificationState(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    PASSED = "passed"
    FAILED = "failed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    STALE = "stale"


class RequirementState(StrEnum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SUPERSEDED = "superseded"


class CompletionStatus(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class CompletionEpochState(StrEnum):
    OPEN = "open"
    SEALING = "sealing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class DependencyStatus(StrEnum):
    CLEAN = "clean"
    FINDING = "finding"
    OBSERVED = "observed"
    NOT_RUN = "not_run"
    UNAVAILABLE = "unavailable"
    INCOMPATIBLE = "incompatible"


class UserDecisionState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    REVOKED = "revoked"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    CONSUMED = "consumed"


class InvalidTransition(ValueError):
    """Raised when a state-machine transition is not legal."""


_ACTION_TRANSITIONS: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PROPOSED: frozenset({ActionState.AUTHORIZED, ActionState.DENIED}),
    ActionState.DENIED: frozenset(),
    ActionState.AUTHORIZED: frozenset({ActionState.PREPARED, ActionState.ABORTED}),
    ActionState.PREPARED: frozenset({ActionState.EXECUTING, ActionState.ABORTED}),
    ActionState.EXECUTING: frozenset({ActionState.OBSERVED}),
    ActionState.OBSERVED: frozenset(
        {
            ActionState.COMMITTED,
            ActionState.ABORTED,
            ActionState.PARTIAL,
            ActionState.UNKNOWN,
        }
    ),
    ActionState.COMMITTED: frozenset({ActionState.VERIFIED}),
    ActionState.ABORTED: frozenset(),
    ActionState.PARTIAL: frozenset(),
    ActionState.UNKNOWN: frozenset(),
    ActionState.VERIFIED: frozenset(),
}

_USER_DECISION_TRANSITIONS: dict[UserDecisionState, frozenset[UserDecisionState]] = {
    UserDecisionState.PENDING: frozenset(
        {
            UserDecisionState.APPROVED,
            UserDecisionState.DENIED,
            UserDecisionState.EXPIRED,
            UserDecisionState.INVALIDATED,
        }
    ),
    UserDecisionState.APPROVED: frozenset(
        {
            UserDecisionState.REVOKED,
            UserDecisionState.EXPIRED,
            UserDecisionState.INVALIDATED,
            UserDecisionState.CONSUMED,
        }
    ),
    UserDecisionState.DENIED: frozenset(),
    UserDecisionState.REVOKED: frozenset(),
    UserDecisionState.EXPIRED: frozenset(),
    UserDecisionState.INVALIDATED: frozenset(),
    UserDecisionState.CONSUMED: frozenset(),
}


def transition_action(current: ActionState, target: ActionState) -> ActionState:
    if type(current) is not ActionState or type(target) is not ActionState:
        raise InvalidTransition(
            f"invalid action transition values: {current!r} -> {target!r}"
        )
    if target not in _ACTION_TRANSITIONS[current]:
        raise InvalidTransition(
            f"illegal action transition: {current.value} -> {target.value}"
        )
    return target


def transition_user_decision(
    current: UserDecisionState,
    target: UserDecisionState,
) -> UserDecisionState:
    if type(current) is not UserDecisionState or type(target) is not UserDecisionState:
        raise InvalidTransition(
            f"invalid user-decision transition values: {current!r} -> {target!r}"
        )
    if target not in _USER_DECISION_TRANSITIONS[current]:
        raise InvalidTransition(
            f"illegal user-decision transition: {current.value} -> {target.value}"
        )
    return target
