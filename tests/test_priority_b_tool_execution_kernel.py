from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


class FakeToolExecutor:
    openmagi_local_fake_provider = True

    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers
        self.calls: list[dict[str, object]] = []

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        self.calls.append({"toolName": tool_name, "arguments": arguments})
        return self.handlers[tool_name](arguments, context)


def _manifest(
    name: str,
    *,
    permission: str = "read",
    dangerous: bool = False,
    mutates_workspace: bool = False,
    timeout_ms: int = 1000,
    enabled_by_default: bool = True,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind="native",
        source=ToolSource(kind="native-plugin", package="test"),
        permission=permission,  # type: ignore[arg-type]
        inputSchema={"type": "object"},
        dangerous=dangerous,
        mutatesWorkspace=mutates_workspace,
        timeoutMs=timeout_ms,
        enabled_by_default=enabled_by_default,
    )


def _context() -> ToolContext:
    return ToolContext(
        botId="bot-test",
        userId="user-test",
        sessionId="session-test",
        sessionKey="session://chat/session-test",
        turnId="turn-test",
        workspaceRoot="/workspace/private",
    )


def test_kernel_is_default_off_and_does_not_call_handler() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionRequest,
    )

    calls: list[dict[str, object]] = []
    registry = ToolRegistry()
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {"Echo": lambda args, _ctx: calls.append(args) or ToolResult(status="ok", output=args)}
    )

    outcome = asyncio.run(
        ToolExecutionKernel(registry, local_fake_executor=executor).execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={"text": "safe"},
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )

    assert outcome.status == "blocked"
    assert outcome.reason_code == "tool_execution_disabled"
    assert outcome.handler_called is False
    assert outcome.executed is False
    assert calls == []
    assert outcome.result.status == "blocked"
    assert outcome.request_ledger_result.recorded is False
    assert outcome.authority_flags.tool_dispatch_allowed is False
    assert outcome.authority_flags.production_write_allowed is False
    assert outcome.authority_flags.user_visible_output_allowed is False


def test_kernel_records_request_shape_and_runs_fake_handler_only_when_enabled() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    calls: list[dict[str, object]] = []
    registry = ToolRegistry()
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {"Echo": lambda args, _ctx: calls.append(args) or ToolResult(status="ok", output="done")}
    )

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
                requestLedgerEnabled=True,
            ),
            local_fake_executor=executor,
        ).execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={
                    "text": "ok",
                    "Authorization": "Bearer live-token",
                    "path": "/workspace/private",
                },
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
                evidenceRefs=("evidence://recipe/e-1",),
            )
        )
    )

    assert outcome.status == "ok"
    assert outcome.reason_code == "tool_executed"
    assert outcome.handler_called is True
    assert outcome.executed is True
    assert calls == [
        {
            "text": "ok",
            "Authorization": "Bearer live-token",
            "path": "/workspace/private",
        }
    ]
    assert outcome.request_ledger_result.recorded is True
    assert outcome.request_ledger_result.entry is not None
    assert outcome.request_ledger_result.entry.tool_refs == ("tool:Echo",)
    assert outcome.request_ledger_result.entry.evidence_refs == ("evidence://recipe/e-1",)
    assert [record.kind for record in outcome.evidence_records] == [
        "tool_call",
        "tool_result",
    ]
    dumped = outcome.model_dump(by_alias=True)
    assert "live-token" not in str(dumped)
    assert "/workspace/private" not in str(dumped)
    assert outcome.authority_flags.tool_dispatch_allowed is False
    assert executor.calls[0]["toolName"] == "Echo"


def test_kernel_redacts_approval_metadata_and_fake_result_payloads() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("WriteFile", permission="write", mutates_workspace=True))
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {
            "Echo": lambda _args, _ctx: ToolResult(
                status="ok",
                output="raw /workspace/private sk-live-secret",
                llmOutput="Authorization: Bearer live-token",
                transcriptOutput="Cookie: sid=opaque",
                metadata={"path": "/Users/kevin/private"},
            )
        }
    )
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )

    approval = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="WriteFile",
                arguments={
                    "path": "/workspace/private",
                    "Authorization": "Bearer live-token",
                },
                context=_context(),
                mode="act",
                exposedToolNames=("WriteFile",),
            )
        )
    )
    ok = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={"text": "safe"},
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )
    dumped = f"{approval.model_dump(by_alias=True)} {ok.model_dump(by_alias=True)}"

    for forbidden in (
        "/workspace/private",
        "/Users/kevin",
        "sk-live-secret",
        "live-token",
        "sid=opaque",
        "Authorization",
    ):
        assert forbidden not in dumped


