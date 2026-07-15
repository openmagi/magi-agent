from __future__ import annotations

from enum import StrEnum

import pytest

from magi_agent.execution_authority.state_machine import (
    ActionState,
    CompletionEpochState,
    CompletionStatus,
    DependencyStatus,
    InvalidTransition,
    RequirementState,
    UserDecisionState,
    VerificationState,
    transition_action,
    transition_user_decision,
)


@pytest.mark.parametrize(
    ("enum_type", "expected_members"),
    (
        (
            ActionState,
            (
                ("PROPOSED", "proposed"),
                ("DENIED", "denied"),
                ("AUTHORIZED", "authorized"),
                ("PREPARED", "prepared"),
                ("EXECUTING", "executing"),
                ("OBSERVED", "observed"),
                ("COMMITTED", "committed"),
                ("ABORTED", "aborted"),
                ("PARTIAL", "partial"),
                ("UNKNOWN", "unknown"),
                ("VERIFIED", "verified"),
            ),
        ),
        (
            VerificationState,
            (
                ("NOT_EVALUATED", "not_evaluated"),
                ("PASSED", "passed"),
                ("FAILED", "failed"),
                ("INSUFFICIENT_EVIDENCE", "insufficient_evidence"),
                ("STALE", "stale"),
            ),
        ),
        (
            RequirementState,
            (
                ("PENDING", "pending"),
                ("SATISFIED", "satisfied"),
                ("UNSATISFIED", "unsatisfied"),
                ("BLOCKED", "blocked"),
                ("INSUFFICIENT_EVIDENCE", "insufficient_evidence"),
                ("SUPERSEDED", "superseded"),
            ),
        ),
        (
            CompletionStatus,
            (
                ("COMPLETE", "complete"),
                ("PARTIAL", "partial"),
                ("BLOCKED", "blocked"),
                ("INSUFFICIENT_EVIDENCE", "insufficient_evidence"),
                ("CANCELLED", "cancelled"),
                ("UNKNOWN", "unknown"),
            ),
        ),
        (
            CompletionEpochState,
            (
                ("OPEN", "open"),
                ("SEALING", "sealing"),
                ("COMPLETE", "complete"),
                ("PARTIAL", "partial"),
                ("BLOCKED", "blocked"),
                ("INSUFFICIENT_EVIDENCE", "insufficient_evidence"),
                ("CANCELLED", "cancelled"),
                ("UNKNOWN", "unknown"),
            ),
        ),
        (
            DependencyStatus,
            (
                ("CLEAN", "clean"),
                ("FINDING", "finding"),
                ("OBSERVED", "observed"),
                ("NOT_RUN", "not_run"),
                ("UNAVAILABLE", "unavailable"),
                ("INCOMPATIBLE", "incompatible"),
            ),
        ),
        (
            UserDecisionState,
            (
                ("PENDING", "pending"),
                ("APPROVED", "approved"),
                ("DENIED", "denied"),
                ("REVOKED", "revoked"),
                ("EXPIRED", "expired"),
                ("INVALIDATED", "invalidated"),
                ("CONSUMED", "consumed"),
            ),
        ),
    ),
)
def test_shared_state_enum_members_are_canonical(
    enum_type: type[StrEnum],
    expected_members: tuple[tuple[str, str], ...],
) -> None:
    assert issubclass(enum_type, StrEnum)
    assert (
        tuple((name, member.value) for name, member in enum_type.__members__.items())
        == expected_members
    )


_LEGAL_ACTION_TRANSITIONS = frozenset(
    {
        (ActionState.PROPOSED, ActionState.AUTHORIZED),
        (ActionState.PROPOSED, ActionState.DENIED),
        (ActionState.AUTHORIZED, ActionState.PREPARED),
        (ActionState.AUTHORIZED, ActionState.ABORTED),
        (ActionState.PREPARED, ActionState.EXECUTING),
        (ActionState.PREPARED, ActionState.ABORTED),
        (ActionState.EXECUTING, ActionState.OBSERVED),
        (ActionState.OBSERVED, ActionState.COMMITTED),
        (ActionState.OBSERVED, ActionState.ABORTED),
        (ActionState.OBSERVED, ActionState.PARTIAL),
        (ActionState.OBSERVED, ActionState.UNKNOWN),
        (ActionState.COMMITTED, ActionState.VERIFIED),
    }
)


