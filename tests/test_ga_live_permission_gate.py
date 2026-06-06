"""Track 19 PR2 — GA classifiers as the live allow/ask/deny gate (flag-gated).

These tests pin the contract that, when ``MAGI_GA_LIVE_ENABLED`` is ON *and* the
active pack/agent_role is ``general``:

* shell tool calls route through ``classify_shell_policy``,
* file/path tool calls route through ``classify_path_access``,
* destructive shell ``rm -rf`` is BLOCKED (existing permission-denied path) and a
  ``ShellPolicyReceipt`` is produced/appended to the evidence ledger,
* an external-directory write yields a ``pending_control_request`` carrying a
  ``build_general_automation_control_projection(controlType="approval_required")``
  plus an ``ExternalDirectoryApprovalReceipt``,
* a workspace-local read proceeds unchanged.

Flag-OFF or non-general → the gate is a pure bypass (no classifier invoked,
dispatcher behavior byte-identical to ``main``).
"""
from __future__ import annotations

import asyncio

import pytest

from magi_agent.evidence.ledger import EvidenceLedger
from magi_agent.harness.general_automation.external_directory_receipts import (
    ExternalDirectoryApprovalReceipt,
)
from magi_agent.harness.general_automation.live_gate import (
    GeneralAutomationLiveGate,
    general_automation_live_gate_enabled,
)
from magi_agent.harness.general_automation.shell_receipts import ShellPolicyReceipt
from magi_agent.hooks.bus import HookPermissionBoundary
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


WORKSPACE_ROOT = "/workspace/bot"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _general_context() -> ToolContext:
    return ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract={"agentRole": "general"},
    )


def _coding_context() -> ToolContext:
    return ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract={"agentRole": "coding"},
    )


def _ledger() -> EvidenceLedger:
    return EvidenceLedger.model_validate(
        {
            "ledgerId": "ledger-session-1-turn-1",
            "sessionId": "session-1",
            "turnId": "turn-1",
            "runOn": "main",
            "agentRole": "general",
            "spawnDepth": 0,
            "sourceKind": "tool_trace",
            "producerSurface": "tool_host",
        }
    )


def _registry_with_handler(name: str, *, permission: str = "execute") -> ToolRegistry:
    registry = ToolRegistry()
    source = ToolSource(kind="builtin", package="test")
    manifest = ToolManifest(
        name=name,
        description=f"Test tool {name}",
        kind="core",
        source=source,
        permission=permission,
        inputSchema={"type": "object"},
        timeoutMs=30_000,
        availableInModes=("plan", "act"),
        enabled_by_default=True,
    )

    def _handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"ran": name})

    registry.register(manifest, handler=_handler)
    return registry


# ---------------------------------------------------------------------------
# 1. Flag accessor + flag-OFF bypass
# ---------------------------------------------------------------------------


def test_flag_default_on_in_full_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
    monkeypatch.delenv("MAGI_RUNTIME_PROFILE", raising=False)
    assert general_automation_live_gate_enabled() is True


def test_safe_profile_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MAGI_GA_LIVE_ENABLED", raising=False)
    monkeypatch.setenv("MAGI_RUNTIME_PROFILE", "safe")
    assert general_automation_live_gate_enabled() is False


@pytest.mark.parametrize("token", ["1", "true", "yes", "on", "TRUE", "On"])
def test_flag_truthy_tokens(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", token)
    assert general_automation_live_gate_enabled() is True


@pytest.mark.parametrize("token", ["0", "false", "no", "off", ""])
def test_flag_falsy_tokens(monkeypatch: pytest.MonkeyPatch, token: str) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", token)
    assert general_automation_live_gate_enabled() is False


def test_gate_inactive_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "0")
    gate = GeneralAutomationLiveGate()
    assert gate.is_active(_general_context()) is False


def _normalize(payload: dict[str, object]) -> dict[str, object]:
    """Strip the pre-existing nondeterministic permission controlRequest id.

    The existing (gate-independent) permission path mints a fresh ``uuid4`` per
    ``make_control_request``; it is unrelated to the GA gate. Dropping it lets us
    assert the *rest* of the result is byte-identical between live and baseline.
    """
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        control_request = metadata.get("controlRequest")
        if isinstance(control_request, dict):
            control_request = dict(control_request)
            control_request.pop("requestId", None)
            metadata = dict(metadata)
            metadata["controlRequest"] = control_request
            payload = dict(payload)
            payload["metadata"] = metadata
    return payload


