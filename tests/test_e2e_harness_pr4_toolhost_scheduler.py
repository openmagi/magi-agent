from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from magi_agent.artifacts.local_result_store import (
    LocalResultStore,
    LocalResultStoreConfig,
)
from magi_agent.tools.catalog import register_core_tool_manifests
from magi_agent.tools.context import ToolContext
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult


class FakeToolExecutor:
    openmagi_local_fake_provider = True

    def __init__(self, handlers: dict[str, Any]) -> None:
        self.handlers = handlers
        self.calls: list[str] = []

    async def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        self.calls.append(tool_name)
        result = self.handlers[tool_name](arguments, context)
        if hasattr(result, "__await__"):
            return await result
        return ToolResult.model_validate(result)


def _context() -> ToolContext:
    return ToolContext(
        botId="bot-pr4",
        userId="user-pr4",
        sessionId="session-pr4",
        sessionKey="context-ref-pr4",
        turnId="turn-pr4",
        workspaceRoot="local-workspace-root",
    )


def _manifest(
    name: str,
    *,
    permission: str = "read",
    input_schema: dict[str, object] | None = None,
    dangerous: bool = False,
    mutates_workspace: bool = False,
    parallel_safety: str = "readonly",
    is_concurrency_safe: bool = True,
    enabled_by_default: bool = True,
    kind: str = "native",
    source_kind: str = "native-plugin",
    budget: Budget | None = None,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} PR4 tool",
        kind=kind,
        source=ToolSource(kind=source_kind, package="test.pr4"),
        permission=permission,  # type: ignore[arg-type]
        inputSchema=input_schema or {"type": "object", "additionalProperties": True},
        dangerous=dangerous,
        mutatesWorkspace=mutates_workspace,
        parallelSafety=parallel_safety,  # type: ignore[arg-type]
        isConcurrencySafe=is_concurrency_safe,
        sideEffectClass="local_workspace" if mutates_workspace else "none",
        timeoutMs=1000,
        enabled_by_default=enabled_by_default,
        budget=budget or Budget(outputChars=32, transcriptChars=16),
    )


def test_protected_core_handler_binding_preserves_metadata_and_default_enabled_state() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    original = registry.resolve("FileRead")
    assert original is not None

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output={"path": arguments["path"], "bot": context.bot_id})

    registry.bind_handler("FileRead", handler)
    registration = registry.resolve_registration("FileRead")

    assert registration is not None
    assert registration.manifest == original
    assert registration.handler is handler
    assert registration.protected is True
    assert registration.enabled is True
    assert "FileRead" in {manifest.name for manifest in registry.list_available(mode="act")}


def test_protected_handler_binding_can_only_enable_through_explicit_policy_flag() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    calls: list[str] = []

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        calls.append(f"{context.bot_id}:{arguments['path']}")
        return ToolResult(status="ok", output="bound")

    registry.bind_handler("FileRead", handler)
    registry.bind_handler("FileRead", handler, enabled_by_registry_policy=True)

    assert registry.is_enabled("FileRead") is True
    result = asyncio.run(
        FakeToolExecutor({"FileRead": handler}).execute_tool(
            tool_name="FileRead",
            arguments={"path": "README.md"},
            context=_context(),
        )
    )
    assert result.status == "ok"
    assert calls == ["bot-pr4:README.md"]


def test_protected_handler_binding_rejects_metadata_replacement_bypass() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    original = registry.resolve("Bash")
    assert original is not None

    def handler(_arguments: dict[str, object], _context: ToolContext) -> ToolResult:
        return ToolResult(status="ok")

    with pytest.raises(ValueError, match="bind_handler does not accept manifest replacements"):
        registry.bind_handler(
            "Bash",
            handler,
            manifest=_manifest(
                "Bash",
                kind="custom",
                source_kind="external",
                permission="read",
                dangerous=False,
                mutates_workspace=False,
            ),
        )

    assert registry.resolve("Bash") == original
    assert registry.resolve_registration("Bash").handler is None  # type: ignore[union-attr]
    assert registry.is_enabled("Bash") is True


