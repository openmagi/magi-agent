from __future__ import annotations

import json
from pathlib import Path

from magi_agent.plugins.general_automation.hook_projection import (
    PluginLifecycleHookProjectionRequest,
    project_plugin_lifecycle_hooks,
)
from magi_agent.plugins.general_automation.mcp_projection import (
    McpToolProjectionRequest,
    project_mcp_tool_metadata,
)


PYTHON_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = PYTHON_ROOT / "magi_agent" / "plugins" / "general_automation"


def _fragment(*parts: str) -> str:
    return "".join(parts)


def test_mcp_tool_projection_uses_disabled_function_tool_metadata_shape() -> None:
    projection = project_mcp_tool_metadata(
        McpToolProjectionRequest(
            serverRef="mcp:notes",
            toolName="read_note",
            permissionClass="read",
            allowedPermissions=("read",),
            policyRef="policy:mcp-notes",
            inputSchema={
                "type": "object",
                "properties": {
                    "noteName": {"type": "string"},
                    "apiToken": {"type": "string"},
                },
                "required": ("noteName", "apiToken"),
                "additionalProperties": False,
            },
        )
    )

    public = projection.public_projection()
    adk_tool = public["adkTool"]
    assert projection.status == "projected_metadata"
    assert public["toolRef"].startswith("tool:mcp-projection:sha256:")
    assert adk_tool["name"] == "mcp.notes.read_note"
    assert adk_tool["adkToolType"] == "FunctionTool"
    assert adk_tool["enabledByDefault"] is False
    assert adk_tool["handlerAttached"] is False
    assert adk_tool["mcpServerAttached"] is False
    assert adk_tool["inputSchema"]["properties"] == {
        "noteName": {"type": "string"},
    }
    assert adk_tool["inputSchema"]["required"] == ("noteName",)
    assert public["adkBoundary"] == {
        "functionTool": "FunctionTool",
        "functionToolName": "mcp.notes.read_note",
        "pluginLifecycle": "plugin lifecycle",
        "externalToolMetadataOnly": True,
    }
    assert set(public["authorityFlags"].values()) == {False}


def test_mcp_tool_projection_blocks_permissions_outside_policy() -> None:
    projection = project_mcp_tool_metadata(
        McpToolProjectionRequest(
            serverRef="mcp:notes",
            toolName="publish_change",
            permissionClass="write",
            allowedPermissions=("read",),
            policyRef="policy:mcp-notes",
        )
    )

    public = projection.public_projection()
    assert projection.status == "blocked"
    assert projection.reason_codes == ("mcp_permission_not_allowed_by_policy",)
    assert public["adkTool"] is None
    assert public["authorityFlags"]["externalToolExecutionEnabled"] is False
    assert public["authorityFlags"]["mcpServerAttached"] is False


def test_plugin_lifecycle_projection_uses_adk_callback_vocabulary_without_attachment() -> None:
    projection = project_plugin_lifecycle_hooks(
        PluginLifecycleHookProjectionRequest(
            pluginId="openmagi.notes",
            lifecycleStage="tool_execution",
            callbackNames=("before_tool_callback", "after_tool_callback"),
            policyRef="policy:plugin-notes",
        )
    )

    public = projection.public_projection()
    assert projection.status == "projected_metadata"
    assert public["pluginRef"].startswith("plugin:general-automation:sha256:")
    assert public["callbackNames"] == (
        "before_tool_callback",
        "after_tool_callback",
    )
    assert len(public["hookRefs"]) == 2
    assert public["adkBoundary"] == {
        "pluginLifecycle": "plugin lifecycle",
        "callbackVocabulary": "ADK callback",
        "callbackAttached": False,
    }
    assert public["authorityFlags"] == {
        "callbackAttached": False,
        "pluginLoaded": False,
        "externalCodeExecuted": False,
        "mcpServerAttached": False,
        "credentialUsed": False,
        "routeAttached": False,
    }


def test_plugin_lifecycle_projection_blocks_protected_runtime_hooks() -> None:
    projection = project_plugin_lifecycle_hooks(
        PluginLifecycleHookProjectionRequest(
            pluginId="openmagi.notes",
            lifecycleStage="runtime_hook",
            callbackNames=("before_agent_callback",),
            policyRef="policy:plugin-notes",
            protectedRuntimeHook=True,
        )
    )

    assert projection.status == "blocked"
    assert projection.reason_codes == ("protected_runtime_hook_blocked",)
    assert projection.public_projection()["hookRefs"] == ()


def test_plugin_projection_public_views_are_digest_only_for_private_metadata() -> None:
    mcp_projection = project_mcp_tool_metadata(
        McpToolProjectionRequest(
            serverRef="mcp:private-notes",
            toolName="read_private_note",
            permissionClass="read",
            allowedPermissions=("read",),
            policyRef="policy:mcp-private",
            metadata={"privateSelector": "account names and local-home markers"},
        )
    )
    hook_projection = project_plugin_lifecycle_hooks(
        PluginLifecycleHookProjectionRequest(
            pluginId="openmagi.private-notes",
            lifecycleStage="tool_execution",
            callbackNames=("before_tool_callback",),
            policyRef="policy:plugin-private",
            metadata={"privateSelector": "account names and local-home markers"},
        )
    )

    rendered = json.dumps(
        [mcp_projection.public_projection(), hook_projection.public_projection()],
        sort_keys=True,
    )
    assert "account names" not in rendered
    assert "local-home" not in rendered
    assert "privateSelector" not in rendered
    assert mcp_projection.public_projection()["metadataDigest"].startswith("sha256:")
    assert hook_projection.public_projection()["metadataDigest"].startswith("sha256:")


def test_general_automation_plugin_modules_do_not_touch_live_surfaces() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PLUGIN_DIR / "mcp_projection.py",
            PLUGIN_DIR / "hook_projection.py",
        )
    )

    forbidden_fragments = (
        "google.adk",
        "FunctionTool(",
        "magi_agent.adk_bridge",
        "magi_agent.runtime",
        "magi_agent.transport",
        "magi_agent.routing",
        "magi_agent.tools.dispatcher",
        "magi_agent.tools.registry",
        "magi_agent.tools.permission",
        "magi_agent.tools.result",
        "magi_agent.plugins.mcp_adapter",
        "magi_agent.plugins.extension_boundary",
        "requests",
        "httpx",
        "aiohttp",
        "socket",
        "playwright",
        "selenium",
        _fragment("sub", "process"),
        _fragment("import", "lib"),
        _fragment("__", "import", "__("),
        ".write_text(",
        "open(",
    )
    for fragment in forbidden_fragments:
        assert fragment not in source
