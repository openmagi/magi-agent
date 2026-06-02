from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from magi_agent.artifacts.local_result_store import (
    LocalResultStore,
    LocalResultStoreConfig,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.event_projection import (
    project_tool_execution_events,
    project_tool_terminal_events,
)
from magi_agent.tools.kernel import (
    ToolExecutionKernel,
    ToolExecutionKernelConfig,
    ToolExecutionRequest,
)
from magi_agent.tools.manifest import Budget, ToolManifest, ToolSource
from magi_agent.tools.registry import ToolRegistry
from magi_agent.tools.result import ToolResult
from magi_agent.transport.sse import InMemorySseWriter


class FakeToolExecutor:
    openmagi_local_fake_provider = True

    def __init__(self, handlers: dict[str, Callable[..., object]]) -> None:
        self.handlers = handlers

    async def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        context: ToolContext,
    ) -> ToolResult:
        result = self.handlers[tool_name](arguments, context)
        if hasattr(result, "__await__"):
            result = await result  # type: ignore[assignment]
        return ToolResult.model_validate(result)


def _manifest(
    name: str,
    *,
    permission: str = "read",
    dangerous: bool = False,
    mutates_workspace: bool = False,
    budget: Budget | None = None,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind="native",
        source=ToolSource(kind="native-plugin", package="tests.tool_projection"),
        permission=permission,  # type: ignore[arg-type]
        inputSchema={"type": "object", "additionalProperties": True},
        dangerous=dangerous,
        mutatesWorkspace=mutates_workspace,
        timeoutMs=1_000,
        enabled_by_default=True,
        budget=budget or Budget(outputChars=32, transcriptChars=16),
    )


def _context(events: list[dict[str, object]] | None = None) -> ToolContext:
    return ToolContext(
        botId="bot-tool-events",
        turnId="turn-tool-events",
        workspaceRoot="/Users/kevin/private/workspace",
        emitAgentEvent=events.append if events is not None else None,
    )


def _registry(*manifests: ToolManifest) -> ToolRegistry:
    registry = ToolRegistry()
    for manifest in manifests:
        registry.register(manifest)
    return registry


def _payloads(events: list[dict[str, object]]) -> list[dict[str, object]]:
    writer = InMemorySseWriter()
    for event in events:
        writer.agent(event)
    return [
        json.loads(line.removeprefix("data: "))
        for line in writer.body.splitlines()
        if line.startswith("data: ")
    ]


async def _execute_with_events(
    *,
    handler: Callable[..., object],
    arguments: dict[str, object] | None = None,
    manifest: ToolManifest | None = None,
    output_budget_enabled: bool = True,
    projection_enabled: bool = True,
    local_result_store_enabled: bool = False,
) -> tuple[list[dict[str, object]], Any]:
    events: list[dict[str, object]] = []
    tool_manifest = manifest or _manifest("Echo")
    registry = _registry(tool_manifest)
    store = (
        LocalResultStore(LocalResultStoreConfig(enabled=True, localFakeStoreEnabled=True))
        if local_result_store_enabled
        else None
    )
    outcome = await ToolExecutionKernel(
        registry,
        config=ToolExecutionKernelConfig(
            enabled=True,
            localFakeHandlerExecutionEnabled=True,
            outputBudgetEnabled=output_budget_enabled,
            localFakeResultStoreEnabled=local_result_store_enabled,
            publicEventProjectionEnabled=projection_enabled,
        ),
        local_fake_executor=FakeToolExecutor({tool_manifest.name: handler}),
        local_result_store=store,
    ).execute(
        ToolExecutionRequest(
            toolName=tool_manifest.name,
            arguments=arguments or {"query": "weather"},
            context=_context(events),
            mode="act",
            exposedToolNames=(tool_manifest.name,),
            toolCallId="tool-call-public-1",
        )
    )
    return events, outcome


def test_allowed_local_fake_tool_emits_public_progress_with_receipt_refs() -> None:
    async def run() -> tuple[list[dict[str, object]], object]:
        sensitive_key = "api" + "_key"
        return await _execute_with_events(
            arguments={"query": "weather", sensitive_key: "sk-" + ("a" * 24)},
            handler=lambda _args, _ctx: ToolResult(
                status="ok",
                output={"temperatureC": 21, "path": "/Users/kevin/private/raw.txt"},
                metadata={
                    "status": "running",
                    "detail": "Fetched public summary",
                    "progress": 75,
                },
            ),
            local_result_store_enabled=True,
        )

    events, outcome = asyncio.run(run())
    payloads = _payloads(events)

    assert [event["type"] for event in payloads] == [
        "tool_start",
        "tool_progress",
        "tool_end",
    ]
    assert payloads[0]["name"] == "Echo"
    assert payloads[0]["input_preview"]
    assert "sk-" not in json.dumps(payloads)
    assert "/Users/kevin" not in json.dumps(payloads)
    assert payloads[1]["status"] == "running"
    assert payloads[1]["detail"] == "Fetched public summary"
    assert payloads[1]["progress"] == 75
    assert payloads[2]["status"] == "ok"
    assert payloads[2]["durationMs"] >= 0
    assert payloads[2]["transcriptRefs"]
    assert all(
        str(ref).startswith(("sha256:", "result:sha256:"))
        for ref in payloads[2]["transcriptRefs"]
    )
    assert outcome.executed is True


