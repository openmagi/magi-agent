"""PR4: Read Ledger Hard Gate for workspace mutations.

Enforces that FileEdit and PatchApply require a prior read receipt for the
same relative path digest.  Stale read versions reject mutation.  Concurrent
read-only actions remain allowed.

Default-off: ``ReadLedgerHardGateConfig.enabled`` is ``False`` by default.
``productionWorkspaceMutationAllowed`` is always ``False``.
"""
from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from magi_agent.tools.read_ledger import (
    ReadLedger,
    WorkspaceMutationReadCheck,
    WorkspaceMutationReadDecision,
    is_unsafe_workspace_path,
    workspace_path_ref,
)


ReadLedgerHardGateStatus = Literal["ok", "blocked", "disabled"]

_MODEL_CONFIG = ConfigDict(
    frozen=True,
    populate_by_name=True,
    extra="forbid",
    validate_default=True,
    hide_input_in_errors=True,
)


class ReadLedgerHardGateConfig(BaseModel):
    model_config = _MODEL_CONFIG

    enabled: bool = False
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @field_serializer("production_workspace_mutation_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False


class ReadLedgerHardGateDecision(BaseModel):
    model_config = _MODEL_CONFIG

    status: ReadLedgerHardGateStatus
    reason_codes: tuple[str, ...] = Field(alias="reasonCodes")
    tool_name: str = Field(alias="toolName")
    path_ref: str = Field(alias="pathRef")
    digest_ref: str | None = Field(default=None, alias="digestRef")
    entry_ref: str | None = Field(default=None, alias="entryRef")
    production_workspace_mutation_allowed: Literal[False] = Field(
        default=False,
        alias="productionWorkspaceMutationAllowed",
    )

    @field_serializer("production_workspace_mutation_allowed")
    def _serialize_false(self, _value: object) -> bool:
        return False

    def public_projection(self) -> dict[str, object]:
        projection: dict[str, object] = {
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
            "toolName": self.tool_name,
            "pathRef": self.path_ref,
            "productionWorkspaceMutationAllowed": False,
        }
        if self.digest_ref is not None:
            projection["digestRef"] = self.digest_ref
        if self.entry_ref is not None:
            projection["entryRef"] = self.entry_ref
        return projection


class ReadLedgerHardGate:
    """Hard gate that enforces read-before-edit for FileEdit and PatchApply.

    Default-off.  When enabled, any mutation tool call must present a current
    digest that matches a prior full-read receipt in the same session, workspace,
    and path.  Stale digests are rejected.  Concurrent read-only actions are
    never blocked.
    """

    _GATED_TOOLS: frozenset[str] = frozenset({"FileEdit", "PatchApply"})

    def __init__(
        self,
        config: ReadLedgerHardGateConfig | None = None,
        *,
        read_ledger: ReadLedger | None = None,
    ) -> None:
        self.config = config or ReadLedgerHardGateConfig()
        self.read_ledger = read_ledger

    def check_mutation(
        self,
        *,
        tool_name: str,
        session_id: str,
        workspace_ref: str,
        path: str,
        current_digest: str | None = None,
    ) -> ReadLedgerHardGateDecision:
        path_ref = workspace_path_ref(workspace_ref, path)

        if not self.config.enabled:
            return ReadLedgerHardGateDecision(
                status="disabled",
                reasonCodes=("hard_gate_disabled",),
                toolName=tool_name,
                pathRef=path_ref,
                productionWorkspaceMutationAllowed=False,
            )

        if tool_name not in self._GATED_TOOLS:
            return ReadLedgerHardGateDecision(
                status="ok",
                reasonCodes=("tool_not_gated",),
                toolName=tool_name,
                pathRef=path_ref,
                productionWorkspaceMutationAllowed=False,
            )

        if self.read_ledger is None:
            return ReadLedgerHardGateDecision(
                status="blocked",
                reasonCodes=("read_ledger_required",),
                toolName=tool_name,
                pathRef=path_ref,
                productionWorkspaceMutationAllowed=False,
            )

        if is_unsafe_workspace_path(path):
            return ReadLedgerHardGateDecision(
                status="blocked",
                reasonCodes=("unsafe_or_sealed_path_blocked",),
                toolName=tool_name,
                pathRef=path_ref,
                productionWorkspaceMutationAllowed=False,
            )

        mutation_kind = "patch" if tool_name == "PatchApply" else "edit"
        read_decision = self.read_ledger.require_fresh_full_read(
            WorkspaceMutationReadCheck(
                sessionId=session_id,
                workspaceRef=workspace_ref,
                path=path,
                currentDigest=current_digest,
                mutationKind=mutation_kind,
            ),
        )

        if read_decision.status != "ok":
            return ReadLedgerHardGateDecision(
                status="blocked",
                reasonCodes=read_decision.reason_codes,
                toolName=tool_name,
                pathRef=path_ref,
                digestRef=read_decision.digest_ref,
                entryRef=read_decision.entry_ref,
                productionWorkspaceMutationAllowed=False,
            )

        return ReadLedgerHardGateDecision(
            status="ok",
            reasonCodes=read_decision.reason_codes,
            toolName=tool_name,
            pathRef=path_ref,
            digestRef=read_decision.digest_ref,
            entryRef=read_decision.entry_ref,
            productionWorkspaceMutationAllowed=False,
        )


__all__ = [
    "ReadLedgerHardGate",
    "ReadLedgerHardGateConfig",
    "ReadLedgerHardGateDecision",
    "ReadLedgerHardGateStatus",
]
