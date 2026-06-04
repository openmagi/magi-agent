"""Track 19 PR11 — path_policy read/write distinction.

Tests pin the new contract:

* workspace-local READ / LIST  → approvalRequired=False, reason "workspace_local_access"
* workspace-local WRITE / DELETE → approvalRequired=True, reason "workspace_write_requires_approval"
* workspace-local EXECUTE → approvalRequired=True, reason "workspace_write_requires_approval"
  (execute is mutation-class: running a workspace file is as consequential as writing one;
  applying the same approval posture closes the "write then execute" gap silently)
* external_directory paths → unchanged (always approvalRequired=True)
* blocked paths → unchanged (approvalRequired=False)

Through the live_gate consumer (PR2 / live_gate.py):
* workspace write  → classify_pre returns decision="ask" + control_projection (NO receipt)
* workspace read   → classify_pre returns decision="allow"
"""
from __future__ import annotations

import asyncio
import json

import pytest

from magi_agent.harness.general_automation.path_policy import (
    PathAccessRequest,
    classify_path_access,
)


WORKSPACE_ROOT = "/workspace/bot"
HOME_DIR = "/Users/acme"


def _req(
    path: str,
    *,
    operation: str = "read",
    workspace_root: str = WORKSPACE_ROOT,
    home_dir: str | None = HOME_DIR,
) -> PathAccessRequest:
    return PathAccessRequest(
        workspaceRoot=workspace_root,
        homeDir=home_dir,
        path=path,
        operationClass=operation,
    )


# ---------------------------------------------------------------------------
# 1. workspace-local READ → silent (approvalRequired=False)
# ---------------------------------------------------------------------------


def test_workspace_local_read_no_approval() -> None:
    decision = classify_path_access(_req("notes/todo.md", operation="read"))

    assert decision.status == "workspace_local"
    assert decision.approval_required is False
    assert decision.reason_codes == ("workspace_local_access",)
    assert decision.operation_class == "read"
    # digest-only: raw path must not appear
    assert "notes/todo.md" not in decision.public_projection().get("pathDigest", "")


def test_workspace_local_list_no_approval() -> None:
    decision = classify_path_access(_req("reports/", operation="list"))

    assert decision.status == "workspace_local"
    assert decision.approval_required is False
    assert decision.reason_codes == ("workspace_local_access",)
    assert decision.operation_class == "list"


# ---------------------------------------------------------------------------
# 2. workspace-local WRITE → requires approval
# ---------------------------------------------------------------------------


def test_workspace_local_write_requires_approval() -> None:
    decision = classify_path_access(_req("output/result.csv", operation="write"))

    assert decision.status == "workspace_local"
    assert decision.approval_required is True
    assert "workspace_write_requires_approval" in decision.reason_codes
    assert decision.operation_class == "write"


def test_workspace_local_write_public_projection_approval_flag() -> None:
    decision = classify_path_access(_req("output/result.csv", operation="write"))
    public = decision.public_projection()

    assert public["status"] == "workspace_local"
    assert public["approvalRequired"] is True
    assert "workspace_write_requires_approval" in public["reasonCodes"]
    # digest-only: no raw path in projection
    assert "output/result.csv" not in str(public.get("pathDigest", ""))


# ---------------------------------------------------------------------------
# 3. workspace-local DELETE → requires approval
# ---------------------------------------------------------------------------


def test_workspace_local_delete_requires_approval() -> None:
    decision = classify_path_access(_req("tmp/draft.txt", operation="delete"))

    assert decision.status == "workspace_local"
    assert decision.approval_required is True
    assert "workspace_write_requires_approval" in decision.reason_codes
    assert decision.operation_class == "delete"


# ---------------------------------------------------------------------------
# 4. workspace-local EXECUTE → requires approval
#    Rationale: executing a workspace file is mutation-class (side-effects same
#    magnitude as write). Treat as write-tier to close the write→execute gap.
# ---------------------------------------------------------------------------


def test_workspace_local_execute_requires_approval() -> None:
    decision = classify_path_access(_req("scripts/deploy.sh", operation="execute"))

    assert decision.status == "workspace_local"
    assert decision.approval_required is True
    assert "workspace_write_requires_approval" in decision.reason_codes
    assert decision.operation_class == "execute"


# ---------------------------------------------------------------------------
# 5. external_directory paths → unchanged (always approval required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_path", "expected_prefix"),
    (
        ("~/Downloads/report.pdf", "/Users/acme/Downloads"),
        ("/tmp/upload.csv", "/tmp"),
        ("/Volumes/Backup/data.xlsx", "/Volumes/Backup"),
        ("/mnt/share/file.txt", "/mnt/share"),
    ),
)
def test_external_directory_always_requires_approval(
    raw_path: str,
    expected_prefix: str,
) -> None:
    for op in ("read", "write", "list", "delete", "execute"):
        decision = classify_path_access(_req(raw_path, operation=op))
        assert decision.status == "external_directory", f"op={op}"
        assert decision.approval_required is True, f"op={op}"
        assert decision.canonical_path_prefix == expected_prefix, f"op={op}"
        assert decision.reason_codes == ("external_directory_approval_required",), f"op={op}"


