import asyncio
import ast
from collections.abc import Callable
from pathlib import Path

import pytest
from google.adk.tools import FunctionTool, LongRunningFunctionTool

from magi_agent.adk_bridge import tool_adapter
from magi_agent.adk_bridge.tool_adapter import (
    build_adk_function_tool,
    build_adk_function_tools_for_registry,
)
from magi_agent.tools import (
    ToolDispatcher,
    ToolRegistry,
    ToolResult,
    ToolSource,
    register_core_tool_manifests,
)
from magi_agent.tools.context import ToolContext as OpenMagiToolContext
from magi_agent.tools.manifest import ToolManifest


def make_manifest(
    name: str,
    *,
    description: str | None = None,
    input_schema: dict[str, object] | None = None,
    permission: str = "read",
    modes: tuple[str, ...] = ("plan", "act"),
    dangerous: bool = False,
    mutates_workspace: bool = False,
    enabled_by_default: bool = True,
    tags: tuple[str, ...] = (),
    should_defer: bool = False,
    latency_class: str = "inline",
    adk_tool_type: str = "FunctionTool",
) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=description or f"{name} test tool",
        kind="custom",
        source=ToolSource(kind="custom-plugin", package="tests.tools"),
        permission=permission,
        input_schema=input_schema or {"type": "object", "additionalProperties": True},
        timeout_ms=1_000,
        available_in_modes=modes,
        dangerous=dangerous,
        mutates_workspace=mutates_workspace,
        tags=tags,
        should_defer=should_defer,
        latency_class=latency_class,
        adk_tool_type=adk_tool_type,
        enabled_by_default=enabled_by_default,
    )


def make_context_factory(
    *,
    bot_id: str = "bot-1",
    turn_id: str = "turn-1",
    calls: list[object] | None = None,
) -> Callable[[object], OpenMagiToolContext]:
    def factory(adk_tool_context: object) -> OpenMagiToolContext:
        if calls is not None:
            calls.append(adk_tool_context)
        return OpenMagiToolContext(
            bot_id=bot_id,
            turn_id=turn_id,
            workspace_root="/tmp/workspace",
            adk_tool_context=adk_tool_context,
        )

    return factory


def run_tool(
    tool: FunctionTool | LongRunningFunctionTool,
    arguments: dict[str, object],
    *,
    adk_tool_context: object | None = None,
) -> dict[str, object]:
    return asyncio.run(
        tool.run_async(
            args={"arguments": arguments},
            tool_context=adk_tool_context or object(),
        )
    )


def test_registry_helper_returns_no_function_tools_for_default_disabled_core_catalog() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    dispatcher = ToolDispatcher(registry)

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(),
        attach_enabled=True,
    )

    assert tools == []


def test_enabled_safe_tool_wraps_official_function_tool_and_dispatches_with_openmagi_context() -> None:
    manifest = make_manifest(
        "SafeEcho",
        description="Echo arguments through the OpenMagi ToolHost.",
    )
    calls: list[tuple[dict[str, object], OpenMagiToolContext]] = []

    def handler(arguments: dict[str, object], context: OpenMagiToolContext) -> ToolResult:
        calls.append((arguments, context))
        return ToolResult(status="ok", output={"echo": arguments["value"]})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    dispatcher = ToolDispatcher(registry)
    context_factory_calls: list[object] = []
    adk_tool_context = object()

    tool = tool_adapter.build_adk_tool_for_manifest(
        manifest,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(calls=context_factory_calls),
    )

    assert isinstance(tool, FunctionTool)
    assert tool.name == manifest.name
    assert tool.description == manifest.description
    assert tool._require_confirmation is False

    result = run_tool(tool, {"value": "hello"}, adk_tool_context=adk_tool_context)

    assert result["status"] == "ok"
    assert result["output"] == {"echo": "hello"}
    assert context_factory_calls == [adk_tool_context]
    assert len(calls) == 1
    assert calls[0][0] == {"value": "hello"}
    assert calls[0][1].bot_id == "bot-1"
    assert calls[0][1].adk_tool_context is adk_tool_context


def test_adk_function_tool_rejects_invalid_schema_before_dispatch_handler() -> None:
    manifest = make_manifest(
        "StrictEcho",
        input_schema={
            "type": "object",
            "required": ["value"],
            "properties": {"value": {"type": "string"}},
            "additionalProperties": False,
        },
    )
    calls: list[dict[str, object]] = []

    def handler(arguments: dict[str, object], context: OpenMagiToolContext) -> ToolResult:
        _ = context
        calls.append(arguments)
        return ToolResult(status="ok", output={"echo": arguments["value"]})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    dispatcher = ToolDispatcher(registry)
    tool = build_adk_function_tool(
        manifest,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(),
    )

    result = run_tool(tool, {"value": "hello", "unexpected": "blocked"})
    dumped = str(result)

    assert result["status"] == "blocked"
    assert result["errorCode"] == "tool_input_schema_invalid"
    assert result["metadata"]["reason"] == "input schema validation failed"
    assert "schema_additional_property_blocked" in dumped
    assert "unexpected" not in dumped
    assert calls == []