def test_kernel_redacts_tool_call_and_control_identifier_side_channels() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("WriteFile", permission="write", mutates_workspace=True))
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {
            "Echo": lambda _args, _ctx: ToolResult(
                status="ok",
                artifactRefs=("/Users/kevin/private/artifact.txt",),
                fileRefs=("Bearer raw-file-ref",),
                deliveryReceipts=("sk-live-receipt",),
            )
        }
    )
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )

    approval = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="WriteFile",
                toolCallId="/Users/kevin/private/tool-call",
                arguments={"path": "README.md", "content": "safe"},
                context=_context(),
                mode="act",
                exposedToolNames=("WriteFile",),
            )
        )
    )
    ok = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="Echo",
                toolCallId="Bearer live-token",
                arguments={"text": "safe"},
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )
    dumped = f"{approval.model_dump(by_alias=True)} {ok.model_dump(by_alias=True)}"

    for forbidden in (
        "/Users/kevin",
        "Bearer live-token",
        "Bearer raw-file-ref",
        "sk-live-receipt",
        "turn-test",
    ):
        assert forbidden not in dumped
    assert approval.approval_gate is not None
    assert all(ref.startswith("control:") for ref in approval.approval_gate.control_refs)


def test_kernel_redacts_private_path_tails_in_tool_results() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {
            "Echo": lambda _args, _ctx: ToolResult(
                status="ok",
                output="/workspace/secrets.env /Users/kevin/secrets.env /data/bots/bot-1/secrets.env",
            )
        }
    )

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
            local_fake_executor=executor,
        ).execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert "secrets.env" not in dumped
    assert "/workspace" not in dumped
    assert "/Users/kevin" not in dumped
    assert "/data/bots" not in dumped


def test_kernel_redacts_home_and_kubelet_paths_in_tool_results() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {
            "Echo": lambda _args, _ctx: ToolResult(
                status="ok",
                output="/home/kevin/.ssh/id_rsa /var/lib/kubelet/pods/x/token",
            )
        }
    )

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
            local_fake_executor=executor,
        ).execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )
    dumped = outcome.model_dump_json(by_alias=True)

    assert "/home/kevin" not in dumped
    assert "/var/lib/kubelet" not in dumped
    assert "id_rsa" not in dumped


def test_kernel_redacts_private_evidence_summary_keys() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {
            "Echo": lambda _args, _ctx: ToolResult(
                status="ok",
                output={
                    "/Users/kevin/private-output": "value",
                    "Authorization: Bearer result-token": "value",
                },
            )
        }
    )

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
            local_fake_executor=executor,
        ).execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={
                    "/Users/kevin/private-arg": "value",
                    "Authorization: Bearer arg-token": "value",
                },
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )
    dumped = outcome.model_dump_json(by_alias=True)

    for forbidden in (
        "/Users/kevin",
        "Authorization",
        "arg-token",
        "result-token",
    ):
        assert forbidden not in dumped


def test_kernel_redacts_key_named_tool_result_credentials() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("Echo"))
    executor = FakeToolExecutor(
        {
            "Echo": lambda _args, _ctx: ToolResult(
                status="ok",
                output={
                    "metadata": {
                        "serviceKey": "plain-service-secret",
                        "service_key": "plain-service-secret-snake",
                        "credentialId": "plain-credential-id",
                        "apiKey": "plain-api-key",
                        "safeCount": 1,
                    }
                },
                metadata={
                    "serviceKey": "plain-service-secret",
                    "credentialId": "plain-credential-id",
                    "safeCount": 1,
                },
            )
        }
    )

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
            local_fake_executor=executor,
        ).execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )
    dumped = outcome.model_dump_json(by_alias=True)

    for forbidden in (
        "plain-service-secret",
        "plain-service-secret-snake",
        "plain-credential-id",
        "plain-api-key",
    ):
        assert forbidden not in dumped
    assert outcome.result.metadata["safeCount"] == 1