# ---------------------------------------------------------------------------
# 6. blocked paths → unchanged (approvalRequired=False)
# ---------------------------------------------------------------------------


def test_blocked_path_no_approval() -> None:
    decision = classify_path_access(_req("/proc/cpuinfo", operation="read"))

    assert decision.status == "blocked"
    assert decision.approval_required is False
    assert "unsupported_external_path" in decision.reason_codes


def test_blocked_path_write_still_no_approval() -> None:
    # "blocked" means the path class is outright rejected — no receipt path.
    # approval_required stays False because blocking is not the "ask" path.
    decision = classify_path_access(_req("/etc/passwd", operation="write"))

    assert decision.status == "blocked"
    assert decision.approval_required is False


# ---------------------------------------------------------------------------
# 7. digest-only invariant: raw path never leaks in public projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("operation", ["read", "write", "delete", "list", "execute"])
def test_workspace_local_public_projection_never_contains_raw_path(
    operation: str,
) -> None:
    raw = "secret/credentials.json"
    decision = classify_path_access(_req(raw, operation=operation))
    public = decision.public_projection()
    serialized = json.dumps(public)
    assert raw not in serialized
    assert WORKSPACE_ROOT not in serialized


# ---------------------------------------------------------------------------
# 8. Through live_gate consumer: workspace write → ask, read → allow
# ---------------------------------------------------------------------------


def test_live_gate_workspace_write_yields_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")

    from magi_agent.harness.general_automation.live_gate import GeneralAutomationLiveGate
    from magi_agent.tools.context import ToolContext

    context = ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract={"agentRole": "general"},
    )
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "FileWrite",
        {"path": "output/result.csv", "operationClass": "write"},
        context,
        mode="act",
    )

    assert outcome.active is True
    assert outcome.decision == "ask"
    assert outcome.permission_boundary is not None
    assert outcome.permission_boundary.decision == "ask"
    assert outcome.control_projection is not None
    assert outcome.control_projection.control_type == "approval_required"
    # Workspace writes produce NO ExternalDirectoryApprovalReceipt
    assert outcome.receipt is None


def test_live_gate_workspace_write_via_dispatcher_needs_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")

    from magi_agent.harness.general_automation.live_gate import GeneralAutomationLiveGate
    from magi_agent.tools.context import ToolContext
    from magi_agent.tools.dispatcher import ToolDispatcher
    from magi_agent.tools.manifest import ToolManifest, ToolSource
    from magi_agent.tools.registry import ToolRegistry
    from magi_agent.tools.result import ToolResult

    registry = ToolRegistry()
    source = ToolSource(kind="builtin", package="test")
    manifest = ToolManifest(
        name="FileWrite",
        description="Test file write",
        kind="core",
        source=source,
        permission="write",
        inputSchema={"type": "object"},
        timeoutMs=30_000,
        availableInModes=("plan", "act"),
        enabled_by_default=True,
    )

    def _handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"ran": "FileWrite"})

    registry.register(manifest, handler=_handler)

    context = ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract={"agentRole": "general"},
    )
    dispatcher = ToolDispatcher(registry)
    result = asyncio.run(
        dispatcher.dispatch(
            "FileWrite",
            {"path": "output/result.csv", "operationClass": "write"},
            context,
            mode="act",
        )
    )
    # Handler must NOT have run — we need approval first
    assert result.status == "needs_approval"
    assert result.output != {"ran": "FileWrite"}
    assert result.metadata.get("controlProjection") is not None


def test_live_gate_workspace_read_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")

    from magi_agent.harness.general_automation.live_gate import GeneralAutomationLiveGate
    from magi_agent.tools.context import ToolContext

    context = ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract={"agentRole": "general"},
    )
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "FileRead",
        {"path": "notes/todo.md", "operationClass": "read"},
        context,
        mode="act",
    )

    assert outcome.active is True
    assert outcome.decision == "allow"
    assert outcome.permission_boundary is None
    assert outcome.control_projection is None


def test_live_gate_workspace_delete_yields_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")

    from magi_agent.harness.general_automation.live_gate import GeneralAutomationLiveGate
    from magi_agent.tools.context import ToolContext

    context = ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract={"agentRole": "general"},
    )
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "FileDelete",
        {"path": "tmp/draft.txt", "operationClass": "delete"},
        context,
        mode="act",
    )

    assert outcome.active is True
    assert outcome.decision == "ask"
    assert outcome.permission_boundary is not None
    assert outcome.permission_boundary.decision == "ask"


def test_live_gate_workspace_list_proceeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAGI_GA_LIVE_ENABLED", "1")

    from magi_agent.harness.general_automation.live_gate import GeneralAutomationLiveGate
    from magi_agent.tools.context import ToolContext

    context = ToolContext(
        botId="test-bot",
        turnId="turn-1",
        workspaceRoot=WORKSPACE_ROOT,
        executionContract={"agentRole": "general"},
    )
    gate = GeneralAutomationLiveGate()
    outcome = gate.classify_pre(
        "ListFiles",
        {"path": "reports/", "operationClass": "list"},
        context,
        mode="act",
    )

    assert outcome.active is True
    assert outcome.decision == "allow"
    assert outcome.permission_boundary is None
    assert outcome.control_projection is None
