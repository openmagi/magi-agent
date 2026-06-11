"""Project loaded connector specs' ToolManifests into the live tool registry.

A connector (MCP descriptor) carries projected ``ToolManifest``s. Registering
them into ``registries.tools`` lets connector tools share the SAME mode-scoped
offer path native tools use (the Group-A live seam) — proving a pack-provided
connector is live, not catalogued only.
"""
from __future__ import annotations

from magi_agent.packs.registries import PackRegistries


def project_connector_tools(registries: PackRegistries) -> tuple[str, ...]:
    """Register each loaded connector's projected ToolManifests into the live tool
    registry. Returns the registered tool names."""
    registered: list[str] = []
    for ref in registries.connectors.list_refs():
        spec = registries.connectors.resolve(ref)
        if spec is None:
            continue
        for manifest in spec.tool_manifests:
            if registries.tools.resolve(manifest.name) is None:
                registries.tools.register(manifest)
            else:
                registries.tools.replace(manifest)
            registered.append(manifest.name)
    return tuple(registered)