def test_kernel_blocks_before_handler_for_policy_failures() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    calls: list[str] = []
    registry = ToolRegistry()
    registry.register(_manifest("SafeRead"))
    registry.register(_manifest("WriteFile", permission="write", mutates_workspace=True))
    executor = FakeToolExecutor(
        {
            "SafeRead": lambda _args, _ctx: calls.append("called") or ToolResult(status="ok"),
            "WriteFile": lambda _args, _ctx: calls.append("write") or ToolResult(status="ok"),
        }
    )

    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )
    missing = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="Missing",
                arguments={},
                context=_context(),
                mode="act",
            )
        )
    )
    not_exposed = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="SafeRead",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("Other",),
            )
        )
    )
    approval = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="WriteFile",
                arguments={"path": "README.md", "content": "x"},
                context=_context(),
                mode="act",
                exposedToolNames=("WriteFile",),
            )
        )
    )

    assert missing.status == "error"
    assert missing.reason_code == "tool_not_found"
    assert not_exposed.status == "error"
    assert not_exposed.reason_code == "tool_not_exposed"
    assert approval.status == "needs_approval"
    assert approval.approval_gate is not None
    assert approval.approval_gate.status == "pending"
    assert approval.approval_gate.execute_allowed is False
    assert calls == []
    for outcome in (missing, not_exposed, approval):
        assert outcome.handler_called is False
        assert outcome.executed is False
        assert outcome.evidence_records


def test_kernel_normalizes_handler_exception_and_timeout_without_raw_leakage() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    async def slow_handler(_args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(0.05)
        return ToolResult(status="ok", output="late")

    def throwing_handler(_args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        raise RuntimeError("secret failure from /workspace/private sk-live-secret")

    registry = ToolRegistry()
    registry.register(_manifest("Slow", timeout_ms=1))
    registry.register(_manifest("Throwing"))
    executor = FakeToolExecutor({"Slow": slow_handler, "Throwing": throwing_handler})
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )

    timeout = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="Slow",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("Slow",),
            )
        )
    )
    thrown = asyncio.run(
        kernel.execute(
            ToolExecutionRequest(
                toolName="Throwing",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("Throwing",),
            )
        )
    )

    assert timeout.status == "error"
    assert timeout.reason_code == "tool_timeout"
    assert timeout.result.error_code == "tool_timeout"
    assert thrown.status == "error"
    assert thrown.reason_code == "tool_exception"
    assert thrown.result.error_code == "tool_threw"
    dumped = f"{timeout.model_dump(by_alias=True)} {thrown.model_dump(by_alias=True)}"
    assert "/workspace/private" not in dumped
    assert "sk-live-secret" not in dumped


def test_kernel_propagates_cancellation_without_normalizing_as_tool_failure() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    def cancelling_handler(_args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        raise asyncio.CancelledError

    registry = ToolRegistry()
    registry.register(_manifest("Cancel"))
    executor = FakeToolExecutor({"Cancel": cancelling_handler})

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ToolExecutionKernel(
                registry,
                config=ToolExecutionKernelConfig(
                    enabled=True,
                    localFakeHandlerExecutionEnabled=True,
                ),
                local_fake_executor=executor,
            ).execute(
                ToolExecutionRequest(
                    toolName="Cancel",
                    arguments={},
                    context=_context(),
                    mode="act",
                    exposedToolNames=("Cancel",),
                )
            )
        )


def test_tool_kernel_import_boundary_avoids_live_runtime_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

before = set(sys.modules)
module = importlib.import_module("magi_agent.tools.kernel")
assert hasattr(module, "ToolExecutionKernel")

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.tools",
    "fastapi",
    "uvicorn",
    "supabase",
    "psycopg",
    "asyncpg",
    "kubernetes",
    "httpx",
    "requests",
    "magi_agent.adk_bridge.runner_adapter",
    "magi_agent.transport.chat",
    "magi_agent.transport.sse",
    "magi_agent.memory.adk_bridge",
    "magi_agent.workspace.adoption_boundary",
)
loaded = [
    name
    for name in set(sys.modules) - before
    if name in forbidden_exact
    or any(name.startswith(f"{prefix}.") for prefix in forbidden_exact)
]
if loaded:
    raise AssertionError(f"tool kernel loaded forbidden modules: {loaded}")
""",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    source = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "tools"
        / "kernel.py"
    ).read_text(encoding="utf-8")
    assert "registration.handler(" not in source