def test_protected_handler_binding_is_one_way_without_audited_policy_path() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)

    def original_handler(_arguments: dict[str, object], _context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output="original")

    def replacement_handler(_arguments: dict[str, object], _context: ToolContext) -> ToolResult:
        return ToolResult(status="ok", output="replacement")

    registry.bind_handler("FileRead", original_handler)
    registry.bind_handler("FileRead", original_handler)
    with pytest.raises(ValueError, match="protected tool handler already bound"):
        registry.bind_handler("FileRead", replacement_handler)

    registration = registry.resolve_registration("FileRead")
    assert registration is not None
    assert registration.handler is original_handler


def test_kernel_budget_store_hook_projects_local_fake_receipt_without_live_authority() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("Echo", budget=Budget(outputChars=10, transcriptChars=6)))
    store = LocalResultStore(LocalResultStoreConfig(enabled=True, localFakeStoreEnabled=True))
    executor = FakeToolExecutor(
        {
            "Echo": lambda _args, _ctx: ToolResult(
                status="ok",
                output="ABCDEFGHIJK",
                llmOutput="LLLLLLLLLLLL",
                transcriptOutput="TTTTTTTT",
            )
        }
    )

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
                outputBudgetEnabled=True,
                localFakeResultStoreEnabled=True,
            ),
            local_fake_executor=executor,
            local_result_store=store,
        ).execute(
            ToolExecutionRequest(
                toolName="Echo",
                arguments={"query": "safe"},
                context=_context(),
                mode="act",
                exposedToolNames=("Echo",),
            )
        )
    )

    assert outcome.status == "ok"
    assert outcome.output_projection is not None
    assert outcome.output_projection["llmPreview"] == "L" * 10
    assert outcome.output_projection["transcriptPreview"] == "T" * 6
    assert outcome.output_projection["storeRef"].startswith("result:sha256:")
    assert set(outcome.output_projection["authorityFlags"].values()) == {False}
    assert store.get(outcome.output_projection["storeRef"]) is not None
    assert store.production_write_count == 0
    assert outcome.authority_flags.tool_dispatch_allowed is False


