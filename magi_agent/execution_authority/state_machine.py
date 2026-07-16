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


class EffectClass(StrEnum):
    """Closed effect families understood by the authority kernel."""

    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    WORKSPACE_DELETE = "workspace.delete"
    PROCESS_EXEC = "process.exec"
    PROCESS_EXECUTE = "process.execute"
    NETWORK_READ = "network.read"
    NETWORK_CONNECT = "network.connect"
    NETWORK_WRITE = "network.write"
    DATABASE_WRITE = "database.write"
    MESSAGE_SEND = "message.send"
    ARTIFACT_DELIVER = "artifact.deliver"
    MEMORY_WRITE = "memory.write"
    SCHEDULER_WRITE = "scheduler.write"
    MISSION_WRITE = "mission.write"
    INFRASTRUCTURE_WRITE = "infrastructure.write"
    PLUGIN_WRITE = "plugin.write"
    HOOK_EXECUTE = "hook.execute"
    CHILD_AGENT_DELEGATE = "child_agent.delegate"


class ResourceSemantics(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_TRANSACTION = "workspace_transaction"
    PRIVATE_WORKSPACE_PROCESS = "private_workspace_process"
    REMOTE_EFFECT = "remote_effect"
    DURABLE_STATE_MUTATION = "durable_state_mutation"
    DELIVERY = "delivery"


class IdempotencyCapability(StrEnum):
    LOCAL_GENERATION_CAS = "local_generation_cas"
    PROVIDER_IDEMPOTENCY_KEY = "provider_idempotency_key"
    RECONCILIATION_ONLY = "reconciliation_only"
    NONE = "none"


class RecoveryStrategy(StrEnum):
    READ_ONLY_REPLAY = "read_only_replay"
    WORKSPACE_TRANSACTION = "workspace_transaction"
    PROVIDER_RECONCILIATION = "provider_reconciliation"
    PROJECTION_REBUILD = "projection_rebuild"
    NO_REPLAY = "no_replay"


class ProviderGuarantee(StrEnum):
    LOCAL_ATOMIC = "local_atomic"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    AT_MOST_ONCE = "at_most_once"
    RECONCILABLE = "reconcilable"
    NONE = "none"


class ObservationOutcome(StrEnum):
    COMMITTED = "committed"
    ABORTED = "aborted"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class AttemptKind(StrEnum):
    EXECUTION = "execution"
    RECONCILIATION = "reconciliation"


class TransmissionState(StrEnum):
    PROVEN_NOT_SENT = "proven_not_sent"
    MAY_HAVE_SENT = "may_have_sent"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PARTIAL = "partial"


class RecoveryDisposition(StrEnum):
    ABORT = "abort"
    REPLAY = "replay"
    RECONCILE = "reconcile"
    REDO_COMMIT = "redo_commit"
    REBUILD_PROJECTIONS = "rebuild_projections"
    QUARANTINE = "quarantine"


class EvidenceSemanticClass(StrEnum):
    OBSERVATION = "observation"
    ASSERTION = "assertion"
    INFERENCE = "inference"
    VERDICT = "verdict"


class EvidenceKind(StrEnum):
    ACTION_RECEIPT = "action_receipt"
    SOURCE_SNAPSHOT = "source_snapshot"
    SOURCE_SPAN = "source_span"
    EXTRACTION = "extraction"
    ENTAILMENT_VERDICT = "entailment_verdict"
    POSTCONDITION_VERDICT = "postcondition_verdict"
    WORKSPACE_POSTCONDITION = "workspace_postcondition"
    REQUIREMENT_VERDICT = "requirement_verdict"
    DEPENDENCY_HEALTH = "dependency_health"
    COMPLETION_VERDICT = "completion_verdict"
    INTEGRITY_SCAN = "integrity_scan"


class LeaseState(StrEnum):
    HELD = "held"
    RELEASED = "released"


class OutboxState(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DELIVERED = "delivered"


class WorkspacePublicationState(StrEnum):
    READY = "ready"
    PUBLISHING = "publishing"
    QUARANTINED = "quarantined"


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
        raise InvalidTransition(f"invalid action transition values: {current!r} -> {target!r}")
    if target not in _ACTION_TRANSITIONS[current]:
        raise InvalidTransition(f"illegal action transition: {current.value} -> {target.value}")
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
