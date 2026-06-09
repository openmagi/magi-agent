"""Gate 5B-4c-3 Shadow Parity — observe-only measurement primitive.

This module is OBSERVE-ONLY telemetry. It gates nothing and changes no
behavior in the live runner boundary or any decision path.

Purpose:
    Compute a public-safe parity summary comparing the Python ADK diagnostic
    output against the TypeScript authoritative answer. The summary contains
    only sha256 digests and safe-label tokens — never raw output text.

Follow-up tasks (outside the scope of this module):
    1. TypeScript side must supply ``typeScriptFinalAnswerDigest`` in the
       ``Gate5B4C3ShadowGenerationComparison`` payload.
    2. This summary should be emitted into the diagnostic report / ledger
       once the TypeScript digest supply is wired.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self

from pydantic import Field, model_validator

from magi_agent.shadow.gate5b4c3_live_runner_boundary import (
    Gate5B4C3LiveRunnerBoundaryResult,
)
from magi_agent.shadow.gate5b4c3_shadow_generation_contract import (
    Gate5B4C3ShadowGenerationComparison,
    _Gate5B4C3Model,
    _validate_digest,
    _validate_safe_label,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sha256_digest(value: str) -> str:
    """Return ``"sha256:" + sha256_hex(value)``."""
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Parity verdict logic
# ---------------------------------------------------------------------------

# Verdict mapping (documented here and tested exhaustively):
#
#   answer_parity  | status_parity  | verdict
#   ---------------+----------------+-----------------------------
#   incomparable   | any            | incomparable
#   any            | incomparable   | incomparable
#   mismatch       | mismatch       | answer_and_status_mismatch
#   mismatch       | match          | answer_mismatch
#   match          | mismatch       | status_mismatch
#   match          | match          | parity_match
#
# Principle: never report ``parity_match`` unless BOTH answer AND status are
# concretely ``"match"``. Any incomparable dimension (TS side not yet
# supplying the field) collapses the verdict to ``"incomparable"``.

_AnswerParity = Literal["match", "mismatch", "incomparable"]
_StatusParity = Literal["match", "mismatch", "incomparable"]
_Verdict = Literal[
    "parity_match",
    "answer_mismatch",
    "status_mismatch",
    "answer_and_status_mismatch",
    "incomparable",
]


def _compute_verdict(
    answer_parity: _AnswerParity,
    status_parity: _StatusParity,
) -> _Verdict:
    if answer_parity == "incomparable" or status_parity == "incomparable":
        return "incomparable"
    if answer_parity == "mismatch" and status_parity == "mismatch":
        return "answer_and_status_mismatch"
    if answer_parity == "mismatch":
        return "answer_mismatch"
    if status_parity == "mismatch":
        return "status_mismatch"
    return "parity_match"


# ---------------------------------------------------------------------------
# Public model
# ---------------------------------------------------------------------------

class Gate5B4C3ShadowParitySummary(_Gate5B4C3Model):
    """Public-safe parity summary for a Gate 5B-4c-3 shadow generation run.

    All fields are digests or safe-label tokens. No raw output text is stored
    or emitted. This model is OBSERVE-ONLY and gates nothing.
    """

    schema_version: Literal["gate5b4c3.shadowParity.v1"] = Field(
        default="gate5b4c3.shadowParity.v1",
        alias="schemaVersion",
    )
    observe_only: Literal[True] = Field(
        default=True,
        alias="observeOnly",
    )
    python_final_answer_digest: str = Field(alias="pythonFinalAnswerDigest")
    type_script_final_answer_digest: str | None = Field(
        default=None,
        alias="typeScriptFinalAnswerDigest",
    )
    answer_parity: Literal["match", "mismatch", "incomparable"] = Field(
        alias="answerParity",
    )
    python_terminal_status: str = Field(alias="pythonTerminalStatus")
    type_script_terminal_status: str | None = Field(
        default=None,
        alias="typeScriptTerminalStatus",
    )
    status_parity: Literal["match", "mismatch", "incomparable"] = Field(
        alias="statusParity",
    )
    verdict: Literal[
        "parity_match",
        "answer_mismatch",
        "status_mismatch",
        "answer_and_status_mismatch",
        "incomparable",
    ]

    @model_validator(mode="after")
    def _validate_parity_summary(self) -> Self:
        _validate_digest(
            self.python_final_answer_digest,
            "python final answer digest must be sha256",
        )
        if self.type_script_final_answer_digest is not None:
            _validate_digest(
                self.type_script_final_answer_digest,
                "TypeScript final answer digest must be sha256",
            )
        _validate_safe_label(
            self.python_terminal_status,
            "python terminal status must be public-safe",
        )
        if self.type_script_terminal_status is not None:
            _validate_safe_label(
                self.type_script_terminal_status,
                "TypeScript terminal status must be public-safe",
            )
        return self


# ---------------------------------------------------------------------------
# Pure factory function
# ---------------------------------------------------------------------------

def compute_shadow_parity(
    *,
    python_result: Gate5B4C3LiveRunnerBoundaryResult,
    comparison: Gate5B4C3ShadowGenerationComparison,
) -> Gate5B4C3ShadowParitySummary:
    """Compute a public-safe parity summary — pure, no I/O, no side effects.

    This function is OBSERVE-ONLY.  It does not gate, route, or modify any
    live path.  The result is a measurement primitive for future telemetry
    emission once the TypeScript side supplies ``type_script_final_answer_digest``.

    Verdict mapping:
        - Either incomparable dimension → ``"incomparable"``
        - Both mismatch → ``"answer_and_status_mismatch"``
        - Answer mismatch only → ``"answer_mismatch"``
        - Status mismatch only → ``"status_mismatch"``
        - Both match → ``"parity_match"``

    Note: ``user_visible_output`` on ``Gate5B4C3LiveRunnerBoundaryResult`` is
    always ``None`` (enforced by the model's non-authoritative field guard).
    The Python digest is therefore always ``sha256("")`` in current operation.
    When TypeScript begins supplying its digest and the Python side is promoted
    to authoritative serving, the digest will reflect actual output.
    """
    # --- Python answer digest (hash raw text; never emit it) ---
    raw_output: str = python_result.user_visible_output or ""
    python_digest = _sha256_digest(raw_output)

    # --- Python terminal status (public-safe label) ---
    # Use the terminal `status` only (a public-safe Literal from the contract),
    # matching the single-label shape the TypeScript side supplies in
    # `type_script_terminal_status`.  `reason` is intentionally NOT folded in:
    # a compound label would spuriously mismatch the TS single-label status.
    python_status_label: str = python_result.status

    # --- Answer parity ---
    ts_answer_digest = comparison.type_script_final_answer_digest
    if ts_answer_digest is None:
        answer_parity: _AnswerParity = "incomparable"
    elif python_digest == ts_answer_digest:
        answer_parity = "match"
    else:
        answer_parity = "mismatch"

    # --- Status parity ---
    ts_terminal_status = comparison.type_script_terminal_status
    if ts_terminal_status is None:
        status_parity: _StatusParity = "incomparable"
    elif python_status_label == ts_terminal_status:
        status_parity = "match"
    else:
        status_parity = "mismatch"

    # --- Verdict ---
    verdict = _compute_verdict(answer_parity, status_parity)

    return Gate5B4C3ShadowParitySummary(
        schemaVersion="gate5b4c3.shadowParity.v1",
        observeOnly=True,
        pythonFinalAnswerDigest=python_digest,
        typeScriptFinalAnswerDigest=ts_answer_digest,
        answerParity=answer_parity,
        pythonTerminalStatus=python_status_label,
        typeScriptTerminalStatus=ts_terminal_status,
        statusParity=status_parity,
        verdict=verdict,
    )


__all__ = [
    "Gate5B4C3ShadowParitySummary",
    "compute_shadow_parity",
]