def _baseline_dispatch(
    registry: ToolRegistry,
    name: str,
    arguments: dict[str, object],
    context: ToolContext,
) -> ToolResult:
    """Dispatch with the GA live gate forced inactive (current ``main`` behavior)."""
    inactive_gate = GeneralAutomationLiveGate()
    inactive_gate.is_active = lambda _ctx: False  # type: ignore[method-assign]
    dispatcher = ToolDispatcher(registry, general_automation_live_gate=inactive_gate)
    return asyncio.run(dispatcher.dispatch(name, arguments, context, mode="act"))


def test_flag_off_dispatch_byte_identical_to_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-OFF: dispatch result is byte-identical to a gate-disabled baseline."""
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "0")
    args = {"command": "rm -rf /workspace/bot/data"}

    live = asyncio.run(
        ToolDispatcher(_registry_with_handler("Bash")).dispatch(
            "Bash", dict(args), _general_context(), mode="act"
        )
    )
    baseline = _baseline_dispatch(
        _registry_with_handler("Bash"), "Bash", dict(args), _general_context()
    )
    assert _normalize(live.model_dump()) == _normalize(baseline.model_dump())


def test_flag_off_gate_never_classifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag-OFF: classify_pre returns an inactive outcome with no receipt."""
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "0")
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "Bash",
        {"command": "rm -rf /workspace/bot/data"},
        _general_context(),
        mode="act",
    )
    assert outcome.active is False
    assert outcome.decision == "allow"
    assert outcome.receipt is None
    assert outcome.control_projection is None


# ---------------------------------------------------------------------------
# 2. Flag-ON + general + destructive rm -rf → blocked + ShellPolicyReceipt
# ---------------------------------------------------------------------------


def test_destructive_shell_blocked_with_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "Bash",
        {"command": "rm -rf /workspace/bot/data"},
        _general_context(),
        mode="act",
    )

    assert outcome.active is True
    assert outcome.decision == "deny"
    assert isinstance(outcome.receipt, ShellPolicyReceipt)
    assert outcome.receipt.status == "blocked"
    assert "destructive_filesystem_operation_denied" in outcome.receipt.reason_codes
    assert isinstance(outcome.permission_boundary, HookPermissionBoundary)
    assert outcome.permission_boundary.decision == "deny"

    ledger = gate.append_receipt_to_ledger(_ledger(), outcome.receipt)
    assert len(ledger.entries) == 1


def test_destructive_shell_blocked_via_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    registry = _registry_with_handler("Bash")
    dispatcher = ToolDispatcher(registry)
    result = asyncio.run(
        dispatcher.dispatch(
            "Bash",
            {"command": "rm -rf /workspace/bot/data"},
            _general_context(),
            mode="act",
        )
    )
    assert result.status == "blocked"
    assert result.metadata.get("reason") != "allowed"
    # handler must NOT have run
    assert result.output != {"ran": "Bash"}


# ---------------------------------------------------------------------------
# 3. Flag-ON + general + external-dir write → pending_control_request
# ---------------------------------------------------------------------------


def test_external_dir_write_pending_control_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "FileWrite",
        {"path": "/Volumes/external/report.txt", "operationClass": "write"},
        _general_context(),
        mode="act",
    )

    assert outcome.active is True
    assert outcome.decision == "ask"
    assert isinstance(outcome.receipt, ExternalDirectoryApprovalReceipt)
    assert isinstance(outcome.permission_boundary, HookPermissionBoundary)
    assert outcome.permission_boundary.decision == "ask"
    assert outcome.control_projection is not None
    assert outcome.control_projection.control_type == "approval_required"

    ledger = gate.append_receipt_to_ledger(_ledger(), outcome.receipt)
    assert len(ledger.entries) == 1


def test_external_dir_write_needs_approval_via_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    registry = _registry_with_handler("FileWrite", permission="write")
    dispatcher = ToolDispatcher(registry)
    result = asyncio.run(
        dispatcher.dispatch(
            "FileWrite",
            {"path": "/Volumes/external/report.txt", "operationClass": "write"},
            _general_context(),
            mode="act",
        )
    )
    assert result.status == "needs_approval"
    assert result.metadata.get("controlProjection") is not None
    assert result.output != {"ran": "FileWrite"}


# ---------------------------------------------------------------------------
# 4. Flag-ON + general + workspace read → proceeds
# ---------------------------------------------------------------------------


def test_workspace_read_proceeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "FileRead",
        {"path": "notes/todo.md", "operationClass": "read"},
        _general_context(),
        mode="act",
    )
    assert outcome.active is True
    assert outcome.decision == "allow"
    assert outcome.permission_boundary is None
    assert outcome.control_projection is None


