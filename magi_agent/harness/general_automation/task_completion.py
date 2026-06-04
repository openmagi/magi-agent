"""Track 19 PR3 — General-Automation task-completion verifier (flag-gated).

This module makes a ``general`` task unable to finalise without the deliverable
evidence its active contract required. It is a deterministic verifier that runs
in the ``task_plan_completion`` stage of the verifier bus and a *seam* function
(:func:`completion_repair_decision`) that the turn loop consults at its finalise
branch.

Activation requires BOTH:

* ``MAGI_GA_LIVE_ENABLED`` truthy (single-source flag, default OFF), and
* ``agent_role == "general"``.

When inactive — any non-general role, flag-OFF, or a contract that declares no
required deliverable evidence — :func:`completion_repair_decision` returns
``None`` (no gate), so the finalise path behaves byte-identically to ``main``.

The verifier never blocks terminally and never touches the protected
``security-policy-hard-safety`` verifier: a missing deliverable routes to
*repair* (re-enter the loop with a synthetic "you still owe X" user turn),
bounded by :data:`COMPLETION_REPAIR_LIMIT` attempts before falling through to a
finalise with an audit event.
"""
from __future__ import annotations

from collections.abc import Mapping, MutableSequence, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.runtime.turn_policy import StopReasonHandlerState


#: Bounded number of completion-repair re-entries before forcing a finalise.
#: Mirrors ``MAX_OUTPUT_TOKENS_RECOVERY_LIMIT`` in ``runtime/turn_policy``.
COMPLETION_REPAIR_LIMIT = 3

#: Public label for the artifact deliverable requirement.
_ARTIFACT_REF_LABEL = "artifactRef"
#: Public label for the snapshot deliverable requirement.
_SNAPSHOT_REF_LABEL = "snapshotRef"

#: Ledger payload / metadata keys that satisfy the artifact requirement.
#: Written by the live flow via metadata["localArtifactReceipt"]["artifactRef"]
#: in the spreadsheet.write tool handler.
_ARTIFACT_KEYS: frozenset[str] = frozenset({"artifactId", "artifactRef"})
#: Ledger payload / metadata keys that satisfy the snapshot requirement.
#: Expected to be written by SpreadsheetWriteEvidence.sourceSnapshotRef once
#: a production path is wired (no non-test caller exists yet — see
#: ENFORCE_SNAPSHOT_REQUIREMENT below).
_SNAPSHOT_KEYS: frozenset[str] = frozenset(
    {"snapshotRef", "sourceSnapshotRef", "snapshotId"}
)

#: Flip to True once a production path writes a snapshot ref to the
#: EvidenceLedger (later PR). Until then, requiring it would force every real
#: write task into repair-exhaustion because SpreadsheetWriteEvidence.sourceSnapshotRef
#: has no non-test caller.
ENFORCE_SNAPSHOT_REQUIREMENT = False


@dataclass(frozen=True)
class RequiredDeliverableEvidence:
    """The deliverable evidence an active contract requires before finalise.

    Built from a contract's ``requires_artifact_ref`` / ``requires_snapshot_ref``
    declarations via :func:`required_deliverable_evidence_for_contract`. When
    both flags are ``False`` the requirement is empty and the completion gate is
    inert.
    """

    requires_artifact_ref: bool = False
    requires_snapshot_ref: bool = False

    def is_empty(self) -> bool:
        """Return True when no deliverable evidence is required."""
        return not (self.requires_artifact_ref or self.requires_snapshot_ref)


@dataclass(frozen=True)
class TaskCompletionVerdict:
    """Deterministic verdict for the ``task_plan_completion`` stage.

    ``status`` is ``"pass"`` when every required deliverable ref is present in
    the ledger, else ``"fail"`` with ``action == "repair"`` and ``missing``
    naming the still-owed deliverable labels. ``repair_message`` is the synthetic
    user-turn text used to re-enter the loop.
    """

    status: Literal["pass", "fail"]
    missing: tuple[str, ...] = ()
    action: Literal["pass", "repair"] = "pass"
    repair_message: str | None = None

    def __post_init__(self) -> None:
        if self.status == "fail" and self.action == "pass":
            raise ValueError(
                "TaskCompletionVerdict invariant violated: "
                "status='fail' must not have action='pass'"
            )
        if self.status == "pass" and self.action == "repair":
            raise ValueError(
                "TaskCompletionVerdict invariant violated: "
                "status='pass' must not have action='repair'"
            )


class _ContractWithRequiredEvidence(Protocol):
    requires_artifact_ref: bool
    requires_snapshot_ref: bool


def required_deliverable_evidence_for_contract(
    contract: _ContractWithRequiredEvidence,
) -> RequiredDeliverableEvidence:
    """Derive the required deliverable evidence set from an active contract.

    Reads the contract's ``requires_artifact_ref`` / ``requires_snapshot_ref``
    declarations (e.g. ``spreadsheet.write`` sets both). Contracts that declare
    neither yield an empty requirement, which keeps the completion gate inert.
    """
    return RequiredDeliverableEvidence(
        requires_artifact_ref=bool(getattr(contract, "requires_artifact_ref", False)),
        requires_snapshot_ref=bool(getattr(contract, "requires_snapshot_ref", False)),
    )


