import asyncio
import ast
import json
from pathlib import Path

import pytest
from google.adk.tools import FunctionTool

from openmagi_core_agent.adk_bridge.tool_adapter import build_adk_function_tools_for_registry
from openmagi_core_agent.config.env import parse_python_toolhost_attachment_env
from openmagi_core_agent.tools import ToolDispatcher, ToolRegistry, ToolResult, ToolSource
from openmagi_core_agent.tools.context import ToolContext
from openmagi_core_agent.tools.manifest import ToolManifest


def make_manifest(name: str, *, enabled_by_default: bool = True) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"{name} test tool",
        kind="custom",
        source=ToolSource(kind="custom-plugin", package="tests.tools"),
        permission="read",
        input_schema={"type": "object"},
        timeout_ms=1_000,
        enabled_by_default=enabled_by_default,
    )


def make_context() -> ToolContext:
    return ToolContext(bot_id="bot-1", turn_id="turn-1", workspace_root="/tmp/workspace")


def run_tool(tool: FunctionTool, arguments: dict[str, object]) -> dict[str, object]:
    return asyncio.run(tool.run_async(args={"arguments": arguments}, tool_context=object()))


def test_toolhost_attachment_env_defaults_off() -> None:
    config = parse_python_toolhost_attachment_env({})

    assert config.enabled is False
    assert config.mode == "disabled"
    assert config.production_attachment_enabled is False
    assert config.live_tool_mutation_enabled is False


def test_local_toolhost_adk_attachment_defaults_to_no_tools_and_no_authority() -> None:
    from openmagi_core_agent.adk_bridge.local_toolhost import build_local_toolhost_adk_tools

    bundle = build_local_toolhost_adk_tools()

    assert bundle.tools == ()
    assert bundle.attach_enabled is False
    assert bundle.local_only is True
    assert bundle.traffic_attached is False
    assert bundle.production_attached is False
    assert bundle.canary_attached is False
    assert bundle.route_attached is False
    assert bundle.deploy_attached is False
    assert bundle.user_visible_output_attached is False
    assert bundle.transcript_write_attached is False
    assert bundle.sse_write_attached is False
    assert bundle.control_write_attached is False
    assert bundle.db_write_attached is False
    assert bundle.workspace_mutation_attached is False


def test_local_toolhost_enabled_exposes_only_requested_fake_function_tools() -> None:
    from openmagi_core_agent.adk_bridge.local_toolhost import build_local_toolhost_adk_tools

    bundle = build_local_toolhost_adk_tools(
        attach_enabled=True,
        exposed_tool_names=("LocalEchoReceipt", "MissingFakeTool"),
    )

    assert [tool.name for tool in bundle.tools] == ["LocalEchoReceipt"]
    assert all(isinstance(tool, FunctionTool) for tool in bundle.tools)
    assert bundle.exposed_tool_names == ("LocalEchoReceipt",)


def test_local_toolhost_hidden_tool_fails_closed_without_handler_call() -> None:
    from openmagi_core_agent.adk_bridge.local_toolhost import build_local_toolhost_adk_tools

    bundle = build_local_toolhost_adk_tools(
        attach_enabled=True,
        exposed_tool_names=("LocalEchoReceipt",),
    )

    result = asyncio.run(
        bundle.host.dispatch(
            "LocalStatusReceipt",
            {"value": "private"},
            exposed_tool_names=("LocalEchoReceipt",),
        )
    )

    assert result.status == "error"
    assert result.error_code == "tool_not_exposed"
    assert result.metadata["toolName"] == "LocalStatusReceipt"
    assert result.metadata["availableTools"] == ("LocalEchoReceipt",)
    assert bundle.host.calls == ()


def test_local_toolhost_fake_handler_runs_only_through_direct_adk_tool_call_and_sanitizes_receipt() -> None:
    from openmagi_core_agent.adk_bridge.local_toolhost import build_local_toolhost_adk_tools

    bundle = build_local_toolhost_adk_tools(
        attach_enabled=True,
        exposed_tool_names=("LocalEchoReceipt",),
    )

    assert bundle.host.calls == ()

    result = run_tool(
        bundle.tools[0],
        {
            "path": "/workspace/secret.txt",
            "token": "super-secret-value",
            "deploy": True,
            "network": True,
            "canary": True,
        },
    )

    assert result["status"] == "ok"
    assert result["output"]["receipt"]["toolName"] == "LocalEchoReceipt"
    assert result["output"]["receipt"]["localOnly"] is True
    assert len(bundle.host.calls) == 1
    assert "argumentDigest" not in result["output"]["receipt"]

    serialized = json.dumps(result, sort_keys=True).lower()
    for forbidden in (
        "/workspace",
        "/data",
        "super-secret-value",
        "deploy",
        "network",
        "canary",
        "production",
        "database",
        "supabase",
        "kube",
    ):
        assert forbidden not in serialized


