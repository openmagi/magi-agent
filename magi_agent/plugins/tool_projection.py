from __future__ import annotations

import copy
from typing import TYPE_CHECKING, cast

from .manager import ResolvedPluginState
from .manifest import PermissionClass, PluginKind

if TYPE_CHECKING:
    from magi_agent.tools.manifest import ToolManifest


_GENERIC_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": True,
}
_VALID_PERMISSIONS: set[str] = {"read", "write", "execute", "net", "meta"}
_DELIVERY_PRECONDITIONS: tuple[str, ...] = (
    "future-approval-required",
    "adk-artifact-service-required",
    "channel-traffic-disabled",
)
_FILE_DELIVER_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "artifactId": {
            "type": "string",
            "description": "ID of a registered output artifact",
        },
        "path": {
            "type": "string",
            "description": "Workspace-relative path to an existing file",
        },
        "target": {
            "type": "string",
            "enum": ("chat", "kb", "both"),
        },
        "chat": {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "caption": {"type": "string"},
            },
        },
        "kb": {
            "type": "object",
            "properties": {
                "collection": {"type": "string"},
                "scope": {"type": "string", "enum": ("personal", "org")},
            },
        },
    },
    "required": ("target",),
}
_FILE_SEND_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Workspace-relative path to the file",
        },
        "channel": {
            "type": "string",
            "description": "Channel name to send to",
        },
        "caption": {
            "type": "string",
            "description": "Optional caption for direct file delivery",
        },
        "mode": {
            "type": "string",
            "enum": ("document", "photo"),
        },
    },
    "required": ("path",),
}
_SPECIAL_TOOL_METADATA: dict[tuple[str, str], dict[str, object]] = {
    ("openmagi.agentmemory", "AgentMemoryRemember"): {
        "permission": "write",
    },
    ("openmagi.artifacts", "ArtifactUpdate"): {
        "permission": "write",
    },
    ("openmagi.artifacts", "ArtifactDelete"): {
        "permission": "write",
    },
    ("openmagi.coding", "CommitCheckpoint"): {
        "permission": "write",
    },
    ("openmagi.documents", "DocumentWrite"): {
        "permission": "write",
    },
    ("openmagi.documents", "SpreadsheetWrite"): {
        "permission": "write",
    },
    ("openmagi.documents", "FileDeliver"): {
        "description": (
            "Metadata-only native plugin projection for openmagi.documents FileDeliver. "
            "Delivery execution requires future approval and ADK tool/artifact attachment."
        ),
        "input_schema": _FILE_DELIVER_INPUT_SCHEMA,
        "should_defer": True,
        "side_effect_class": "external",
        "latency_class": "background",
        "adk_tool_type": "LongRunningFunctionTool",
        "capability_tags": ("artifact-delivery", "channel-delivery", "metadata-only"),
        "preconditions": _DELIVERY_PRECONDITIONS,
        "tags": ("native-plugin", "openmagi.documents", "metadata-only", "delivery"),
    },
    ("openmagi.documents", "FileSend"): {
        "description": (
            "Metadata-only native plugin projection for openmagi.documents FileSend. "
            "Channel file sending requires future approval and ADK tool/artifact attachment."
        ),
        "input_schema": _FILE_SEND_INPUT_SCHEMA,
        "should_defer": True,
        "side_effect_class": "external",
        "latency_class": "background",
        "adk_tool_type": "LongRunningFunctionTool",
        "capability_tags": ("file-send", "channel-delivery", "metadata-only"),
        "preconditions": _DELIVERY_PRECONDITIONS,
        "tags": ("native-plugin", "openmagi.documents", "metadata-only", "delivery"),
    },
    ("openmagi.knowledge", "KnowledgeWrite"): {
        "permission": "write",
    },
    ("openmagi.knowledge", "knowledge-write"): {
        "permission": "write",
    },
    ("openmagi.source-ledger", "ExternalSourceCache"): {
        "permission": "write",
    },
    ("openmagi.taskboard", "TaskBoard"): {
        "permission": "write",
    },
}
_SYNTHETIC_PLUGIN_TOOLS: dict[str, tuple[str, ...]] = {
    "openmagi.documents": ("FileDeliver", "FileSend"),
}


