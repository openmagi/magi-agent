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
        "defaultEnabled": True,
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
                "entrypoint": "magi_agent.plugins.agentmemory.tools:agentmemory_search",
            },
            {
                "name": "AgentMemoryRemember",
                "entrypoint": "magi_agent.plugins.agentmemory.tools:agentmemory_remember",
            },
        ),
        "hooks": (
            {
                "name": "agentmemory.recall",
                "point": "beforeModelCall",
                "entrypoint": "magi_agent.plugins.agentmemory.hooks:agentmemory_recall",
            },
            {
                "name": "agentmemory.observe",
                "point": "afterTurnEnd",
                "entrypoint": "magi_agent.plugins.agentmemory.hooks:agentmemory_observe",
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
        "id": "openmagi.artifacts",
        "name": "OpenMagi Artifacts",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "First-party artifact update and delete surfaces for local artifact bookkeeping.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "ADK ArtifactService-compatible local artifact policy surface.",
        },
        "permissions": ("write",),
        "services": (),
        "tools": (
            {
                "name": "ArtifactUpdate",
                "entrypoint": "magi_agent.plugins.native.artifacts:artifact_update",
            },
            {
                "name": "ArtifactDelete",
                "entrypoint": "magi_agent.plugins.native.artifacts:artifact_delete",
            },
        ),
        "harnessRules": ("artifact_bookkeeping_policy",),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "capabilities": (
            {"type": "tool", "name": "ArtifactUpdate"},
            {"type": "tool", "name": "ArtifactDelete"},
            {"type": "harness", "name": "artifact_bookkeeping_policy"},
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
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "browser provider surface; ADK FunctionTool attachment routes "
                "through ToolHost policy"
            ),
        },
        "permissions": ("read", "write", "net"),
        "services": ("browser-worker", "chat-proxy"),
        "tools": (
            {
                "name": "Browser",
                "entrypoint": "magi_agent.plugins.native.browser:browser",
            },
            {
                "name": "SocialBrowser",
                "entrypoint": "magi_agent.plugins.native.browser:social_browser",
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
        "id": "openmagi.coding",
        "name": "OpenMagi Coding",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": (
            "First-party coding, repository inspection, verification planning, "
            "and safe command metadata surfaces."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "ADK FunctionTool-compatible local coding harness surface.",
        },
        "permissions": ("read",),
        "services": (),
        "tools": (
            {
                "name": "CodeDiagnostics",
                "entrypoint": "magi_agent.plugins.native.coding:code_diagnostics",
            },
            {
                "name": "CodeIntelligence",
                "entrypoint": "magi_agent.plugins.native.coding:code_intelligence",
            },
            {
                "name": "CodeSymbolSearch",
                "entrypoint": "magi_agent.plugins.native.coding:code_symbol_search",
            },
            {
                "name": "CodeWorkspace",
                "entrypoint": "magi_agent.plugins.native.coding:code_workspace",
            },
            {
                "name": "CodingBenchmark",
                "entrypoint": "magi_agent.plugins.native.coding:coding_benchmark",
            },
            {
                "name": "CommitCheckpoint",
                "entrypoint": "magi_agent.plugins.native.coding:commit_checkpoint",
            },
            {
                "name": "PackageDependencyResolve",
                "entrypoint": "magi_agent.plugins.native.coding:package_dependency_resolve",
            },
            {
                "name": "ProjectVerificationPlanner",
                "entrypoint": "magi_agent.plugins.native.coding:project_verification_planner",
            },
            {
                "name": "RepoMap",
                "entrypoint": "magi_agent.plugins.native.coding:repo_map",
            },
            {
                "name": "RepositoryMap",
                "entrypoint": "magi_agent.plugins.native.coding:repository_map",
            },
            {
                "name": "RepoTaskState",
                "entrypoint": "magi_agent.plugins.native.coding:repo_task_state",
            },
            {
                "name": "SafeCommand",
                "entrypoint": "magi_agent.plugins.native.coding:safe_command",
            },
        ),
        "harnessRules": ("coding_verification_policy", "repo_intelligence_policy"),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "capabilities": (
            {"type": "tool", "name": "CodeDiagnostics"},
            {"type": "tool", "name": "CodeIntelligence"},
            {"type": "tool", "name": "CodeSymbolSearch"},
            {"type": "tool", "name": "CodeWorkspace"},
            {"type": "tool", "name": "CodingBenchmark"},
            {"type": "tool", "name": "CommitCheckpoint"},
            {"type": "tool", "name": "PackageDependencyResolve"},
            {"type": "tool", "name": "ProjectVerificationPlanner"},
            {"type": "tool", "name": "RepoMap"},
            {"type": "tool", "name": "RepositoryMap"},
            {"type": "tool", "name": "RepoTaskState"},
            {"type": "tool", "name": "SafeCommand"},
            {"type": "harness", "name": "coding_verification_policy"},
            {"type": "harness", "name": "repo_intelligence_policy"},
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
            "adkCompatibility": "ADK tool/plugin attachment point",
        },
        "permissions": ("read", "write", "net"),
        "services": ("document-worker", "document-converter-worker", "chat-proxy"),
        "tools": (
            {
                "name": "DocumentWrite",
                "entrypoint": "magi_agent.plugins.native.documents:document_write",
            },
            {
                "name": "SpreadsheetWrite",
                "entrypoint": "magi_agent.plugins.native.documents:spreadsheet_write",
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
            "adkCompatibility": "ADK tool/plugin attachment point",
        },
        "permissions": ("read", "write", "net"),
        "services": ("knowledge-worker", "chat-proxy"),
        "tools": (
            {
                "name": "KnowledgeSearch",
                "entrypoint": "magi_agent.plugins.native.knowledge:knowledge_search",
            },
            {
                "name": "knowledge-search",
                "entrypoint": "magi_agent.plugins.native.knowledge:knowledge_search",
            },
            {
                "name": "KnowledgeWrite",
                "entrypoint": "magi_agent.plugins.native.knowledge:knowledge_write",
            },
            {
                "name": "knowledge-write",
                "entrypoint": "magi_agent.plugins.native.knowledge:knowledge_write",
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
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "ADK tool/plugin attachment point",
        },
        "permissions": ("read", "meta"),
        "services": ("mission-ledger",),
        "tools": (
            {
                "name": "MissionLedger",
                "entrypoint": "magi_agent.plugins.native.missions:mission_ledger",
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
        "defaultEnabled": True,
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
                "entrypoint": "magi_agent.plugins.native.scheduled_work:cron_create",
            },
            {
                "name": "CronList",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:cron_list",
            },
            {
                "name": "CronUpdate",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:cron_update",
            },
            {
                "name": "CronDelete",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:cron_delete",
            },
            {
                "name": "TaskWait",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:task_wait",
            },
            {
                "name": "TaskGet",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:task_get",
            },
            {
                "name": "TaskList",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:task_list",
            },
            {
                "name": "TaskOutput",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:task_output",
            },
            {
                "name": "TaskStop",
                "entrypoint": "magi_agent.plugins.native.scheduled_work:task_stop",
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
        "id": "openmagi.skills",
        "name": "OpenMagi Skills",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "First-party skill loading, runtime hook, and external tool metadata surfaces.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "ADK plugin and callback metadata surface.",
        },
        "permissions": ("meta",),
        "services": (),
        "tools": (
            {
                "name": "SkillLoader",
                "entrypoint": "magi_agent.plugins.native.skills:skill_loader",
            },
            {
                "name": "SkillRuntimeHooks",
                "entrypoint": "magi_agent.plugins.native.skills:skill_runtime_hooks",
            },
            {
                "name": "ExternalToolLoader",
                "entrypoint": "magi_agent.plugins.native.skills:external_tool_loader",
            },
        ),
        "harnessRules": ("skill_runtime_policy",),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "capabilities": (
            {"type": "tool", "name": "SkillLoader"},
            {"type": "tool", "name": "SkillRuntimeHooks"},
            {"type": "tool", "name": "ExternalToolLoader"},
            {"type": "harness", "name": "skill_runtime_policy"},
        ),
    },
    {
        "id": "openmagi.source-ledger",
        "name": "OpenMagi Source Ledger",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "First-party batch read, date-range, and external source ledger tools.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "ADK FunctionTool-compatible source evidence surface.",
        },
        "permissions": ("read",),
        "services": (),
        "tools": (
            {
                "name": "BatchRead",
                "entrypoint": "magi_agent.plugins.native.source_ledger:batch_read",
            },
            {
                "name": "DateRange",
                "entrypoint": "magi_agent.plugins.native.source_ledger:date_range",
            },
            {
                "name": "ExternalSourceCache",
                "entrypoint": "magi_agent.plugins.native.source_ledger:external_source_cache",
            },
            {
                "name": "ExternalSourceRead",
                "entrypoint": "magi_agent.plugins.native.source_ledger:external_source_read",
            },
        ),
        "harnessRules": ("source_ledger_policy",),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "capabilities": (
            {"type": "tool", "name": "BatchRead"},
            {"type": "tool", "name": "DateRange"},
            {"type": "tool", "name": "ExternalSourceCache"},
            {"type": "tool", "name": "ExternalSourceRead"},
            {"type": "harness", "name": "source_ledger_policy"},
        ),
    },
    {
        "id": "openmagi.subagents",
        "name": "OpenMagi Subagents",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "First-party subagent and worktree apply surfaces for delegated workflow metadata.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "ADK multi-agent orchestration metadata surface.",
        },
        "permissions": ("execute",),
        "services": (),
        "tools": (
            {
                "name": "SpawnAgent",
                "entrypoint": "magi_agent.plugins.native.subagents:spawn_agent",
            },
            {
                "name": "SpawnWorktreeApply",
                "entrypoint": "magi_agent.plugins.native.subagents:spawn_worktree_apply",
            },
        ),
        "harnessRules": ("subagent_delegation_policy",),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "capabilities": (
            {"type": "tool", "name": "SpawnAgent"},
            {"type": "tool", "name": "SpawnWorktreeApply"},
            {"type": "harness", "name": "subagent_delegation_policy"},
        ),
    },
    {
        "id": "openmagi.taskboard",
        "name": "OpenMagi Taskboard",
        "kind": "native",
        "version": "0.1.0-adk-scaffold",
        "description": "First-party task board, mode switch, notification, and memory redaction surfaces.",
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": "ADK FunctionTool-compatible task coordination surface.",
        },
        "permissions": ("meta",),
        "services": (),
        "tools": (
            {
                "name": "TaskBoard",
                "entrypoint": "magi_agent.plugins.native.taskboard:task_board",
            },
            {
                "name": "MemoryRedact",
                "entrypoint": "magi_agent.plugins.native.taskboard:memory_redact",
            },
            {
                "name": "NotifyUser",
                "entrypoint": "magi_agent.plugins.native.taskboard:notify_user",
            },
            {
                "name": "SwitchToActMode",
                "entrypoint": "magi_agent.plugins.native.taskboard:switch_to_act_mode",
            },
        ),
        "harnessRules": ("taskboard_coordination_policy",),
        "configSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        "capabilities": (
            {"type": "tool", "name": "TaskBoard"},
            {"type": "tool", "name": "MemoryRedact"},
            {"type": "tool", "name": "NotifyUser"},
            {"type": "tool", "name": "SwitchToActMode"},
            {"type": "harness", "name": "taskboard_coordination_policy"},
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
        "defaultEnabled": True,
        "optOutAllowed": False,
        "securityCritical": True,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "ADK callback/plugin policy surface; no live tool, route, "
                "sandbox, credential, or provider attachment"
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
            "Provider-interface metadata for replaceable web "
            "acquisition, source ledger input, reader extraction, and browser "
            "worker provider surfaces."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
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
            "Metadata for web search and fetch surfaces, including legacy aliases; "
            "source/browser acquisition provider selection is represented by "
            "openmagi.web-acquisition."
        ),
        "publisher": "openmagi",
        "defaultInstalled": True,
        "defaultEnabled": True,
        "optOutAllowed": True,
        "securityCritical": False,
        "audit_required": True,
        "runtime": {
            "minCoreVersion": "0.1.0-adk-scaffold",
            "adkCompatibility": (
                "source provider surface; ADK FunctionTool attachment routes "
                "through ToolHost policy"
            ),
        },
        "permissions": ("read", "net"),
        "services": ("api-proxy", "firecrawl"),
        "tools": (
            {
                "name": "WebSearch",
                "entrypoint": "magi_agent.plugins.native.web:web_search",
            },
            {
                "name": "web-search",
                "entrypoint": "magi_agent.plugins.native.web:web_search",
            },
            {
                "name": "web_search",
                "entrypoint": "magi_agent.plugins.native.web:web_search",
            },
            {
                "name": "WebFetch",
                "entrypoint": "magi_agent.plugins.native.web:web_fetch",
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
