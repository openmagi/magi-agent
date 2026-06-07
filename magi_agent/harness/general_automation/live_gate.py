"""Track 19 PR2 — General-Automation live allow/ask/deny gate (flag-gated).

This module is the *consumer* that turns the existing General-Automation policy
classifiers into the live tool-dispatch permission boundary for the ``general``
agent role. The classifiers themselves are untouched — this adapter calls them.

Activation requires BOTH:

* ``MAGI_GA_LIVE_ENABLED`` truthy (single-source flag, default OFF), and
* the active pack / ``agent_role`` is ``general`` (derived from the tool
  ``execution_contract``, mirroring ``tools/local_readonly.py``).

When inactive the gate is a *pure bypass*: ``classify_pre`` returns an inactive
outcome whose ``decision`` is ``allow`` and which carries no receipt / control
projection, so flag-OFF and non-general dispatch behave byte-identically to
``main``.

Mapping (active path):

* shell tool calls          → ``classify_shell_policy``
* file / path tool calls    → ``classify_path_access``
* ``denied`` / ``blocked``  → ``deny`` (existing permission-denied path) +
  ``ShellPolicyReceipt`` / ``ExternalDirectoryApprovalReceipt``
* ``approval_required`` /
  ``external_directory``     → ``ask`` (``pending_control_request`` via
  ``HookPermissionBoundary``) + a
  ``build_general_automation_control_projection(controlType="approval_required")``
* ``allowed`` /
  ``workspace_local``
  read/list                  → ``allow`` (proceed unchanged)
  write/delete/execute       → ``ask`` (approval required)

No authority flag, hard-safety verifier, or sealed core path is modified here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from magi_agent.config.env import general_automation_live_enabled
from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.harness.general_automation.control_projection import (
    GeneralAutomationControlProjection,
    GeneralAutomationControlProjectionRequest,
    build_general_automation_control_projection,
)
from magi_agent.harness.general_automation.external_directory_receipts import (
    ExternalDirectoryApprovalReceipt,
    build_external_directory_approval_receipt,
)
from magi_agent.harness.general_automation.path_policy import (
    PathAccessDecision,
    PathAccessRequest,
    PathOperationClass,
    classify_path_access,
)
from magi_agent.harness.general_automation.shell_policy import (
    ShellPolicyRequest,
    classify_shell_policy,
)
from magi_agent.harness.general_automation.shell_receipts import (
    ShellPolicyReceipt,
    build_shell_policy_receipt,
)
from magi_agent.hooks.bus import HookPermissionBoundary
from magi_agent.tools.context import ToolContext

GateDecision = Literal["allow", "deny", "ask"]

GeneralAutomationReceipt = ShellPolicyReceipt | ExternalDirectoryApprovalReceipt

_SOURCE_HOOK = "general-automation-live-gate"
_DEFAULT_OPERATION_CLASS: PathOperationClass = "read"
_VALID_OPERATION_CLASSES: frozenset[str] = frozenset(
    {"read", "write", "list", "delete", "execute"}
)
_SHELL_COMMAND_KEYS = ("command", "cmd", "shell_command", "shellCommand")
_PATH_KEYS = ("path", "file_path", "filePath", "target_path", "targetPath")


def general_automation_live_gate_enabled(
    env: Mapping[str, str] | None = None,
) -> bool:
    """Return True when ``MAGI_GA_LIVE_ENABLED`` is truthy (default OFF)."""
    return general_automation_live_enabled(env)


@dataclass(frozen=True)
class GeneralAutomationGateOutcome:
    """Result of consulting the live gate before a tool executes.

    ``active`` is ``False`` whenever the gate is bypassed (flag-OFF or
    non-general). In that case ``decision`` is always ``allow`` and no receipt /
    control projection is produced, so callers proceed unchanged.
    """

    active: bool
    decision: GateDecision
    receipt: GeneralAutomationReceipt | None = None
    permission_boundary: HookPermissionBoundary | None = None
    control_projection: GeneralAutomationControlProjection | None = None
    reason: str | None = None


_BYPASS_OUTCOME = GeneralAutomationGateOutcome(active=False, decision="allow")


class GeneralAutomationLiveGate:
    """Flag-gated consumer of the GA classifiers at the dispatch boundary."""

    def is_active(self, context: ToolContext) -> bool:
        """Return True only when the flag is ON and the role is ``general``."""
        if not general_automation_live_gate_enabled():
            return False
        return _agent_role(context) == "general"

    def classify_pre(
        self,
        tool_name: str,
        arguments: Mapping[str, object],
        context: ToolContext,
        *,
        mode: str,
    ) -> GeneralAutomationGateOutcome:
        """Classify a pending tool call. Pure bypass when the gate is inactive."""
        # tool_name/mode reserved for per-tool or plan-mode policy in a later PR
        _ = tool_name, mode
        if not self.is_active(context):
            return _BYPASS_OUTCOME

        command = _shell_command(arguments)
        if command is not None:
            return self._classify_shell(command, context)

        path = _path_argument(arguments)
        if path is not None:
            return self._classify_path(path, arguments, context)

        # No shell/path surface to classify: allow, but mark the gate active so
        # callers know it ran.
        return GeneralAutomationGateOutcome(active=True, decision="allow")

    def append_receipt_to_ledger(
        self,
        ledger: EvidenceLedger,
        receipt: GeneralAutomationReceipt,
    ) -> EvidenceLedger:
        """Append a gated receipt to the immutable append-only ledger.

        Returns a *new* ledger (the source ledger is unchanged). Shell receipts
        are recorded as a control-ref entry; external-directory approval receipts
        carry their approval-ref. The receipt's ``public_projection`` is stored as
        ledger entry metadata (already secret-scrubbed by the receipt model).
        """
        # Intentionally-unwired seam: the dispatch path must call this to persist
        # the receipt to the EvidenceLedger in a later PR (without it, the
        # completion verifier's artifact check can't see receipts).
        public_receipt = receipt.public_projection()
        if isinstance(receipt, ShellPolicyReceipt):
            return ledger.append_control_ref(
                receipt.command_digest,
                metadata={
                    "generalAutomationReceipt": public_receipt,
                    "shellPolicyReceipt": public_receipt,
                },
            )
        return ledger.append_control_ref(
            receipt.approval_ref,
            metadata={
                "generalAutomationReceipt": public_receipt,
                "externalDirectoryApprovalReceipt": public_receipt,
            },
        )

    # ------------------------------------------------------------------
    # Internal classification
    # ------------------------------------------------------------------

    def _classify_shell(
        self,
        command: str,
        context: ToolContext,
    ) -> GeneralAutomationGateOutcome:
        # NOTE: The GA shell classifier is regex/token based and does NOT catch
        # subshell/eval/backtick/`| xargs sh`/`base64|bash` indirection.  This
        # is acceptable because no live shell-EXECUTION tool is wired in the
        # general pack yet; a future PR adding live shell execution must extend
        # the classifier to handle these indirect forms before enabling it.
        request = ShellPolicyRequest(
            command=command,
            workspaceRoot=_workspace_root(context),
        )
        decision = classify_shell_policy(request)

        if decision.status == "denied":
            receipt = build_shell_policy_receipt(
                decision,
                exitReason="destructive_filesystem_operation_denied"
                if decision.destructive_commands
                else "shell_policy_denied",
            )
            return GeneralAutomationGateOutcome(
                active=True,
                decision="deny",
                receipt=receipt,
                permission_boundary=HookPermissionBoundary(
                    source_hook=_SOURCE_HOOK,
                    decision="deny",  # HookPermissionBoundary uses approve/deny/ask (not the gate's allow/ask/deny)
                    reason="general_automation_shell_policy_denied",
                ),
                reason="general_automation_shell_policy_denied",
            )

        if decision.status == "approval_required":
            receipt = build_shell_policy_receipt(
                decision,
                exitReason="shell_policy_approval_required",
            )
            projection = _control_projection(
                subject_ref=decision.command_digest,
                policy_ref="policy:general-automation:shell-policy",
                payload_digest=decision.command_digest,
                reason_codes=decision.reason_codes,
            )
            return GeneralAutomationGateOutcome(
                active=True,
                decision="ask",
                receipt=receipt,
                permission_boundary=HookPermissionBoundary(
                    source_hook=_SOURCE_HOOK,
                    decision="ask",  # HookPermissionBoundary uses approve/deny/ask (not the gate's allow/ask/deny)
                    requires_control_request=True,
                    reason="general_automation_shell_policy_approval_required",
                ),
                control_projection=projection,
                reason="general_automation_shell_policy_approval_required",
            )

        return GeneralAutomationGateOutcome(active=True, decision="allow")

    def _classify_path(
        self,
        path: str,
        arguments: Mapping[str, object],
        context: ToolContext,
    ) -> GeneralAutomationGateOutcome:
        request = PathAccessRequest(
            workspaceRoot=_workspace_root(context),
            path=path,
            operationClass=_operation_class(arguments),
        )
        decision = classify_path_access(request)

        if decision.status == "workspace_local":
            # Read / list → silent allow (workspace_local_access, approvalRequired=False).
            # Write / delete / execute → approvalRequired=True per path_policy; surface
            # as "ask" so the caller must obtain approval before the tool runs.
            if not decision.approval_required:
                return GeneralAutomationGateOutcome(active=True, decision="allow")
            # Workspace mutation: issue a control projection (ask) so the
            # dispatcher surfaces status=needs_approval.  No ExternalDirectory
            # receipt is produced here — workspace writes are distinct from
            # external-directory access and do not share that receipt chain.
            projection = _control_projection(
                subject_ref=decision.path_digest,
                policy_ref="policy:general-automation:path-policy",
                payload_digest=decision.path_digest,
                reason_codes=decision.reason_codes,
                approval_ref=_workspace_write_approval_ref(decision),
            )
            return GeneralAutomationGateOutcome(
                active=True,
                decision="ask",
                permission_boundary=HookPermissionBoundary(
                    source_hook=_SOURCE_HOOK,
                    decision="ask",
                    requires_control_request=True,
                    reason="general_automation_workspace_write_requires_approval",
                ),
                control_projection=projection,
                reason="general_automation_workspace_write_requires_approval",
            )

        if decision.status == "external_directory":
            receipt = build_external_directory_approval_receipt(
                decision,
                approvalRef=_approval_ref(decision),
            )
            projection = _control_projection(
                subject_ref=decision.path_digest,
                policy_ref="policy:general-automation:path-policy",
                payload_digest=decision.path_digest,
                reason_codes=decision.reason_codes,
                approval_ref=receipt.approval_ref,
            )
            return GeneralAutomationGateOutcome(
                active=True,
                decision="ask",
                receipt=receipt,
                permission_boundary=HookPermissionBoundary(
                    source_hook=_SOURCE_HOOK,
                    decision="ask",  # HookPermissionBoundary uses approve/deny/ask (not the gate's allow/ask/deny)
                    requires_control_request=True,
                    reason="general_automation_external_directory_approval_required",
                ),
                control_projection=projection,
                reason="general_automation_external_directory_approval_required",
            )

        # blocked
        return GeneralAutomationGateOutcome(
            active=True,
            decision="deny",
            permission_boundary=HookPermissionBoundary(
                source_hook=_SOURCE_HOOK,
                decision="deny",  # HookPermissionBoundary uses approve/deny/ask (not the gate's allow/ask/deny)
                reason="general_automation_path_blocked",
            ),
            reason="general_automation_path_blocked",
        )


# ---------------------------------------------------------------------------
# Dispatch receipt ledger store
# ---------------------------------------------------------------------------


class GeneralAutomationReceiptLedgerStore:
    """In-memory per-turn ledger for GA dispatch receipts.

    The CLI and local dashboard runner path is local-only and single-process, so
    this store intentionally stays small and ephemeral. It gives the real
    dispatcher somewhere to retain GA gate receipts without granting execution,
    route, traffic, or production-write authority.
    """

    def __init__(self) -> None:
        self._ledgers: dict[tuple[str, str], EvidenceLedger] = {}

    def append_receipt(
        self,
        context: ToolContext,
        receipt: GeneralAutomationReceipt,
        *,
        gate: GeneralAutomationLiveGate | None = None,
    ) -> EvidenceLedger:
        key = _ledger_key(context)
        ledger = self._ledgers.get(key)
        if ledger is None:
            ledger = _new_dispatch_ledger(context, session_id=key[0], turn_id=key[1])
        updated = (gate or GeneralAutomationLiveGate()).append_receipt_to_ledger(
            ledger,
            receipt,
        )
        self._ledgers[key] = updated
        return updated

    def ledger_for_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
    ) -> EvidenceLedger | None:
        return self._ledgers.get((session_id, turn_id))

    def entries_for_turn(self, turn_id: str) -> tuple[object, ...]:
        return tuple(
            entry
            for (_session_id, stored_turn_id), ledger in self._ledgers.items()
            if stored_turn_id == turn_id
            for entry in ledger.entries
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _control_projection(
    *,
    subject_ref: str,
    policy_ref: str,
    payload_digest: str,
    reason_codes: tuple[str, ...],
    approval_ref: str | None = None,
) -> GeneralAutomationControlProjection:
    request = GeneralAutomationControlProjectionRequest(
        controlType="approval_required",
        subjectRef=subject_ref,
        policyRef=policy_ref,
        payloadDigest=payload_digest,
        reasonCodes=reason_codes,
        approvalRef=approval_ref,
    )
    return build_general_automation_control_projection(request)


def _approval_ref(decision: PathAccessDecision) -> str:
    return f"approval:external-directory:{decision.path_digest}"


def _workspace_write_approval_ref(decision: PathAccessDecision) -> str:
    return f"approval:workspace-write:{decision.path_digest}"


def _agent_role(context: ToolContext) -> str:
    contract = context.execution_contract
    if isinstance(contract, Mapping):
        for key in ("agentRole", "agent_role"):
            value = contract.get(key)
            if isinstance(value, str):
                return value.strip().casefold().replace("-", "_")
    # No execution_contract present — unknown role, not "general".
    # Returning "" ensures is_active() returns False (bypass) rather than
    # incorrectly treating every uncontracted dispatch as general-role.
    # NOTE: sourceAgentRole / source_agent_role are intentionally excluded —
    # they are not populated by the runtime and would create a spoof surface.
    return ""


def _ledger_key(context: ToolContext) -> tuple[str, str]:
    session_id = (
        context.session_id
        or context.session_key
        or context.bot_id
        or "local-session"
    )
    turn_id = context.turn_id or "local-turn"
    return (session_id, turn_id)


def _new_dispatch_ledger(
    context: ToolContext,
    *,
    session_id: str,
    turn_id: str,
) -> EvidenceLedger:
    agent_role = _agent_role(context) or "general"
    return EvidenceLedger(
        ledgerId=f"ledger:{session_id}:{turn_id}:ga-dispatch",
        sessionId=session_id,
        turnId=turn_id,
        runOn="main",
        agentRole=agent_role,
        spawnDepth=context.spawn_depth,
        sourceKind="tool_trace",
        producerSurface="tool_host",
        trafficAttached=False,
        executionAttached=False,
        routeAttached=False,
        metadata={"source": _SOURCE_HOOK},
    )


def _workspace_root(context: ToolContext) -> str:
    root = context.workspace_root
    if isinstance(root, str) and root.startswith("/"):
        return root
    return "/workspace"


def _shell_command(arguments: Mapping[str, object]) -> str | None:
    for key in _SHELL_COMMAND_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _path_argument(arguments: Mapping[str, object]) -> str | None:
    for key in _PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _operation_class(arguments: Mapping[str, object]) -> PathOperationClass:
    for key in ("operationClass", "operation_class"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip().casefold() in _VALID_OPERATION_CLASSES:
            return value.strip().casefold()  # type: ignore[return-value]
    return _DEFAULT_OPERATION_CLASS


__all__ = [
    "GateDecision",
    "GeneralAutomationGateOutcome",
    "GeneralAutomationLiveGate",
    "GeneralAutomationReceiptLedgerStore",
    "GeneralAutomationReceipt",
    "general_automation_live_gate_enabled",
]
