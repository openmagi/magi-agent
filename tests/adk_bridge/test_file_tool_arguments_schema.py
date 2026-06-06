"""The ADK declaration for a file tool must surface its inner argument schema.

The first-party tools are exposed via a generic ``invoke_openmagi_tool(arguments)``
callable, so ADK builds a declaration whose single ``arguments`` object had
``properties=None`` — the model could not see path/old_text/new_text and guessed.
After the fix the ``arguments`` object must carry the manifest's properties.
"""
from __future__ import annotations

from magi_agent.adk_bridge.tool_adapter import build_adk_tool_for_manifest
from magi_agent.runtime.openmagi_runtime import (
    _build_core_tool_registry,
    _build_default_plugin_state,
)
from magi_agent.tools.context import ToolContext
from magi_agent.tools.dispatcher import ToolDispatcher


def _arguments_props(tool_name: str):
    registry = _build_core_tool_registry(_build_default_plugin_state())
    manifest = registry.resolve_registration(tool_name).manifest
    dispatcher = ToolDispatcher(registry)
    tool = build_adk_tool_for_manifest(
        manifest,
        dispatcher,
        mode="act",
        tool_context_factory=lambda _adk: ToolContext(workspace_root="/tmp"),
    )
    decl = tool._get_declaration()
    arguments = decl.parameters.properties["arguments"]
    return arguments.properties or {}


def test_file_edit_arguments_expose_inner_properties():
    props = _arguments_props("FileEdit")
    assert set(props) >= {"path", "old_text", "new_text"}


def test_file_write_arguments_expose_inner_properties():
    props = _arguments_props("FileWrite")
    assert set(props) >= {"path", "content"}
