from __future__ import annotations

from collections.abc import Mapping

from .manifest import PluginManifest, parse_plugin_manifest


_NATIVE_PLUGIN_DATA: tuple[Mapping[str, object], ...] = (
    {
        "id": "openmagi.agentmemory",
        "name": "OpenMagi AgentMemory",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": (
            "Metadata-only AgentMemory provider candidate behind OpenMagi "
            "memory policy and ADK MemoryService boundaries."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "future ADK MemoryService/plugin attachment point only",
        },
        "permissions": (),
        "services": ("agentmemory-provider-endpoint",),
        "tools": (
            {
                "name": "AgentMemorySearch",
                "entrypoint": "openmagi_core_agent.plugins.agentmemory.tools:agentmemory_search",
            },
            {
                "name": "AgentMemoryRemember",
                "entrypoint": "openmagi_core_agent.plugins.agentmemory.tools:agentmemory_remember",
            },
        ),
        "hooks": (
            {
                "name": "agentmemory.recall",
                "point": "beforeModelCall",
                "entrypoint": "openmagi_core_agent.plugins.agentmemory.hooks:agentmemory_recall",
            },
            {
                "name": "agentmemory.observe",
                "point": "afterTurnEnd",
                "entrypoint": "openmagi_core_agent.plugins.agentmemory.hooks:agentmemory_observe",
            },
        ),
        "harnessRules": ("memory_agentmemory_provider_boundary",),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "capabilities": (
            {"type": "tool", "name": "AgentMemorySearch"},
            {"type": "tool", "name": "AgentMemoryRemember"},
            {"type": "hook", "name": "agentmemory.recall"},
            {"type": "hook", "name": "agentmemory.observe"},
            {"type": "harness", "name": "memory_agentmemory_provider_boundary"},
            {"type": "service-endpoint", "name": "agentmemory-provider-endpoint"},
            {"type": "verifier", "name": "agentmemory-provider-boundary"},
        ),
    },
    {
        "id": "openmagi.browser",
        "name": "OpenMagi Browser",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "Metadata for OpenMagi browser automation and social browsing surfaces.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "default-disabled browser provider surface; future ADK FunctionTool "
                "attachment must route through ToolHost policy"
            ),
        },
        "permissions": ("read", "write", "net"),
        "services": ("browser-worker", "chat-proxy"),
        "tools": (
            {
                "name": "Browser",
                "entrypoint": "openmagi_core_agent.plugins.native.browser:browser",
            },
            {
                "name": "SocialBrowser",
                "entrypoint": "openmagi_core_agent.plugins.native.browser:social_browser",
            },
        ),
        "harnessRules": ("browser_session_scope",),
        "secrets": (
            {
                "name": "GATEWAY_TOKEN",
                "source": "platform",
            },
        ),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "allowedDomains": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "socialBrowserEnabled": {
                    "type": "boolean",
                    "default": False,
                },
            },
        },
        "capabilities": (
            {"type": "tool", "name": "Browser"},
            {"type": "tool", "name": "SocialBrowser"},
            {"type": "harness", "name": "browser_session_scope"},
            {"type": "service-endpoint", "name": "browser-worker"},
        ),
    },
    {
        "id": "openmagi.documents",
        "name": "OpenMagi Documents",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "Metadata for document reading, writing, rendering, and spreadsheet output surfaces.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "future ADK tool/plugin attachment point only",
        },
        "permissions": ("read", "write", "net"),
        "services": ("document-worker", "document-converter-worker", "chat-proxy"),
        "tools": (
            {
                "name": "DocumentWrite",
                "entrypoint": "openmagi_core_agent.plugins.native.documents:document_write",
            },
            {
                "name": "SpreadsheetWrite",
                "entrypoint": "openmagi_core_agent.plugins.native.documents:spreadsheet_write",
            },
        ),
        "harnessRules": ("document_format_compatibility",),
        "secrets": (
            {
                "name": "GATEWAY_TOKEN",
                "source": "platform",
            },
        ),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "allowedFormats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ("docx", "pdf", "html", "xlsx", "csv"),
                },
                "deliveryChannels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ("web", "telegram", "discord"),
                },
            },
        },
        "capabilities": (
            {"type": "tool", "name": "DocumentWrite"},
            {"type": "tool", "name": "SpreadsheetWrite"},
            {"type": "tool", "name": "FileDeliver"},
            {"type": "tool", "name": "FileSend"},
            {"type": "harness", "name": "document_format_compatibility"},
            {"type": "service-endpoint", "name": "document-worker"},
            {"type": "service-endpoint", "name": "document-converter-worker"},
        ),
    },
    {
        "id": "openmagi.knowledge",
        "name": "OpenMagi Knowledge",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "Metadata for KnowledgeSearch, KnowledgeWrite, and KB collection access.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "future ADK tool/plugin attachment point only",
        },
        "permissions": ("read", "write", "net"),
        "services": ("knowledge-worker", "chat-proxy"),
        "tools": (
            {
                "name": "KnowledgeSearch",
                "entrypoint": "openmagi_core_agent.plugins.native.knowledge:knowledge_search",
            },
            {
                "name": "knowledge-search",
                "entrypoint": "openmagi_core_agent.plugins.native.knowledge:knowledge_search",
            },
            {
                "name": "KnowledgeWrite",
                "entrypoint": "openmagi_core_agent.plugins.native.knowledge:knowledge_write",
            },
            {
                "name": "knowledge-write",
                "entrypoint": "openmagi_core_agent.plugins.native.knowledge:knowledge_write",
            },
        ),
        "harnessRules": ("knowledge_write_scope",),
        "secrets": (
            {
                "name": "GATEWAY_TOKEN",
                "source": "platform",
            },
        ),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "collections": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "defaultReadLimitBytes": {
                    "type": "integer",
                    "minimum": 1,
                },
                "writeScopeRequired": {
                    "type": "boolean",
                    "default": True,
                },
            },
        },
        "capabilities": (
            {"type": "tool", "name": "KnowledgeSearch"},
            {"type": "tool", "name": "knowledge-search"},
            {"type": "tool", "name": "KnowledgeWrite"},
            {"type": "tool", "name": "knowledge-write"},
            {"type": "harness", "name": "knowledge_write_scope"},
            {"type": "service-endpoint", "name": "knowledge-worker"},
        ),
    },
    {
        "id": "openmagi.missions",
        "name": "OpenMagi Missions",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "Metadata for mission ledger coordination and export helpers.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "future ADK tool/plugin attachment point only",
        },
        "permissions": ("read", "meta"),
        "services": ("mission-ledger",),
        "tools": (
            {
                "name": "MissionLedger",
                "entrypoint": "openmagi_core_agent.plugins.native.missions:mission_ledger",
            },
        ),
        "harnessRules": ("mission_coordination_scope",),
        "secrets": (
            {
                "name": "GATEWAY_TOKEN",
                "source": "platform",
            },
        ),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "exportFormats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "default": ("json", "markdown"),
                },
                "allowCrossSessionCoordination": {
                    "type": "boolean",
                    "default": True,
                },
            },
        },
        "capabilities": (
            {"type": "tool", "name": "MissionLedger"},
            {"type": "harness", "name": "mission_coordination_scope"},
            {"type": "service-endpoint", "name": "mission-ledger"},
        ),
    },
    {
        "id": "openmagi.scheduled-work",
        "name": "OpenMagi Scheduled Work",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": (
            "Metadata-only scheduled work policy surface for Cron tool names, "
            "scheduler metadata, script cron runner metadata, and TaskWait parity."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "first-party recipe/native-plugin policy surface only; future Cron tools "
                "may wrap through ADK FunctionTool via ToolHost policy after approval; "
                "scheduler/background runtime is not LongRunningFunctionTool"
            ),
        },
        "permissions": (),
        "services": (),
        "tools": (
            {
                "name": "CronCreate",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:cron_create",
            },
            {
                "name": "CronList",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:cron_list",
            },
            {
                "name": "CronUpdate",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:cron_update",
            },
            {
                "name": "CronDelete",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:cron_delete",
            },
            {
                "name": "TaskWait",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:task_wait",
            },
            {
                "name": "TaskGet",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:task_get",
            },
            {
                "name": "TaskList",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:task_list",
            },
            {
                "name": "TaskOutput",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:task_output",
            },
            {
                "name": "TaskStop",
                "entrypoint": "openmagi_core_agent.plugins.native.scheduled_work:task_stop",
            },
        ),
        "harnessRules": ("scheduled_work_recipe_policy",),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "schedulerAttached": {"type": "boolean", "default": False},
                "scriptCronRunnerAttached": {"type": "boolean", "default": False},
                "channelDeliveryAttached": {"type": "boolean", "default": False},
            },
        },
        "capabilities": (
            {"type": "tool", "name": "CronCreate"},
            {"type": "tool", "name": "CronList"},
            {"type": "tool", "name": "CronUpdate"},
            {"type": "tool", "name": "CronDelete"},
            {"type": "tool", "name": "TaskWait"},
            {"type": "tool", "name": "TaskGet"},
            {"type": "tool", "name": "TaskList"},
            {"type": "tool", "name": "TaskOutput"},
            {"type": "tool", "name": "TaskStop"},
            {"type": "harness", "name": "Scheduler"},
            {"type": "harness", "name": "ScriptCronRunner"},
            {"type": "harness", "name": "scheduled_work_recipe_policy"},
        ),
    },
    {
        "id": "openmagi.security-posture",
        "name": "OpenMagi Security Posture",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": (
            "Metadata-only first-party security posture harnesses for boundary "
            "classification, external-surface fail-closed checks, sandbox "
            "preflight, credential pass-through policy, context scanning, and "
            "supply-chain advisory checks."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": False,
        "securityCritical": True,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "default-disabled ADK callback/plugin policy surface; no live "
                "tool, route, sandbox, credential, or provider attachment"
            ),
        },
        "permissions": (),
        "services": (),
        "tools": (),
        "hooks": (),
        "harnessRules": (
            "security_posture_matrix",
            "external_surface_fail_closed",
            "sandbox_preflight",
            "credential_pass_through_policy",
            "context_file_injection_guard",
            "supply_chain_advisory",
        ),
        "secrets": (),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "posturePreflightAttached": {"type": "boolean", "default": False},
                "externalSurfaceDispatchAttached": {
                    "type": "boolean",
                    "default": False,
                },
                "credentialBrokerAttached": {"type": "boolean", "default": False},
                "contextGuardBlocksPromptProjection": {
                    "type": "boolean",
                    "default": False,
                },
                "supplyChainStartupBannerAttached": {
                    "type": "boolean",
                    "default": False,
                },
            },
        },
        "capabilities": (
            {"type": "harness", "name": "security_posture_matrix"},
            {"type": "harness", "name": "external_surface_fail_closed"},
            {"type": "harness", "name": "sandbox_preflight"},
            {"type": "harness", "name": "credential_pass_through_policy"},
            {"type": "harness", "name": "context_file_injection_guard"},
            {"type": "harness", "name": "supply_chain_advisory"},
        ),
    },
    {
        "id": "openmagi.web-acquisition",
        "name": "OpenMagi Web Acquisition",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": (
            "Default-off provider-interface metadata for replaceable web "
            "acquisition, source ledger input, reader extraction, and browser "
            "worker provider surfaces."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "metadata-only provider boundary; future live calls must wrap "
                "individual provider functions as ADK FunctionTool through "
                "ToolHost policy; LongRunningFunctionTool is only for individual "
                "long crawl/render/export jobs"
            ),
        },
        "permissions": (),
        "services": (),
        "tools": (),
        "hooks": (),
        "harnessRules": (
            "web_acquisition_provider_boundary",
            "web_acquisition_source_ledger_boundary",
        ),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "providerInterfaces": {
                    "type": "array",
                    "default": (
                        {
                            "id": "search-api-provider",
                            "purpose": "Search result acquisition metadata.",
                            "providerCallAllowed": False,
                            "futureLiveSurface": "ADK FunctionTool through ToolHost",
                        },
                        {
                            "id": "reader-extraction-provider",
                            "purpose": "Reader extraction and source text normalization metadata.",
                            "providerCallAllowed": False,
                            "futureLiveSurface": "ADK FunctionTool through ToolHost",
                        },
                        {
                            "id": "browser-worker-agent-browser-provider",
                            "purpose": "Browser-worker or agent-browser acquisition metadata.",
                            "providerCallAllowed": False,
                            "futureLiveSurface": "ADK FunctionTool through ToolHost",
                        },
                        {
                            "id": "custom-third-party-provider",
                            "purpose": "Custom provider plugin metadata.",
                            "providerCallAllowed": False,
                            "futureLiveSurface": "ADK FunctionTool through ToolHost",
                        },
                    ),
                    "items": {"type": "object"},
                },
                "webAcquisitionOrchestratorAttached": {
                    "type": "boolean",
                    "default": False,
                },
                "sourceLedgerInputAttached": {
                    "type": "boolean",
                    "default": False,
                },
                "longRunningFunctionToolScope": {
                    "type": "string",
                    "default": "individual long crawl/render/export jobs only",
                },
            },
        },
        "capabilities": (
            {"type": "harness", "name": "provider-interface:search-api"},
            {"type": "harness", "name": "provider-interface:reader-extraction"},
            {"type": "harness", "name": "provider-interface:browser-worker-agent-browser"},
            {"type": "harness", "name": "provider-interface:custom-third-party"},
            {"type": "harness", "name": "web_acquisition_provider_boundary"},
            {"type": "harness", "name": "web_acquisition_source_ledger_boundary"},
        ),
    },
    {
        "id": "openmagi.web",
        "name": "OpenMagi Web",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": (
            "Default-disabled metadata for legacy web search and fetch surfaces; "
            "source/browser acquisition provider selection is represented by "
            "openmagi.web-acquisition."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": False,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "default-disabled source provider surface; future ADK FunctionTool "
                "attachment must route through ToolHost policy"
            ),
        },
        "permissions": ("read", "net"),
        "services": ("api-proxy", "firecrawl"),
        "tools": (
            {
                "name": "WebSearch",
                "entrypoint": "openmagi_core_agent.plugins.native.web:web_search",
            },
            {
                "name": "web-search",
                "entrypoint": "openmagi_core_agent.plugins.native.web:web_search",
            },
            {
                "name": "web_search",
                "entrypoint": "openmagi_core_agent.plugins.native.web:web_search",
            },
            {
                "name": "WebFetch",
                "entrypoint": "openmagi_core_agent.plugins.native.web:web_fetch",
            },
        ),
        "harnessRules": ("web_source_citation",),
        "secrets": (
            {
                "name": "GATEWAY_TOKEN",
                "source": "platform",
            },
            {
                "name": "FIRECRAWL_API_KEY",
                "source": "platform",
            },
        ),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "firecrawlEnabled": {
                    "type": "boolean",
                    "default": False,
                },
                "allowedDomains": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
        },
        "capabilities": (
            {"type": "tool", "name": "WebSearch"},
            {"type": "tool", "name": "web-search"},
            {"type": "tool", "name": "web_search"},
            {"type": "tool", "name": "WebFetch"},
            {"type": "harness", "name": "web_source_citation"},
            {"type": "service-endpoint", "name": "api-proxy"},
            {"type": "service-endpoint", "name": "firecrawl"},
        ),
    },
)