def test_kernel_budget_store_hook_rejects_untrusted_store_before_raw_result_handoff() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    class UntrustedStore:
        def __init__(self) -> None:
            self.calls = 0

        def put_tool_result(self, _result: object, *, metadata: dict[str, object]) -> object:
            _ = metadata
            self.calls += 1
            return object()

    class SpoofedStore(UntrustedStore):
        openmagi_local_fake_provider = True

    class SubclassedStore(LocalResultStore):
        def __init__(self) -> None:
            super().__init__(LocalResultStoreConfig(enabled=True, localFakeStoreEnabled=True))
            self.raw_payloads: list[object] = []

        def put_tool_result(self, result: object, *, metadata: dict[str, object]) -> object:
            _ = metadata
            self.raw_payloads.append(result)
            return object()

    registry = ToolRegistry()
    registry.register(_manifest("Echo", budget=Budget(outputChars=10, transcriptChars=6)))
    executor = FakeToolExecutor(
        {"Echo": lambda _args, _ctx: ToolResult(status="ok", output="raw-result-body")}
    )

    stores = (UntrustedStore(), SpoofedStore(), SubclassedStore())
    for store in stores:
        outcome = asyncio.run(
            ToolExecutionKernel(
                registry,
                config=ToolExecutionKernelConfig(
                    enabled=True,
                    localFakeHandlerExecutionEnabled=True,
                    outputBudgetEnabled=True,
                    localFakeResultStoreEnabled=True,
                ),
                local_fake_executor=executor,
                local_result_store=store,
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

        assert outcome.status == "ok"
        assert outcome.output_projection is not None
        assert outcome.output_projection["storeRef"] is None
        assert set(outcome.output_projection["authorityFlags"].values()) == {False}

    assert stores[0].calls == 0
    assert stores[1].calls == 0
    assert stores[2].raw_payloads == []


def test_kernel_budget_store_hook_ignores_instance_method_rebinding() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )

    registry = ToolRegistry()
    registry.register(_manifest("Echo", budget=Budget(outputChars=10, transcriptChars=6)))
    store = LocalResultStore(LocalResultStoreConfig(enabled=True, localFakeStoreEnabled=True))
    captured: list[object] = []

    def capture_raw_result(_result: object, *, metadata: dict[str, object]) -> object:
        _ = metadata
        captured.append(_result)
        return object()

    store.put_tool_result = capture_raw_result  # type: ignore[method-assign]
    executor = FakeToolExecutor(
        {"Echo": lambda _args, _ctx: ToolResult(status="ok", output="raw-result-body")}
    )

    outcome = asyncio.run(
        ToolExecutionKernel(
            registry,
            config=ToolExecutionKernelConfig(
                enabled=True,
                localFakeHandlerExecutionEnabled=True,
                outputBudgetEnabled=True,
                localFakeResultStoreEnabled=True,
            ),
            local_fake_executor=executor,
            local_result_store=store,
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

    assert outcome.status == "ok"
    assert outcome.output_projection is not None
    assert outcome.output_projection["storeRef"].startswith("result:sha256:")
    assert captured == []
    assert store.get(outcome.output_projection["storeRef"]) is not None


def test_scheduler_default_off_and_rejects_live_config() -> None:
    from magi_agent.tools.scheduler import ToolScheduler, ToolSchedulerConfig

    with pytest.raises(ValidationError):
        ToolSchedulerConfig(enabled=True, liveExecutionEnabled=True)

    scheduler = ToolScheduler()
    outcome = asyncio.run(scheduler.execute(()))

    assert outcome.status == "disabled"
    assert outcome.reason_codes == ("tool_scheduler_disabled",)
    assert outcome.results == ()
    assert set(outcome.authority_flags.model_dump(by_alias=True).values()) == {False}


def test_scheduler_groups_safe_tasks_serializes_unsafe_and_preserves_input_order() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )
    from magi_agent.tools.scheduler import (
        ToolScheduleTask,
        ToolScheduler,
        ToolSchedulerConfig,
    )

    async def slow(_args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(0.02)
        return ToolResult(status="ok", output="slow")

    async def fast(_args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        await asyncio.sleep(0)
        return ToolResult(status="ok", output="fast")

    registry = ToolRegistry()
    registry.register(_manifest("SlowRead"))
    registry.register(_manifest("FastRead"))
    registry.register(
        _manifest(
            "UnsafeWrite",
            permission="write",
            mutates_workspace=True,
            parallel_safety="unsafe",
            is_concurrency_safe=False,
        )
    )
    executor = FakeToolExecutor(
        {
            "SlowRead": slow,
            "FastRead": fast,
            "UnsafeWrite": lambda _args, _ctx: ToolResult(status="blocked"),
        }
    )
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )
    scheduler = ToolScheduler(
        kernel=kernel,
        registry=registry,
        config=ToolSchedulerConfig(enabled=True, localFakeSchedulerEnabled=True),
    )

    outcome = asyncio.run(
        scheduler.execute(
            (
                ToolScheduleTask(
                    request=ToolExecutionRequest(
                        toolName="SlowRead",
                        arguments={},
                        context=_context(),
                        mode="act",
                        exposedToolNames=("SlowRead", "FastRead", "UnsafeWrite"),
                    )
                ),
                ToolScheduleTask(
                    request=ToolExecutionRequest(
                        toolName="FastRead",
                        arguments={},
                        context=_context(),
                        mode="act",
                        exposedToolNames=("SlowRead", "FastRead", "UnsafeWrite"),
                    )
                ),
                ToolScheduleTask(
                    request=ToolExecutionRequest(
                        toolName="UnsafeWrite",
                        arguments={"path": "README.md"},
                        context=_context(),
                        mode="act",
                        exposedToolNames=("SlowRead", "FastRead", "UnsafeWrite"),
                    )
                ),
            )
        )
    )

    assert outcome.status == "blocked"
    assert outcome.reason_codes == ("tool_approval_required",)
    assert [step.mode for step in outcome.steps] == ["parallel", "parallel", "serial"]
    assert [result.result.output for result in outcome.results] == ["slow", "fast", None]
    assert executor.calls[:2] == ["SlowRead", "FastRead"]
    assert executor.calls[2:] == []


def test_scheduler_max_parallel_budget_serializes_without_claiming_failure() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )
    from magi_agent.tools.scheduler import (
        ToolScheduleTask,
        ToolScheduler,
        ToolSchedulerConfig,
    )

    registry = ToolRegistry()
    registry.register(_manifest("LimitedRead", budget=Budget(max_parallel=1)))
    executor = FakeToolExecutor(
        {"LimitedRead": lambda _args, _ctx: ToolResult(status="ok", output="limited")}
    )
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )

    request = ToolExecutionRequest(
        toolName="LimitedRead",
        arguments={},
        context=_context(),
        mode="act",
        exposedToolNames=("LimitedRead",),
    )
    outcome = asyncio.run(
        ToolScheduler(
            kernel=kernel,
            registry=registry,
            config=ToolSchedulerConfig(enabled=True, localFakeSchedulerEnabled=True),
        ).execute(
            (
                ToolScheduleTask(taskId="limited-1", request=request),
                ToolScheduleTask(taskId="limited-2", request=request),
            )
        )
    )

    assert outcome.status == "ok"
    assert outcome.reason_codes == ()
    assert [step.status for step in outcome.steps] == ["scheduled", "budget_serialized"]
    assert [step.mode for step in outcome.steps] == ["parallel", "serial"]
    assert outcome.steps[1].reason_codes == ("max_parallel_serialized",)
    assert [result.status for result in outcome.results] == ["ok", "ok"]
    assert executor.calls == ["LimitedRead", "LimitedRead"]


def test_scheduler_result_status_degrades_when_handler_outcome_is_not_ok() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )
    from magi_agent.tools.scheduler import (
        ToolScheduleTask,
        ToolScheduler,
        ToolSchedulerConfig,
    )

    registry = ToolRegistry()
    registry.register(_manifest("ValidationRead"))
    executor = FakeToolExecutor(
        {
            "ValidationRead": lambda _args, _ctx: ToolResult(
                status="blocked",
                metadata={"reason": "fixture validator blocked"},
            )
        }
    )
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )

    outcome = asyncio.run(
        ToolScheduler(
            kernel=kernel,
            registry=registry,
            config=ToolSchedulerConfig(enabled=True, localFakeSchedulerEnabled=True),
        ).execute(
            (
                ToolScheduleTask(
                    request=ToolExecutionRequest(
                        toolName="ValidationRead",
                        arguments={},
                        context=_context(),
                        mode="act",
                        exposedToolNames=("ValidationRead",),
                    )
                ),
            )
        )
    )

    assert outcome.status == "blocked"
    assert outcome.reason_codes == ("tool_result_blocked",)
    assert outcome.results[0].status == "blocked"