def test_workspace_read_runs_handler_via_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    registry = _registry_with_handler("FileRead", permission="read")
    dispatcher = ToolDispatcher(registry)
    result = asyncio.run(
        dispatcher.dispatch(
            "FileRead",
            {"path": "notes/todo.md", "operationClass": "read"},
            _general_context(),
            mode="act",
        )
    )
    assert result.status == "ok"
    assert result.output == {"ran": "FileRead"}


# ---------------------------------------------------------------------------
# 5. Flag-ON but non-general pack → bypass
# ---------------------------------------------------------------------------


def test_non_general_pack_bypasses_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    gate = GeneralAutomationLiveGate()
    assert gate.is_active(_coding_context()) is False
    outcome = gate.classify_pre(
        "Bash",
        {"command": "rm -rf /workspace/bot/data"},
        _coding_context(),
        mode="act",
    )
    assert outcome.active is False
    assert outcome.decision == "allow"
    assert outcome.receipt is None


def test_non_general_dispatch_byte_identical_to_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-ON but coding role: dispatch is byte-identical to gate-disabled baseline."""
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    args = {"command": "rm -rf /workspace/bot/data"}

    live = asyncio.run(
        ToolDispatcher(_registry_with_handler("Bash")).dispatch(
            "Bash", dict(args), _coding_context(), mode="act"
        )
    )
    baseline = _baseline_dispatch(
        _registry_with_handler("Bash"), "Bash", dict(args), _coding_context()
    )
    assert _normalize(live.model_dump()) == _normalize(baseline.model_dump())


# ---------------------------------------------------------------------------
# 6. Flag-ON + NO execution_contract (None) → gate inactive, pure bypass
# ---------------------------------------------------------------------------


def _no_contract_context() -> ToolContext:
    """ToolContext with no execution_contract — represents an unknown/unset role."""
    return ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract=None,
    )


def test_no_execution_contract_gate_inactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Flag-ON + no execution_contract: gate is NOT active (unset ≠ general)."""
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    gate = GeneralAutomationLiveGate()
    assert gate.is_active(_no_contract_context()) is False


def test_no_execution_contract_classify_pre_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-ON + no execution_contract: classify_pre returns inactive bypass outcome."""
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "Bash",
        {"command": "rm -rf /workspace/bot/data"},
        _no_contract_context(),
        mode="act",
    )
    assert outcome.active is False
    assert outcome.decision == "allow"
    assert outcome.receipt is None
    assert outcome.control_projection is None


def test_no_execution_contract_dispatch_byte_identical_to_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-ON + no execution_contract: dispatch is byte-identical to gate-disabled baseline."""
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    args = {"command": "rm -rf /workspace/bot/data"}

    live = asyncio.run(
        ToolDispatcher(_registry_with_handler("Bash")).dispatch(
            "Bash", dict(args), _no_contract_context(), mode="act"
        )
    )
    baseline = _baseline_dispatch(
        _registry_with_handler("Bash"), "Bash", dict(args), _no_contract_context()
    )
    assert _normalize(live.model_dump()) == _normalize(baseline.model_dump())


# ---------------------------------------------------------------------------
# 7. Flag-ON + general + path classifier "blocked" (/proc/cpuinfo) → deny, no receipt
# ---------------------------------------------------------------------------


def test_path_blocked_yields_deny_no_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-ON + general + path that is neither workspace-local nor external mount.

    ``/proc/cpuinfo`` is not under the workspace root and is not an external
    directory mount, so the path classifier returns ``blocked``.  The gate must
    produce decision=``deny`` with NO receipt (the blocked branch carries no
    ``ExternalDirectoryApprovalReceipt`` or ``ShellPolicyReceipt``, unlike the
    shell-deny and external-dir-ask paths).
    """
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "FileRead",
        {"path": "/proc/cpuinfo", "operationClass": "read"},
        _general_context(),
        mode="act",
    )

    assert outcome.active is True
    assert outcome.decision == "deny"
    assert outcome.receipt is None  # blocked path carries no receipt
    assert isinstance(outcome.permission_boundary, HookPermissionBoundary)
    assert outcome.permission_boundary.decision == "deny"
    assert outcome.control_projection is None


def test_path_blocked_yields_blocked_status_via_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag-ON + general + blocked path: dispatcher surfaces status=``blocked``."""
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")
    registry = _registry_with_handler("FileRead", permission="read")
    dispatcher = ToolDispatcher(registry)
    result = asyncio.run(
        dispatcher.dispatch(
            "FileRead",
            {"path": "/proc/cpuinfo", "operationClass": "read"},
            _general_context(),
            mode="act",
        )
    )
    assert result.status == "blocked"
    assert result.output != {"ran": "FileRead"}  # handler must NOT have run
