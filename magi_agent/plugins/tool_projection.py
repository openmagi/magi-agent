from __future__ import annotations

import copy
from collections.abc import Mapping
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
#: Model-facing argument schema for ``OkfLookup`` (and its ``okf-lookup``
#: alias).  Without this the projection falls back to ``_GENERIC_INPUT_SCHEMA``
#: and the model never learns the tool takes a ``query``/``path`` argument.
#: No ``required`` key: either ``query`` OR ``path`` works and the tool itself
#: enforces "at least one" (returns ``query_required`` otherwise).
_OKF_LOOKUP_INPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Free-text search over the OKF bundle "
                "(title/description/tags/body)."
            ),
        },
        "path": {
            "type": "string",
            "description": (
                "Exact bundle-relative document path, e.g. "
                "sales/tables/orders.md."
            ),
        },
    },
    "additionalProperties": False,
}
#: Model-facing description for ``OkfLookup``.  Leads with WHEN to use it and
#: WHAT it returns so the agent is induced to consult the curated knowledge/okf
#: store before answering domain/factual questions.
_OKF_LOOKUP_DESCRIPTION: str = (
    "Search the curated local knowledge bundle (Open Knowledge Format) for "
    "trusted, human-maintained facts: schemas, definitions, domain references. "
    "Use this BEFORE answering domain or factual questions when a knowledge/okf "
    "store may exist; it returns matching documents with their file path, body, "
    "and source URL verbatim. Args: `query` (free-text search) or `path` (exact "
    "doc path). Read-only and default-OFF (inert until the deployment enables "
    "the OKF knowledge store)."
)