@pytest.mark.parametrize(("current", "target"), _LEGAL_ACTION_TRANSITIONS)
def test_legal_action_transition_returns_target(
    current: ActionState,
    target: ActionState,
) -> None:
    assert transition_action(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (current, target)
        for current in ActionState
        for target in ActionState
        if (current, target) not in _LEGAL_ACTION_TRANSITIONS
    ),
)
def test_every_other_action_transition_raises(
    current: ActionState,
    target: ActionState,
) -> None:
    with pytest.raises(InvalidTransition):
        transition_action(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    (
        pytest.param("proposed", ActionState.AUTHORIZED, id="raw-current"),
        pytest.param(
            UserDecisionState.PENDING,
            ActionState.AUTHORIZED,
            id="foreign-enum-current",
        ),
        pytest.param(object(), ActionState.AUTHORIZED, id="object-current"),
        pytest.param(ActionState.PROPOSED, "authorized", id="raw-target"),
        pytest.param(
            ActionState.PROPOSED,
            UserDecisionState.DENIED,
            id="foreign-enum-target",
        ),
        pytest.param(ActionState.PROPOSED, object(), id="object-target"),
    ),
)
def test_action_transition_rejects_malformed_boundary_values(
    current: object,
    target: object,
) -> None:
    with pytest.raises(InvalidTransition):
        transition_action(current, target)  # type: ignore[arg-type]


_LEGAL_USER_DECISION_TRANSITIONS = {
    (UserDecisionState.PENDING, UserDecisionState.APPROVED),
    (UserDecisionState.PENDING, UserDecisionState.DENIED),
    (UserDecisionState.PENDING, UserDecisionState.EXPIRED),
    (UserDecisionState.PENDING, UserDecisionState.INVALIDATED),
    (UserDecisionState.APPROVED, UserDecisionState.REVOKED),
    (UserDecisionState.APPROVED, UserDecisionState.EXPIRED),
    (UserDecisionState.APPROVED, UserDecisionState.INVALIDATED),
    (UserDecisionState.APPROVED, UserDecisionState.CONSUMED),
}


@pytest.mark.parametrize(("current", "target"), _LEGAL_USER_DECISION_TRANSITIONS)
def test_legal_user_decision_transition_returns_target(
    current: UserDecisionState,
    target: UserDecisionState,
) -> None:
    assert transition_user_decision(current, target) is target


@pytest.mark.parametrize(
    ("current", "target"),
    (
        (current, target)
        for current in UserDecisionState
        for target in UserDecisionState
        if (current, target) not in _LEGAL_USER_DECISION_TRANSITIONS
    ),
)
def test_every_other_user_decision_transition_raises(
    current: UserDecisionState,
    target: UserDecisionState,
) -> None:
    with pytest.raises(InvalidTransition):
        transition_user_decision(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    (
        pytest.param("pending", UserDecisionState.APPROVED, id="raw-current"),
        pytest.param(
            RequirementState.PENDING,
            UserDecisionState.APPROVED,
            id="foreign-enum-current",
        ),
        pytest.param(object(), UserDecisionState.APPROVED, id="object-current"),
        pytest.param(UserDecisionState.PENDING, "approved", id="raw-target"),
        pytest.param(
            UserDecisionState.PENDING,
            ActionState.DENIED,
            id="foreign-enum-target",
        ),
        pytest.param(UserDecisionState.PENDING, object(), id="object-target"),
    ),
)
def test_user_decision_transition_rejects_malformed_boundary_values(
    current: object,
    target: object,
) -> None:
    with pytest.raises(InvalidTransition):
        transition_user_decision(current, target)  # type: ignore[arg-type]