_NATIVE_PLUGIN_MANIFESTS: tuple[PluginManifest, ...] = tuple(
    sorted(
        (parse_plugin_manifest(data) for data in _NATIVE_PLUGIN_DATA),
        key=lambda manifest: manifest.plugin_id,
    )
)
_NATIVE_PLUGIN_BY_ID: dict[str, PluginManifest] = {
    manifest.plugin_id: manifest for manifest in _NATIVE_PLUGIN_MANIFESTS
}
_NATIVE_PLUGIN_IDS: tuple[str, ...] = tuple(manifest.plugin_id for manifest in _NATIVE_PLUGIN_MANIFESTS)


def native_plugin_manifests() -> tuple[PluginManifest, ...]:
    return tuple(manifest.model_copy(deep=True) for manifest in _NATIVE_PLUGIN_MANIFESTS)


def native_plugin_ids() -> tuple[str, ...]:
    return _NATIVE_PLUGIN_IDS


def native_plugin_by_id(plugin_id: str) -> PluginManifest | None:
    manifest = _NATIVE_PLUGIN_BY_ID.get(plugin_id)
    if manifest is None:
        return None
    return manifest.model_copy(deep=True)


__all__ = [
    "native_plugin_by_id",
    "native_plugin_ids",
    "native_plugin_manifests",
]