def _build_spawn_agent_input_schema(
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Build SpawnAgent's input_schema with REGISTRY-DRIVEN route advertising.

    The ``provider`` and ``model`` parameter descriptions enumerate the live
    ``(provider, model)`` pairs that ``resolve_child_route`` will accept,
    pulled from :func:`magi_agent.runtime.model_tiers.available_child_model_routes`
    — the same single-source-of-truth the runtime validates against. This
    closes the drift gap that previously caused every cross-provider
    SpawnAgent call to fail with ``child_model_route_unknown``: the static
    description used to ship the literal example ``claude-opus-4-5`` (a model
    the registry never had), and the parent LLM copied that name verbatim.

    Fail-soft: any error reading the registry leaves the description as a
    short, neutral hint that simply names the failure mode. The runtime is
    still the validation authority either way.
    """
    import os as _os  # noqa: PLC0415

    routes: list[str] = []
    try:
        from magi_agent.runtime.model_tiers import (  # noqa: PLC0415
            available_child_model_routes,
        )

        source_env = env if env is not None else _os.environ
        routes = list(available_child_model_routes(source_env) or ())
    except Exception:  # noqa: BLE001 — fail-soft; description below stays neutral.
        routes = []

    # ``available_child_model_routes`` returns "provider:model (tier)" entries;
    # strip the tier marker so the LLM sees the bare ``provider:model`` pair.
    bare_routes = [route.split(" ", 1)[0] for route in routes]
    providers = sorted({pair.split(":", 1)[0] for pair in bare_routes})

    if bare_routes:
        provider_desc = (
            "LLM provider for the child. Must be one of the configured "
            f"providers: {', '.join(providers)}. Omitting `provider` defaults "
            "to the parent's provider."
        )
        model_desc = (
            "Model id for the child. The (provider, model) pair MUST be one "
            f"of the live routes: {', '.join(bare_routes)}. Unknown routes "
            "are rejected as child_model_route_unknown. Omit `model` to use "
            "the provider's default."
        )
    else:
        provider_desc = (
            "LLM provider for the child (e.g. 'anthropic', 'openai', "
            "'gemini', 'fireworks'). Pair with a matching `model` from the "
            "deployment's configured routes; unknown routes are rejected as "
            "child_model_route_unknown."
        )
        model_desc = (
            "Model id for the child. Pair with a matching `provider`; an "
            "unknown (provider, model) is rejected as child_model_route_unknown. "
            "Omit to use the provider's default."
        )

    return {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The task prompt to send to the child agent.",
            },
            "persona": {
                "type": "string",
                "description": "Persona/role for the child agent (e.g. 'coding', 'research', 'general').",
            },
            "provider": {
                "type": "string",
                "description": provider_desc,
            },
            "model": {
                "type": "string",
                "description": model_desc,
            },
            "budgetMs": {
                "type": "integer",
                "description": "Wall-clock time budget in milliseconds for the child task.",
            },
            "allowedTools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Per-task tool grant: names of tools the child is allowed to use. "
                    "The grant is intersected with the session ceiling — it can only narrow, not expand."
                ),
            },
            "recipeRefs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Recipe pack references to bind to the child task "
                    "(validators, gates, and instructions scoped to this subtask)."
                ),
            },
            "taskTitle": {
                "type": "string",
                "description": (
                    "Short PUBLIC-SAFE label (≤ 64 chars) shown to the user as "
                    "this agent's chip in the dashboard (e.g. 'Cross-validate "
                    "1+1 across 3 SOTA models'). Keep it descriptive but free "
                    "of private prompt content — unlike `prompt`/`task`, this "
                    "field is surfaced to the UI."
                ),
            },
        },
        "additionalProperties": True,
    }
_WEB_READONLY_METADATA: dict[str, object] = {
    # WebSearch / WebFetch (and aliases) only *read* remote data — no external
    # mutation. Declaring them read-only lets the fail-closed default permission
    # scope auto-allow them instead of prompting on every call. ``external``
    # side-effect class reflects the network reach (NOT a workspace mutation);
    # the manifest validator forbids readonly tools from being dangerous or
    # mutating, so this stays safe.
    "side_effect_class": "external",
    "parallel_safety": "readonly",
}
_SPECIAL_TOOL_METADATA: dict[tuple[str, str], dict[str, object]] = {
    ("openmagi.web", "WebSearch"): _WEB_READONLY_METADATA,
    ("openmagi.web", "web-search"): _WEB_READONLY_METADATA,
    ("openmagi.web", "web_search"): _WEB_READONLY_METADATA,
    ("openmagi.web", "WebFetch"): _WEB_READONLY_METADATA,
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
        "input_schema": "document_write",
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
    ("openmagi.knowledge-okf", "OkfLookup"): {
        "description": _OKF_LOOKUP_DESCRIPTION,
        "input_schema": _OKF_LOOKUP_INPUT_SCHEMA,
    },
    ("openmagi.knowledge-okf", "okf-lookup"): {
        "description": _OKF_LOOKUP_DESCRIPTION,
        "input_schema": _OKF_LOOKUP_INPUT_SCHEMA,
    },
    ("openmagi.subagents", "SpawnAgent"): {
        "description": (
            "Delegate a bounded subtask to a child Magi Agent. "
            "Use allowedTools to narrow the child's tool grant (intersected with the session ceiling; "
            "can only restrict, not expand) and recipeRefs to bind recipe packs "
            "(validators, gates, and instructions) scoped to that child task."
        ),
        "input_schema": "spawn_agent",
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
    input_schema = metadata.get("input_schema", _GENERIC_INPUT_SCHEMA)
    if input_schema == "document_write":
        from magi_agent.tools.document_write.model import (  # noqa: PLC0415
            DOCUMENT_WRITE_INPUT_SCHEMA,
        )

        input_schema = DOCUMENT_WRITE_INPUT_SCHEMA
    elif input_schema == "spawn_agent":
        # Resolve at manifest-build time so the LLM sees the live registry's
        # routes (key-aware filtered) instead of a stale literal example.
        input_schema = _build_spawn_agent_input_schema()
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
        input_schema=copy.deepcopy(input_schema),
        timeout_ms=0,
        tags=cast(tuple[str, ...], metadata.get("tags", ("native-plugin", plugin_id, "metadata-only"))),
        should_defer=bool(metadata.get("should_defer", False)),
        capability_tags=cast(tuple[str, ...], metadata.get("capability_tags", ())),
        side_effect_class=cast(str, metadata.get("side_effect_class", "none")),
        parallel_safety=cast(str, metadata.get("parallel_safety", "unsafe")),
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