def project_native_plugin_tool_manifests(state: ResolvedPluginState) -> tuple[ToolManifest, ...]:
    """Project enabled native plugin tool metadata without attaching execution."""
    manifests: list[ToolManifest] = []
    seen_tools: dict[str, str] = {}

    for plugin in state.plugins:
        if plugin.kind is not PluginKind.NATIVE or not plugin.enabled:
            continue

        permission = _project_permission(plugin.permissions, plugin_id=plugin.plugin_id)
        for tool in plugin.tools:
            manifests.append(
                _build_unique_tool_manifest(
                    name=tool.name,
                    plugin_id=plugin.plugin_id,
                    permission=permission,
                    opt_out=plugin.opt_out_allowed,
                    seen_tools=seen_tools,
                )
            )
        for tool_name in _SYNTHETIC_PLUGIN_TOOLS.get(plugin.plugin_id, ()):
            manifests.append(
                _build_unique_tool_manifest(
                    name=tool_name,
                    plugin_id=plugin.plugin_id,
                    permission=permission,
                    opt_out=plugin.opt_out_allowed,
                    seen_tools=seen_tools,
                )
            )

    return tuple(manifests)


def _build_unique_tool_manifest(
    *,
    name: str,
    plugin_id: str,
    permission: PermissionClass,
    opt_out: bool,
    seen_tools: dict[str, str],
) -> ToolManifest:
    existing_plugin_id = seen_tools.get(name)
    if existing_plugin_id is not None:
        raise ValueError(
            f"duplicate native plugin tool name: {name} "
            f"({existing_plugin_id}, {plugin_id})"
        )
    seen_tools[name] = plugin_id
    return _build_tool_manifest(
        name=name,
        plugin_id=plugin_id,
        permission=permission,
        opt_out=opt_out,
    )


def _build_tool_manifest(
    *,
    name: str,
    plugin_id: str,
    permission: PermissionClass,
    opt_out: bool,
) -> ToolManifest:
    from magi_agent.tools.manifest import ToolManifest, ToolSource

    metadata = _SPECIAL_TOOL_METADATA.get((plugin_id, name), {})
    return ToolManifest(
        name=name,
        description=str(
            metadata.get(
                "description",
                (
                    f"Metadata-only native plugin tool projection for {plugin_id}. "
                    "Local first-party execution is attached through the runtime registry."
                ),
            )
        ),
        kind="native",
        source=ToolSource(kind="native-plugin", package=plugin_id),
        permission=cast(PermissionClass, metadata.get("permission", permission)),
        input_schema=copy.deepcopy(metadata.get("input_schema", _GENERIC_INPUT_SCHEMA)),
        timeout_ms=0,
        tags=cast(tuple[str, ...], metadata.get("tags", ("native-plugin", plugin_id, "metadata-only"))),
        should_defer=bool(metadata.get("should_defer", False)),
        capability_tags=cast(tuple[str, ...], metadata.get("capability_tags", ())),
        side_effect_class=cast(str, metadata.get("side_effect_class", "none")),
        latency_class=cast(str, metadata.get("latency_class", "inline")),
        adk_tool_type=cast(str, metadata.get("adk_tool_type", "FunctionTool")),
        preconditions=cast(tuple[str, ...], metadata.get("preconditions", ())),
        plugin_id=plugin_id,
        enabled_by_default=True,
        opt_out=opt_out,
    )


def _project_permission(
    permissions: tuple[str, ...],
    *,
    plugin_id: str,
) -> PermissionClass:
    invalid = tuple(permission for permission in permissions if permission not in _VALID_PERMISSIONS)
    if invalid:
        raise ValueError(f"invalid native plugin permission for {plugin_id}: {invalid[0]}")
    if len(permissions) == 1:
        return cast(PermissionClass, permissions[0])

    # Choose the most conservative single ToolHost class implied by the plugin.
    for permission in ("execute", "net", "write", "read", "meta"):
        if permission in permissions:
            return cast(PermissionClass, permission)
    return "meta"


__all__ = ["project_native_plugin_tool_manifests"]