class TaskCompletionVerifier:
    """Deterministic ``task_plan_completion`` verifier (default-OFF for the bus).

    Given the evidence ledger and the active contract's required deliverable
    evidence, returns a :class:`TaskCompletionVerdict`. This is a pure read over
    the immutable ledger — it mutates nothing and consults no model.
    """

    verifier_id = "ga-task-completion"
    stage = "task_plan_completion"

    def evaluate(
        self,
        ledger: EvidenceLedger,
        required: RequiredDeliverableEvidence,
    ) -> TaskCompletionVerdict:
        if required.is_empty():
            return TaskCompletionVerdict(status="pass")

        present = _present_deliverable_keys(ledger)
        missing: list[str] = []
        if required.requires_artifact_ref and not (present & _ARTIFACT_KEYS):
            missing.append(_ARTIFACT_REF_LABEL)
        if (
            ENFORCE_SNAPSHOT_REQUIREMENT
            and required.requires_snapshot_ref
            and not (present & _SNAPSHOT_KEYS)
        ):
            missing.append(_SNAPSHOT_REF_LABEL)

        if not missing:
            return TaskCompletionVerdict(status="pass")

        owed = tuple(missing)
        return TaskCompletionVerdict(
            status="fail",
            missing=owed,
            action="repair",
            repair_message=_build_repair_message(owed),
        )


# ---------------------------------------------------------------------------
# Finalise-path seam
# ---------------------------------------------------------------------------


class _CompletionGateDeps(Protocol):
    def stage_audit_event(
        self,
        event: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        ...


@dataclass(frozen=True)
class _CompletionGate:
    """Closure-like gate consulted by ``handle_stop_reason`` at finalise.

    ``__call__`` mirrors the ``turn_policy`` output-recovery mechanism: it may
    inject a synthetic user turn into ``messages`` and bump the bounded repair
    counter on ``state``. Returns ``True`` when the turn should re-enter the loop
    (recover) and ``False`` when finalise should proceed.
    """

    verdict: TaskCompletionVerdict

    def __call__(
        self,
        deps: _CompletionGateDeps,
        state: StopReasonHandlerState,
        *,
        blocks: Sequence[dict[str, Any]],
        iteration: int,
        messages: MutableSequence[dict[str, Any]],
    ) -> bool:
        if self.verdict.status == "pass":
            return False

        attempt = state.completion_repair_attempt
        if attempt >= COMPLETION_REPAIR_LIMIT:
            deps.stage_audit_event(
                "ga_completion_repair_exhausted",
                {
                    "missing": list(self.verdict.missing),
                    "limit": COMPLETION_REPAIR_LIMIT,
                    "iteration": iteration,
                },
            )
            return False

        filtered_blocks = [
            deepcopy(block)
            for block in blocks
            if block.get("type") != "tool_use"
        ]
        if filtered_blocks:
            messages.append({"role": "assistant", "content": filtered_blocks})
        messages.append(
            {"role": "user", "content": self.verdict.repair_message or ""}
        )

        state.completion_repair_attempt = attempt + 1
        deps.stage_audit_event(
            "ga_completion_repair",
            {
                "missing": list(self.verdict.missing),
                "completionRepairAttempt": attempt + 1,
                "iteration": iteration,
            },
        )
        return True


def completion_repair_decision(
    *,
    ledger: EvidenceLedger,
    required: RequiredDeliverableEvidence,
    agent_role: str,
    env: Mapping[str, str] | None = None,
) -> _CompletionGate | None:
    """Build the finalise-path completion gate, or ``None`` when inert.

    Returns ``None`` (no gate; finalise unchanged) when the flag is OFF, the
    role is not ``general``, or the contract declares no required deliverable
    evidence. Otherwise returns a gate that ``handle_stop_reason`` consults at
    its finalise branch.
    """
    if not general_automation_live_enabled(env):
        return None
    if _normalize_role(agent_role) != "general":
        return None
    if required.is_empty():
        return None

    verdict = TaskCompletionVerifier().evaluate(ledger, required)
    if verdict.status == "pass":
        return None
    return _CompletionGate(verdict=verdict)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_role(agent_role: str) -> str:
    return agent_role.strip().casefold().replace("-", "_")


def _present_deliverable_keys(ledger: EvidenceLedger) -> frozenset[str]:
    present: set[str] = set()
    for entry in ledger.entries:
        _collect_keys(entry.payload, present)
        _collect_keys(entry.metadata, present)
    return frozenset(present)


def _collect_keys(
    mapping: Mapping[str, object], present: set[str], depth: int = 0
) -> None:
    if depth > 6:
        return
    for key, value in mapping.items():
        if key in _ARTIFACT_KEYS or key in _SNAPSHOT_KEYS:
            if isinstance(value, str) and value.strip():
                present.add(key)
        elif isinstance(value, Mapping):
            _collect_keys(value, present, depth + 1)


def _build_repair_message(missing: tuple[str, ...]) -> str:
    owed = ", ".join(missing)
    return (
        "You still owe the required deliverable evidence before this task is "
        f"complete: {owed}. Produce the missing deliverable(s) and emit the "
        "matching receipt(s), then continue."
    )


__all__ = [
    "COMPLETION_REPAIR_LIMIT",
    "ENFORCE_SNAPSHOT_REQUIREMENT",
    "RequiredDeliverableEvidence",
    "TaskCompletionVerdict",
    "TaskCompletionVerifier",
    "completion_repair_decision",
    "required_deliverable_evidence_for_contract",
]