def test_local_toolhost_receipts_are_not_stable_raw_argument_oracles() -> None:
    from openmagi_core_agent.adk_bridge.local_toolhost import build_local_toolhost_adk_tools

    bundle = build_local_toolhost_adk_tools(
        attach_enabled=True,
        exposed_tool_names=("LocalEchoReceipt",),
    )
    arguments = {"token": "low-entropy-secret", "pin": "1234"}

    first = run_tool(bundle.tools[0], arguments)
    second = run_tool(bundle.tools[0], arguments)

    first_receipt = first["output"]["receipt"]
    second_receipt = second["output"]["receipt"]
    assert first_receipt["receiptId"] != second_receipt["receiptId"]
    assert "argumentDigest" not in first_receipt
    assert "low-entropy-secret" not in json.dumps(first_receipt, sort_keys=True)
    assert "1234" not in json.dumps(first_receipt, sort_keys=True)


def test_local_toolhost_module_stays_self_contained_without_dispatch_runtime_or_transport_imports() -> None:
    module_path = (
        Path(__file__).parents[1]
        / "openmagi_core_agent"
        / "adk_bridge"
        / "local_toolhost.py"
    )
    tree = ast.parse(module_path.read_text())
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "openmagi_core_agent.runtime",
        "openmagi_core_agent.transport",
        "openmagi_core_agent.tools",
        "openmagi_core_agent.control",
    )
    assert not [
        module
        for module in imported_modules
        if any(module == prefix or module.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    ]


def test_local_runner_accepts_fake_toolhost_function_tools_without_model_or_prod_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openmagi_core_agent.adk_bridge.local_runner import build_local_adk_runner
    from openmagi_core_agent.adk_bridge.local_toolhost import build_local_toolhost_adk_tools

    monkeypatch.setenv("CORE_AGENT_PYTHON_LOCAL_ADK_RUNNER", "1")
    toolhost_bundle = build_local_toolhost_adk_tools(
        attach_enabled=True,
        exposed_tool_names=("LocalEchoReceipt",),
    )

    runner_bundle = build_local_adk_runner(tools=toolhost_bundle)

    assert runner_bundle.agent.tools == list(toolhost_bundle.tools)
    assert runner_bundle.local_only is True
    assert runner_bundle.traffic_attached is False
    assert runner_bundle.production_attached is False
    assert runner_bundle.canary_attached is False
    assert runner_bundle.route_attached is False
    assert runner_bundle.deploy_attached is False
    assert runner_bundle.user_visible_output_attached is False
    assert runner_bundle.transcript_write_attached is False
    assert runner_bundle.sse_write_attached is False
    assert runner_bundle.control_write_attached is False
    assert runner_bundle.db_write_attached is False
    assert runner_bundle.workspace_mutation_attached is False
    assert toolhost_bundle.host.calls == ()


@pytest.mark.parametrize(
    "env_name",
    (
        "CORE_AGENT_PYTHON_TOOLHOST_PRODUCTION_ATTACHMENT",
        "CORE_AGENT_PYTHON_TOOLHOST_LIVE_TOOL_MUTATION",
    ),
)
def test_toolhost_attachment_env_rejects_production_or_mutation_flags(env_name: str) -> None:
    with pytest.raises(ValueError, match="not approved"):
        parse_python_toolhost_attachment_env({env_name: "1"})


def test_adk_tool_adapter_returns_no_tools_when_attachment_flag_is_false() -> None:
    registry = ToolRegistry()
    registry.register(make_manifest("ReadOnlyEcho"))
    dispatcher = ToolDispatcher(registry)

    tools = build_adk_function_tools_for_registry(
        registry,
        dispatcher,
        mode="act",
        tool_context_factory=lambda _adk_context: make_context(),
        attach_enabled=False,
    )

    assert tools == []


def test_dispatcher_treats_not_exposed_enabled_tool_as_fail_closed_without_handler_call() -> None:
    called = False

    def handler(arguments: dict[str, object], context: ToolContext) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(status="ok", output={"unexpected": arguments})

    registry = ToolRegistry()
    registry.register(make_manifest("HiddenRead"), handler=handler)
    dispatcher = ToolDispatcher(registry)

    result = asyncio.run(
        dispatcher.dispatch(
            "HiddenRead",
            {"query": "private"},
            make_context(),
            mode="act",
            exposed_tool_names=("VisibleRead",),
        )
    )

    assert result.status == "error"
    assert result.error_code == "tool_not_exposed"
    assert result.metadata["toolName"] == "HiddenRead"
    assert result.metadata["reason"] == "not exposed to this turn"
    assert result.metadata["availableTools"] == ("VisibleRead",)
    assert called is False


def test_dispatcher_unknown_tool_hint_uses_exposed_tool_allowlist() -> None:
    registry = ToolRegistry()
    registry.register(make_manifest("HiddenRead"))
    dispatcher = ToolDispatcher(registry)

    result = asyncio.run(
        dispatcher.dispatch(
            "MissingRead",
            {},
            make_context(),
            mode="act",
            exposed_tool_names=("VisibleRead",),
        )
    )

    assert result.status == "error"
    assert result.error_code == "tool_not_found"
    assert result.metadata["toolName"] == "MissingRead"
    assert result.metadata["availableTools"] == ("VisibleRead",)