def test_scheduler_blocks_invalid_schema_before_planning_or_handler() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )
    from magi_agent.tools.scheduler import (
        ToolScheduleTask,
        ToolScheduler,
        ToolSchedulerConfig,
    )

    registry = ToolRegistry()
    registry.register(
        _manifest(
            "StrictRead",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
                "additionalProperties": False,
            },
        )
    )
    executor = FakeToolExecutor(
        {"StrictRead": lambda _args, _ctx: ToolResult(status="ok", output="unexpected")}
    )
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )

    outcome = asyncio.run(
        ToolScheduler(
            kernel=kernel,
            registry=registry,
            config=ToolSchedulerConfig(enabled=True, localFakeSchedulerEnabled=True),
        ).execute(
            (
                ToolScheduleTask(
                    request=ToolExecutionRequest(
                        toolName="StrictRead",
                        arguments={"unexpected": "blocked"},
                        context=_context(),
                        mode="act",
                        exposedToolNames=("StrictRead",),
                    )
                ),
            )
        )
    )

    assert outcome.status == "blocked"
    assert outcome.reason_codes == ("tool_input_schema_invalid",)
    assert outcome.steps[0].status == "blocked"
    assert outcome.results[0].handler_called is False
    assert executor.calls == []


def test_scheduler_duplicate_and_conflict_statuses_are_deterministic() -> None:
    from magi_agent.tools.kernel import (
        ToolExecutionKernel,
        ToolExecutionKernelConfig,
        ToolExecutionRequest,
    )
    from magi_agent.tools.scheduler import (
        ToolScheduleTask,
        ToolScheduler,
        ToolSchedulerConfig,
    )

    registry = ToolRegistry()
    registry.register(_manifest("ReadA"))
    registry.register(_manifest("ReadB"))
    executor = FakeToolExecutor(
        {
            "ReadA": lambda _args, _ctx: ToolResult(status="ok", output="a"),
            "ReadB": lambda _args, _ctx: ToolResult(status="ok", output="b"),
        }
    )
    kernel = ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(enabled=True, localFakeHandlerExecutionEnabled=True),
        local_fake_executor=executor,
    )
    tasks = (
        ToolScheduleTask(
            taskId="same",
            conflictKey="shared-read",
            request=ToolExecutionRequest(
                toolName="ReadA",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("ReadA", "ReadB"),
            ),
        ),
        ToolScheduleTask(
            taskId="same",
            conflictKey="shared-read",
            request=ToolExecutionRequest(
                toolName="ReadB",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("ReadA", "ReadB"),
            ),
        ),
        ToolScheduleTask(
            taskId="unique",
            conflictKey="shared-read",
            request=ToolExecutionRequest(
                toolName="ReadB",
                arguments={},
                context=_context(),
                mode="act",
                exposedToolNames=("ReadA", "ReadB"),
            ),
        ),
    )

    outcome = asyncio.run(
        ToolScheduler(
            kernel=kernel,
            registry=registry,
            config=ToolSchedulerConfig(enabled=True, localFakeSchedulerEnabled=True),
        ).execute(tasks)
    )

    assert [step.status for step in outcome.steps] == [
        "scheduled",
        "duplicate_blocked",
        "conflict_serialized",
    ]
    assert [result.reason_code for result in outcome.results] == [
        "tool_executed",
        "duplicate_task_blocked",
        "tool_executed",
    ]
    assert [result.result.output for result in outcome.results] == ["a", None, "b"]
    assert executor.calls == ["ReadA", "ReadB"]


