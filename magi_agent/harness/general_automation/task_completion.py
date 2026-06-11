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

A4 promotion: ``handle_stop_reason`` has no production caller (the live turn
loop is ADK's), so :func:`completion_repair_decision` alone never gated a real
run. The deliverable check is therefore ALSO consumed by the LIVE pre-final
evidence gate in ``cli.engine`` via :func:`missing_deliverable_labels` /
:func:`required_deliverable_evidence_from_labels`, behind the strict
default-OFF ``MAGI_GA_DELIVERABLE_GATE_ENABLED`` flag.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableSequence, Sequence
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

#: Ledger payload / metadata keys that satisfy the artifact requirement.
#: Written by the live flow via metadata["localArtifactReceipt"]["artifactRef"]
#: in the spreadsheet.write tool handler.
_ARTIFACT_SCALAR_KEYS: frozenset[str] = frozenset({"artifactId", "artifactRef"})
_ARTIFACT_COLLECTION_KEYS: frozenset[str] = frozenset({"artifactRefs"})
_ARTIFACT_KEYS: frozenset[str] = _ARTIFACT_SCALAR_KEYS | _ARTIFACT_COLLECTION_KEYS

# NOTE (A4 promote-or-delete): the snapshot half of this verifier
# (``ENFORCE_SNAPSHOT_REQUIREMENT`` / ``requires_snapshot_ref`` / snapshot key
# scanning) was DELETED, not promoted. No first-party recipe evidence label
# ever contained "snapshot" and no production path writes a snapshot ref into
# any ledger this verifier reads (SpreadsheetWriteEvidence.sourceSnapshotRef
# has no non-test caller), so the branch was unproducible dead plumbing.
# Re-introduce it together with a real producer if one ever lands.


@dataclass(frozen=True)
class RequiredDeliverableEvidence:
    """The deliverable evidence an active contract requires before finalise.

    Built from a contract's ``requires_artifact_ref`` declaration via
    :func:`required_deliverable_evidence_for_contract` or from a policy
    assembly's evidence labels via
    :func:`required_deliverable_evidence_from_labels`. When the flag is
    ``False`` the requirement is empty and the completion gate is inert.
    """

    requires_artifact_ref: bool = False

    def is_empty(self) -> bool:
        """Return True when no deliverable evidence is required."""
        return not self.requires_artifact_ref


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


def required_deliverable_evidence_for_contract(
    contract: _ContractWithRequiredEvidence,
) -> RequiredDeliverableEvidence:
    """Derive the required deliverable evidence set from an active contract.

    Reads the contract's ``requires_artifact_ref`` declaration (e.g.
    ``spreadsheet.write`` sets it). Contracts that do not declare it yield an
    empty requirement, which keeps the completion gate inert. The contract-level
    ``requires_snapshot_ref`` schema field is intentionally NOT consumed here —
    snapshot enforcement was deleted because nothing produces a snapshot ref
    (see module note above).
    """
    return RequiredDeliverableEvidence(
        requires_artifact_ref=bool(getattr(contract, "requires_artifact_ref", False)),
    )


def required_deliverable_evidence_from_labels(
    labels: Iterable[str],
) -> RequiredDeliverableEvidence:
    """Map policy-assembly evidence-requirement labels onto the requirement.

    ``labels`` is the public evidence label vocabulary carried by a
    ``RunnerPolicyAssembly`` (e.g. ``"artifact_delivery_ref"``,
    ``"office_preview"``, ``"source_ledger"``). Any label mentioning
    ``"artifact"`` requires an artifact deliverable receipt. Shared by
    ``cli.real_runner`` (constraint-reminder control) and ``cli.engine``
    (flag-gated pre-final deliverable gate) so the mapping cannot drift.
    """
    return RequiredDeliverableEvidence(
        requires_artifact_ref=any("artifact" in label for label in labels),
    )


def missing_deliverable_labels(
    required: RequiredDeliverableEvidence,
    entries: Iterable[object],
) -> tuple[str, ...]:
    """Return the still-owed deliverable labels over generic evidence entries.

    A pure, deterministic read. ``entries`` may be ledger entries (objects with
    ``payload`` / ``metadata`` mappings), local tool evidence projections
    (plain mappings), or pydantic records (``model_dump`` fallback). An entry
    satisfies the artifact requirement when a non-blank ``artifactRef`` /
    ``artifactId`` value is present anywhere within the bounded nesting depth.
    """
    if required.is_empty():
        return ()
    present: set[str] = set()
    for entry in entries:
        _collect_entry_keys(entry, present)
    if required.requires_artifact_ref and not (present & _ARTIFACT_KEYS):
        return (_ARTIFACT_REF_LABEL,)
    return ()


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
        missing = missing_deliverable_labels(required, ledger.entries)
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
    # Intentionally-unwired seam: must be passed as ``completion_gate`` to
    # ``runtime.turn_policy.handle_stop_reason`` by the runner in a later PR.
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


def _collect_entry_keys(entry: object, present: set[str]) -> None:
    if isinstance(entry, Mapping):
        _collect_keys(entry, present)
        return
    scanned = False
    present_before = len(present)
    for attr in ("payload", "metadata"):
        value = getattr(entry, attr, None)
        if isinstance(value, Mapping):
            scanned = True
            _collect_keys(value, present)
    if scanned and len(present) > present_before:
        return
    model_dump = getattr(entry, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump(by_alias=True, mode="python", warnings=False)
        except Exception:
            try:
                dumped = model_dump()
            except Exception:
                return
        if isinstance(dumped, Mapping):
            _collect_keys(dumped, present)


def _collect_keys(
    mapping: Mapping[str, object], present: set[str], depth: int = 0
) -> None:
    if depth > 6:
        return
    for key, value in mapping.items():
        if key in _ARTIFACT_SCALAR_KEYS:
            if isinstance(value, str) and value.strip():
                present.add(key)
        elif key in _ARTIFACT_COLLECTION_KEYS:
            if _contains_nonblank_artifact_ref(value):
                present.add(key)
        elif isinstance(value, Mapping):
            _collect_keys(value, present, depth + 1)


def _contains_nonblank_artifact_ref(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return any(isinstance(item, str) and item.strip() for item in value)
    return False


def _build_repair_message(missing: tuple[str, ...]) -> str:
    owed = ", ".join(missing)
    return (
        "You still owe the required deliverable evidence before this task is "
        f"complete: {owed}. Produce the missing deliverable(s) and emit the "
        "matching receipt(s), then continue."
    )


__all__ = [
    "COMPLETION_REPAIR_LIMIT",
    "RequiredDeliverableEvidence",
    "TaskCompletionVerdict",
    "TaskCompletionVerifier",
    "completion_repair_decision",
    "missing_deliverable_labels",
    "required_deliverable_evidence_for_contract",
    "required_deliverable_evidence_from_labels",
]