def test_projection_is_default_off_even_when_local_fake_execution_runs() -> None:
    events, outcome = asyncio.run(
        _execute_with_events(
            handler=lambda _args, _ctx: ToolResult(status="ok", output="visible"),
            projection_enabled=False,
        )
    )

    assert outcome.status == "ok"
    assert events == []


def test_blocked_tool_emits_blocked_tool_end_without_execution() -> None:
    events, outcome = asyncio.run(
        _execute_with_events(
            manifest=_manifest(
                "UnsafeWrite",
                permission="write",
                dangerous=True,
                mutates_workspace=True,
            ),
            arguments={"path": "/Users/kevin/private/secret.txt"},
            handler=lambda _args, _ctx: ToolResult(status="ok", output="should-not-run"),
        )
    )
    payloads = _payloads(events)

    assert outcome.executed is False
    assert payloads == [
        {
            "type": "tool_end",
            "id": "tool-call-public-1",
            "status": "error",
            "output_preview": "dangerous tool requires approval",
            "error": "dangerous tool requires approval",
            "transcriptRefs": payloads[0]["transcriptRefs"],
        },
    ]
    assert payloads[0]["transcriptRefs"]
    assert "/Users/kevin" not in json.dumps(payloads)
    assert "should-not-run" not in json.dumps(payloads)


def test_failed_tool_end_uses_redacted_error_and_evidence_refs() -> None:
    def fail(_args: dict[str, object], _ctx: ToolContext) -> ToolResult:
        raise RuntimeError("raw tool result /Users/kevin/private token=secret")

    events, outcome = asyncio.run(_execute_with_events(handler=fail))
    payloads = _payloads(events)

    assert outcome.status == "error"
    assert [event["type"] for event in payloads] == ["tool_start", "tool_end"]
    assert payloads[1]["status"] == "error"
    assert payloads[1]["error"] == "tool_threw"
    assert payloads[1]["output_preview"]
    assert payloads[1]["transcriptRefs"]
    assert len(payloads[1]["transcriptRefs"]) >= 2
    encoded = json.dumps(payloads)
    assert "raw tool result" not in encoded
    assert "/Users/kevin" not in encoded
    assert "token=secret" not in encoded


def test_projection_helper_redacts_raw_args_results_and_bounds_output_preview() -> None:
    async def run() -> list[dict[str, object]]:
        events, outcome = await _execute_with_events(
            arguments={
                "rawToolArgs": "Authorization: Bearer secret-token",
                "path": "/Users/kevin/private/source.txt",
            },
            handler=lambda _args, _ctx: ToolResult(
                status="ok",
                output={
                    "summary": "tool call result OUTPUT_SECRET",
                    "rawToolResult": "secret",
                    "body": "x" * 5_000,
                    "privatePath": "/data/bots/bot-1/session",
                },
            ),
            output_budget_enabled=False,
        )
        request = ToolExecutionRequest(
            toolName="Echo",
            arguments={"rawToolArgs": "secret", "path": "/Users/kevin/private/source.txt"},
            context=_context([]),
            mode="act",
            exposedToolNames=("Echo",),
            toolCallId="tool-call-public-1",
        )
        assert events
        return list(project_tool_execution_events(request, outcome))

    payloads = _payloads(asyncio.run(run()))
    encoded = json.dumps(payloads)

    assert len(payloads[-1]["output_preview"]) <= 400
    assert "rawToolArgs" not in encoded
    assert "rawToolResult" not in encoded
    assert "OUTPUT_SECRET" not in encoded
    assert "secret-token" not in encoded
    assert "/Users/kevin" not in encoded
    assert "/data/bots" not in encoded
    assert "x" * 500 not in encoded


def test_private_marker_values_taint_entire_projected_strings_before_sse() -> None:
    async def run() -> list[dict[str, object]]:
        events, _outcome = await _execute_with_events(
            arguments={"note": "hidden reasoning: ARG_SECRET"},
            handler=lambda _args, _ctx: ToolResult(
                status="ok",
                output="visible",
                metadata={
                    "status": "running",
                    "detail": "tool call args DETAIL_SECRET",
                    "label": "tool use result LABEL_SECRET",
                },
            ),
        )
        return events

    events = asyncio.run(run())
    payloads = _payloads(events)
    encoded_events = json.dumps(events)
    encoded_payloads = json.dumps(payloads)

    assert "ARG_SECRET" not in encoded_events
    assert "DETAIL_SECRET" not in encoded_events
    assert "LABEL_SECRET" not in encoded_events
    assert "ARG_SECRET" not in encoded_payloads
    assert "DETAIL_SECRET" not in encoded_payloads
    assert "LABEL_SECRET" not in encoded_payloads
    assert events[0]["input_preview"] == "[redacted-private]"
    assert payloads[0]["input_preview"] == "[redacted-private]"
    assert payloads[1]["label"] == "[redacted-private]"
    assert payloads[1]["detail"] == "[redacted-private]"
    assert payloads[0]["transcriptRefs"]
    assert payloads[1]["transcriptRefs"] == payloads[0]["transcriptRefs"]


def test_terminal_projection_omits_tool_end_when_receipt_refs_are_missing() -> None:
    request = ToolExecutionRequest(
        toolName="Echo",
        arguments={},
        context=_context([]),
        mode="act",
        exposedToolNames=("Echo",),
    )
    outcome = ToolResult(status="ok", output="raw result")

    assert project_tool_terminal_events(request, outcome) == ()