def test_strategy_metadata_is_injected_by_harness_not_core_scheduler() -> None:
    from magi_agent.tools.scheduler import (
        ToolScheduleTask,
        ToolScheduler,
        ToolSchedulerConfig,
    )

    scheduler = ToolScheduler(config=ToolSchedulerConfig(enabled=True, localFakeSchedulerEnabled=True))
    snapshots = []
    for strategy in ("coding", "research", "general_automation"):
        task = ToolScheduleTask(
            taskId=f"{strategy}-task",
            strategyMetadata={"strategy": strategy, "recipeRef": f"recipe:{strategy}"},
            request={
                "toolName": "Missing",
                "arguments": {},
                "context": _context(),
                "mode": "act",
            },
        )
        snapshots.append(task.strategy_metadata)

    assert [snapshot["strategy"] for snapshot in snapshots] == [
        "coding",
        "research",
        "general_automation",
    ]
    assert scheduler.core_strategy_names == ()


def test_pr4_scheduler_and_kernel_import_boundaries_avoid_live_runtime_modules() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import importlib
import sys

before = set(sys.modules)
for module_name in (
    "magi_agent.tools.kernel",
    "magi_agent.tools.scheduler",
):
    imported = importlib.import_module(module_name)
    assert imported is not None

forbidden_exact = (
    "google.adk.runners",
    "google.adk.agents",
    "google.adk.sessions",
    "google.adk.models",
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
    "magi_agent.runtime.openmagi_runtime",
    "magi_agent.transport.chat",
    "magi_agent.memory.adk_bridge",
)
loaded = [
    name
    for name in set(sys.modules) - before
    if name in forbidden_exact
    or any(name.startswith(f"{prefix}.") for prefix in forbidden_exact)
]
if loaded:
    raise AssertionError(f"PR4 modules loaded forbidden modules: {loaded}")
""",
        ],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