def test_long_running_manifest_wraps_official_long_running_function_tool_and_dispatches() -> None:
    manifest = make_manifest(
        "BackgroundDigest",
        should_defer=True,
        latency_class="background",
        adk_tool_type="LongRunningFunctionTool",
    )
    calls: list[tuple[dict[str, object], OpenMagiToolContext]] = []

    async def handler(arguments: dict[str, object], context: OpenMagiToolContext) -> ToolResult:
        calls.append((arguments, context))
        return ToolResult(status="ok", output={"jobId": f"job-{arguments['topic']}"})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    dispatcher = ToolDispatcher(registry)
    adk_tool_context = object()

    tool = tool_adapter.build_adk_tool_for_manifest(
        manifest,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(bot_id="bot-bg"),
    )

    assert isinstance(tool, LongRunningFunctionTool)
    assert tool.name == manifest.name
    assert tool.description == manifest.description

    result = run_tool(tool, {"topic": "ledger"}, adk_tool_context=adk_tool_context)

    assert result["status"] == "ok"
    assert result["output"] == {"jobId": "job-ledger"}
    assert len(calls) == 1
    assert calls[0][0] == {"topic": "ledger"}
    assert calls[0][1].bot_id == "bot-bg"
    assert calls[0][1].adk_tool_context is adk_tool_context


def test_build_adk_function_tool_rejects_long_running_manifest() -> None:
    manifest = make_manifest(
        "BackgroundOnly",
        should_defer=True,
        latency_class="long_running",
        adk_tool_type="LongRunningFunctionTool",
    )
    registry = ToolRegistry()
    registry.register(manifest)
    dispatcher = ToolDispatcher(registry)

    with pytest.raises(ValueError, match="LongRunningFunctionTool"):
        build_adk_function_tool(
            manifest,
            dispatcher,
            mode="act",
            tool_context_factory=make_context_factory(),
        )


def test_approval_required_enabled_tool_returns_control_request_without_calling_handler() -> None:
    manifest = make_manifest("WriteNeedsApproval", permission="write")
    called = False

    def handler(arguments: dict[str, object], context: OpenMagiToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": arguments})

    registry = ToolRegistry()
    registry.register(manifest, handler=handler)
    dispatcher = ToolDispatcher(registry)
    tool = build_adk_function_tool(
        manifest,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(turn_id="turn-approval"),
    )

    result = run_tool(tool, {"path": "README.md"})

    assert result["status"] == "needs_approval"
    assert result["metadata"]["toolName"] == manifest.name
    assert result["metadata"]["reason"] == "write permission requires approval"
    control_request = result["metadata"]["controlRequest"]
    assert control_request["turnId"] == "turn-approval"
    assert control_request["toolName"] == manifest.name
    assert control_request["arguments"] == {"path": "README.md"}
    assert control_request["requestId"].startswith(
        "tool-permission:turn-approval:WriteNeedsApproval:"
    )
    assert called is False


def test_registry_helper_includes_enabled_long_running_tool_and_excludes_disabled_tools() -> None:
    registry = ToolRegistry()
    disabled = make_manifest("DisabledSafe", enabled_by_default=False)
    enabled_long_running = make_manifest(
        "EnabledBackground",
        should_defer=True,
        latency_class="background",
        adk_tool_type="LongRunningFunctionTool",
    )

    registry.register(disabled, handler=lambda _arguments, _context: ToolResult(status="ok"))
    registry.register(enabled_long_running, handler=lambda _arguments, _context: ToolResult(status="ok"))
    dispatcher = ToolDispatcher(registry)

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(),
        attach_enabled=True,
    )

    assert [tool.name for tool in tools] == ["EnabledBackground"]
    assert isinstance(tools[0], LongRunningFunctionTool)


def test_enabled_catalog_tool_without_handler_returns_missing_handler_not_approval() -> None:
    registry = ToolRegistry()
    register_core_tool_manifests(registry)
    registry.enable("Bash")
    dispatcher = ToolDispatcher(registry)

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(),
        attach_enabled=True,
    )

    assert [tool.name for tool in tools] == ["Bash"]

    result = run_tool(tools[0], {"command": "echo should-not-run"})

    assert result["status"] == "error"
    assert result["errorCode"] == "tool_handler_missing"
    assert result["errorMessage"] == "tool handler missing"
    assert result["metadata"]["toolName"] == "Bash"
    assert result["metadata"]["reason"] == "tool handler missing"
    assert "controlRequest" not in result["metadata"]


def test_adapter_returns_tool_result_alias_keys_for_errors() -> None:
    manifest = make_manifest("DeclaredOnly")
    registry = ToolRegistry()
    registry.register(manifest)
    dispatcher = ToolDispatcher(registry)
    tool = build_adk_function_tool(
        manifest,
        dispatcher,
        mode="act",
        tool_context_factory=make_context_factory(),
    )

    result = run_tool(tool, {})

    assert result["status"] == "error"
    assert result["errorCode"] == "tool_handler_missing"
    assert result["errorMessage"] == "tool handler missing"
    assert "error_code" not in result
    assert "error_message" not in result


def test_tool_adapter_does_not_import_production_or_implementation_tool_modules() -> None:
    adapter_path = (
        Path(__file__).parents[1]
        / "magi_agent"
        / "adk_bridge"
        / "tool_adapter.py"
    )
    tree = ast.parse(adapter_path.read_text())
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "magi_agent.artifact",
        "magi_agent.channels",
        "magi_agent.deploy",
        "magi_agent.plugins",
        "magi_agent.runtime",
        "magi_agent.transport",
        "magi_agent.workspace",
        "magi_agent.tools.catalog",
    )
    assert not [
        module
        for module in imported_modules
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    ]
