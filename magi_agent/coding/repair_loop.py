"""PR6: Bounded Coding Repair Loop.

Provides a deterministic decision model for coding repair attempts.
When tests fail, the model can request bounded repairs up to a configurable
maximum. If max attempts are reached without passing evidence, the system
must ask the user or abstain -- never claim false success.

Default-off: ``CodingRepairLoopConfig.enabled`` defaults to ``False``.
``productionWorkspaceMutationAllowed`` is always ``False``.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

CodingRepairAction = Literal[
    "continue_repair",
    "ask_user",
    "abstain",
    "project_success",
]

CodingRepairReasonCode = Literal[
    "test_failure_detected",
    "test_pass_detected",
    "max_attempts_reached",
    "missing_evidence",
    "repair_loop_disabled",
    "repair_action_selected",
]

# ---------------------------------------------------------------------------
# Pydantic model config (shared)
# ---------------------------------------------------------------------------

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    revalidate_instances="always",
    hide_input_in_errors=True,
)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class CodingRepairLoopConfig(BaseModel):
    """Configuration for the bounded coding repair loop.

    Default-off: ``enabled`` is ``False`` by default.
    ``max_attempts`` is capped at 10 to prevent unbounded loops.
    """
    model_config = _MODEL_CONFIG

    enabled: bool = False
    max_attempts: int = Field(default=3, ge=0, le=10, alias="maxAttempts")


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class CodingRepairLoopState(BaseModel):
    """Tracks the mutable state of a repair loop across attempts."""
    model_config = _MODEL_CONFIG

    attempt_count: int = Field(default=0, ge=0, alias="attemptCount")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

class CodingRepairDecision(BaseModel):
    """Deterministic decision from the repair loop evaluator."""
    model_config = _MODEL_CONFIG

    action: CodingRepairAction
    attempt_count: int = Field(ge=0, alias="attemptCount")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    evidence_refs: tuple[str, ...] = Field(default=(), alias="evidenceRefs")
    evidence_digest: str | None = Field(default=None, alias="evidenceDigest")


# ---------------------------------------------------------------------------
# Result (wraps decision + updated state)
# ---------------------------------------------------------------------------

class CodingRepairLoopResult(BaseModel):
    """Final result of the repair loop evaluation.

    ``production_workspace_mutation_allowed`` is always ``False`` -- the
    validator rejects any attempt to set it to ``True``.
    """
    model_config = _MODEL_CONFIG

    decision: CodingRepairDecision
    state: CodingRepairLoopState
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @field_validator("production_workspace_mutation_allowed", mode="before")
    @classmethod
    def _reject_production_mutation(cls, value: object) -> bool:
        if value is True:
            raise ValueError(
                "productionWorkspaceMutationAllowed must always be False"
            )
        return False


# ---------------------------------------------------------------------------
# Core evaluator
# ---------------------------------------------------------------------------

def evaluate_repair_decision(
    *,
    config: CodingRepairLoopConfig,
    state: CodingRepairLoopState,
    latest_test_evidence: Mapping[str, object] | None,
) -> CodingRepairDecision:
    """Evaluate the next repair decision based on config, state, and evidence.

    Returns a deterministic ``CodingRepairDecision`` with one of:
    - ``continue_repair``: another attempt is allowed
    - ``ask_user``: max attempts reached, ask the user
    - ``abstain``: disabled or max attempts with no evidence
    - ``project_success``: passing test evidence detected
    """
    # Disabled -- abstain immediately
    if not config.enabled:
        return CodingRepairDecision(
            action="abstain",
            attemptCount=state.attempt_count,
            reasonCodes=("repair_loop_disabled",),
            evidenceRefs=state.evidence_refs,
            evidenceDigest=None,
        )

    # Compute evidence digest for the latest test evidence
    evidence_digest: str | None = None
    if latest_test_evidence is not None:
        evidence_digest = _compute_evidence_digest(latest_test_evidence)

    # Check if the latest test evidence shows a pass
    test_passed = _is_test_pass(latest_test_evidence)

    if test_passed:
        new_ref = evidence_digest or "evidence:pass"
        return CodingRepairDecision(
            action="project_success",
            attemptCount=state.attempt_count,
            reasonCodes=("test_pass_detected",),
            evidenceRefs=(*state.evidence_refs, new_ref),
            evidenceDigest=evidence_digest,
        )

    # Check if we've reached the max attempts
    if state.attempt_count >= config.max_attempts:
        reason_codes: list[str] = ["max_attempts_reached"]
        if latest_test_evidence is None:
            reason_codes.append("missing_evidence")
        return CodingRepairDecision(
            action="ask_user",
            attemptCount=state.attempt_count,
            reasonCodes=tuple(reason_codes),
            evidenceRefs=state.evidence_refs,
            evidenceDigest=evidence_digest,
        )

    # Failing test -- continue repair
    new_attempt = state.attempt_count + 1
    new_ref = evidence_digest or f"evidence:attempt-{new_attempt}"
    reason_codes_tuple: tuple[str, ...] = ("test_failure_detected",)
    if latest_test_evidence is None:
        reason_codes_tuple = ("test_failure_detected", "missing_evidence")

    return CodingRepairDecision(
        action="continue_repair",
        attemptCount=new_attempt,
        reasonCodes=reason_codes_tuple,
        evidenceRefs=(*state.evidence_refs, new_ref),
        evidenceDigest=evidence_digest,
    )


# ---------------------------------------------------------------------------
# Event projection (public-safe)
# ---------------------------------------------------------------------------

def project_repair_decision_event(
    decision: CodingRepairDecision,
) -> dict[str, object]:
    """Project a repair decision into a public-safe event dict.

    No raw file paths, contents, or auth tokens are included.
    Evidence refs use sha256 digests.
    """
    return {
        "type": "coding_repair_decision",
        "action": decision.action,
        "attemptCount": decision.attempt_count,
        "reasonCodes": decision.reason_codes,
        "evidenceRefs": decision.evidence_refs,
        "evidenceDigest": decision.evidence_digest,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_test_pass(evidence: Mapping[str, object] | None) -> bool:
    """Check if evidence represents a passing test."""
    if evidence is None:
        return False
    status = evidence.get("status")
    if status == "ok":
        return True
    fields = evidence.get("fields")
    if isinstance(fields, Mapping):
        exit_code = fields.get("exitCode")
        if exit_code == 0:
            return True
    return False


def _compute_evidence_digest(evidence: Mapping[str, object]) -> str:
    """Compute a sha256 digest of evidence for public-safe referencing."""
    # Deterministic JSON serialization of the evidence
    canonical = json.dumps(
        _make_serializable(evidence),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def _make_serializable(obj: object) -> object:
    """Convert an object to a JSON-serializable form."""
    if isinstance(obj, Mapping):
        return {str(k): _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(item) for item in obj]
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    return str(obj)


__all__ = [
    "CodingRepairAction",
    "CodingRepairDecision",
    "CodingRepairLoopConfig",
    "CodingRepairLoopResult",
    "CodingRepairLoopState",
    "CodingRepairReasonCode",
    "evaluate_repair_decision",
    "project_repair_decision_event",
]
