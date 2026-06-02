from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from magi_agent.harness.general_automation.shell_policy import (
    ShellOutputBudgetMetadata,
    ShellPolicyAuthorityFlags,
    ShellPolicyDecision,
)


ShellReceiptStatus = Literal["blocked", "approval_required", "metadata_only"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_:-]{0,96}$")


class ShellPolicyReceipt(BaseModel):
    model_config = _MODEL_CONFIG

    status: ShellReceiptStatus
    command_digest: str = Field(alias="commandDigest")
    exit_reason: str = Field(alias="exitReason")
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    timeout_ms: int = Field(alias="timeoutMs")
    abortable: Literal[True] = True
    output_budget: ShellOutputBudgetMetadata = Field(alias="outputBudget")
    env_projection: dict[str, str] = Field(alias="envProjection")
    authority_flags: ShellPolicyAuthorityFlags = Field(
        default_factory=ShellPolicyAuthorityFlags,
        alias="authorityFlags",
    )

    @field_validator("exit_reason")
    @classmethod
    def _validate_exit_reason(cls, value: str) -> str:
        if not _REASON_CODE_RE.fullmatch(value):
            raise ValueError("exitReason must be a safe public reason code")
        return value

    @field_validator("reason_codes")
    @classmethod
    def _validate_reason_codes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _REASON_CODE_RE.fullmatch(item):
                raise ValueError("reason codes must be safe public identifiers")
        return value

    def public_projection(self) -> dict[str, object]:
        return {
            "status": self.status,
            "commandDigest": self.command_digest,
            "exitReason": self.exit_reason,
            "reasonCodes": self.reason_codes,
            "timeoutMs": self.timeout_ms,
            "abortable": self.abortable,
            "outputBudget": self.output_budget.model_dump(by_alias=True, mode="json"),
            "envProjection": dict(self.env_projection),
            "authorityFlags": self.authority_flags.model_dump(by_alias=True, mode="json"),
        }


def build_shell_policy_receipt(
    decision: ShellPolicyDecision,
    *,
    exitReason: str,
) -> ShellPolicyReceipt:
    if decision.status == "denied":
        status: ShellReceiptStatus = "blocked"
    elif decision.status == "approval_required":
        status = "approval_required"
    else:
        status = "metadata_only"

    return ShellPolicyReceipt(
        status=status,
        commandDigest=decision.command_digest,
        exitReason=exitReason,
        reasonCodes=decision.reason_codes,
        timeoutMs=decision.timeout_ms,
        outputBudget=decision.output_budget,
        envProjection=dict(decision.env_projection),
        authorityFlags=decision.authority_flags,
    )


__all__ = [
    "ShellPolicyReceipt",
    "build_shell_policy_receipt",
]
