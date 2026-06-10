from __future__ import annotations

import importlib
import subprocess
import sys

from magi_agent.plugins.manager import PluginOptOutRecord, resolve_plugin_state
from magi_agent.plugins.manifest import PluginKind, PluginManifest
from magi_agent.plugins.native_catalog import (
    native_plugin_by_id,
    native_plugin_ids,
    native_plugin_manifests,
)


EXPECTED_NATIVE_PLUGIN_IDS = (
    "openmagi.agentmemory",
    "openmagi.apify",
    "openmagi.artifacts",
    "openmagi.browser",
    "openmagi.coding",
    "openmagi.documents",
    "openmagi.knowledge",
    "openmagi.missions",
    "openmagi.scheduled-work",
    "openmagi.security-posture",
    "openmagi.skills",
    "openmagi.source-ledger",
    "openmagi.subagents",
    "openmagi.taskboard",
    "openmagi.web",
    "openmagi.web-acquisition",
)

TS_FIRST_PARTY_PARITY_TOOL_NAMES = frozenset(
    {
        "ArtifactDelete",
        "ArtifactUpdate",
        "BatchRead",
        "Browser",
        "CodeDiagnostics",
        "CodeIntelligence",
        "CodeSymbolSearch",
        "CodeWorkspace",
        "CodingBenchmark",
        "CommitCheckpoint",
        "DateRange",
        "DocumentWrite",
        "ExternalSourceCache",
        "ExternalSourceRead",
        "ExternalToolLoader",
        "KnowledgeSearch",
        "KnowledgeWrite",
        "MemoryRedact",
        "MissionLedger",
        "NotifyUser",
        "PackageDependencyResolve",
        "ProjectVerificationPlanner",
        "RepoMap",
        "RepoTaskState",
        "RepositoryMap",
        "SafeCommand",
        "SkillLoader",
        "SkillRuntimeHooks",
        "SocialBrowser",
        "SpawnAgent",
        "SpawnWorktreeApply",
        "SpreadsheetWrite",
        "SwitchToActMode",
        "TaskBoard",
        "WebFetch",
        "WebSearch",
    }
)


def _by_id() -> dict[str, PluginManifest]:
    return {manifest.plugin_id: manifest for manifest in native_plugin_manifests()}


def _tool_names(manifest: PluginManifest) -> tuple[str, ...]:
    return tuple(tool.name for tool in manifest.tools)


def _tool_capability_names(manifest: PluginManifest) -> tuple[str, ...]:
    return tuple(capability.name for capability in manifest.capabilities if capability.type == "tool")


def _capability_names(manifest: PluginManifest, capability_type: str) -> tuple[str, ...]:
    return tuple(
        capability.name for capability in manifest.capabilities if capability.type == capability_type
    )


def _secret_sources(manifest: PluginManifest) -> dict[str, str]:
    return {secret.name: secret.source for secret in manifest.secrets}


def test_catalog_contains_exact_native_plugin_ids_in_sorted_order() -> None:
    manifests = native_plugin_manifests()

    assert tuple(manifest.plugin_id for manifest in manifests) == EXPECTED_NATIVE_PLUGIN_IDS
    assert native_plugin_ids() == EXPECTED_NATIVE_PLUGIN_IDS
    assert tuple(sorted(native_plugin_ids())) == native_plugin_ids()


def test_every_native_manifest_has_default_enabled_opt_out_audit_policy() -> None:
    for manifest in native_plugin_manifests():
        assert manifest.kind is PluginKind.NATIVE
        assert manifest.publisher == "openmagi"
        assert manifest.default_installed is True
        assert manifest.default_enabled is True
        if manifest.plugin_id == "openmagi.security-posture":
            assert manifest.opt_out_allowed is False
            assert manifest.security_critical is True
        else:
            assert manifest.opt_out_allowed is True
            assert manifest.security_critical is False
        assert manifest.audit_required is True


def test_native_tool_and_hook_entrypoints_are_importable_callables() -> None:
    for manifest in native_plugin_manifests():
        for ref in (*manifest.tools, *manifest.hooks):
            module_name, function_name = ref.entrypoint.split(":", 1)
            module = importlib.import_module(module_name)
            value = getattr(module, function_name)
            assert callable(value), ref.entrypoint


def test_knowledge_manifest_matches_prd_metadata_shape() -> None:
    knowledge = _by_id()["openmagi.knowledge"]

    expected_tools = ("KnowledgeSearch", "knowledge-search", "KnowledgeWrite", "knowledge-write")
    assert _tool_names(knowledge) == expected_tools
    assert _tool_capability_names(knowledge) == expected_tools
    assert knowledge.permissions == ("read", "write", "net")
    assert knowledge.services == ("knowledge-worker", "chat-proxy")
    assert _secret_sources(knowledge)["GATEWAY_TOKEN"] == "platform"
    assert "knowledge_write_scope" in knowledge.harness_rules
    assert knowledge.config_schema["properties"]["collections"]["type"] == "array"  # type: ignore[index]

    dumped = knowledge.model_dump(by_alias=True)
    assert dumped["id"] == "openmagi.knowledge"
    assert dumped["defaultInstalled"] is True
    assert dumped["defaultEnabled"] is True
    assert dumped["optOutAllowed"] is True
    assert dumped["securityCritical"] is False
    assert dumped["harnessRules"] == ("knowledge_write_scope",)
    assert dumped["configSchema"]["properties"]["collections"]["items"]["type"] == "string"  # type: ignore[index]


def test_web_browser_documents_and_missions_expose_expected_native_metadata() -> None:
    manifests = _by_id()

    web = manifests["openmagi.web"]
    expected_web_tools = ("WebSearch", "web-search", "web_search", "WebFetch")
    assert _tool_names(web) == expected_web_tools
    assert _tool_capability_names(web) == expected_web_tools
    assert web.default_enabled is True
    assert web.services == ("api-proxy", "firecrawl")
    assert _secret_sources(web) == {
        "GATEWAY_TOKEN": "platform",
        "FIRECRAWL_API_KEY": "platform",
    }
    assert web.config_schema["properties"]["firecrawlEnabled"]["default"] is False  # type: ignore[index]

    browser = manifests["openmagi.browser"]
    assert _tool_names(browser) == ("Browser", "SocialBrowser")
    assert browser.default_enabled is True
    assert browser.services == ("browser-worker", "chat-proxy")
    assert _secret_sources(browser)["GATEWAY_TOKEN"] == "platform"
    assert browser.config_schema["properties"]["socialBrowserEnabled"]["default"] is False  # type: ignore[index]

    documents = manifests["openmagi.documents"]
    expected_document_tools = ("DocumentWrite", "SpreadsheetWrite")
    assert _tool_names(documents) == expected_document_tools
    assert _tool_capability_names(documents) == (
        "DocumentWrite",
        "SpreadsheetWrite",
        "FileDeliver",
        "FileSend",
    )
    assert documents.services == ("document-worker", "document-converter-worker", "chat-proxy")
    assert _secret_sources(documents)["GATEWAY_TOKEN"] == "platform"
    assert "document_format_compatibility" in documents.harness_rules
    assert documents.config_schema["properties"]["allowedFormats"]["default"] == (  # type: ignore[index]
        "md",
        "txt",
        "html",
        "docx",
        "pdf",
        "hwpx",
        "xlsx",
        "csv",
    )

    missions = manifests["openmagi.missions"]
    expected_mission_tools = ("MissionLedger",)
    assert _tool_names(missions) == expected_mission_tools
    assert _tool_capability_names(missions) == expected_mission_tools
    assert missions.default_enabled is True
    assert missions.permissions == ("read", "meta")
    assert missions.services == ("mission-ledger",)
    assert _secret_sources(missions)["GATEWAY_TOKEN"] == "platform"
    assert "mission_coordination_scope" in missions.harness_rules


def test_ts_first_party_tool_surfaces_are_available_in_native_catalog() -> None:
    tool_names = {
        tool.name
        for manifest in native_plugin_manifests()
        for tool in manifest.tools
    }

    assert TS_FIRST_PARTY_PARITY_TOOL_NAMES.issubset(tool_names)


def test_web_acquisition_manifest_is_default_enabled_provider_interface_metadata_only() -> None:
    web_acquisition = _by_id()["openmagi.web-acquisition"]
    provider_interfaces = web_acquisition.config_schema["properties"]["providerInterfaces"]["default"]  # type: ignore[index]

    assert web_acquisition.name == "OpenMagi Web Acquisition"
    assert web_acquisition.default_installed is True
    assert web_acquisition.default_enabled is True
    assert web_acquisition.opt_out_allowed is True
    assert web_acquisition.security_critical is False
    assert web_acquisition.audit_required is True
    assert web_acquisition.permissions == ()
    assert web_acquisition.services == ()
    assert web_acquisition.tools == ()
    assert web_acquisition.hooks == ()
    assert _secret_sources(web_acquisition) == {}
    assert web_acquisition.harness_rules == (
        "web_acquisition_provider_boundary",
        "web_acquisition_source_ledger_boundary",
    )
    assert _capability_names(web_acquisition, "harness") == (
        "provider-interface:search-api",
        "provider-interface:reader-extraction",
        "provider-interface:browser-worker-agent-browser",
        "provider-interface:custom-third-party",
        "web_acquisition_provider_boundary",
        "web_acquisition_source_ledger_boundary",
    )
    assert tuple(item["id"] for item in provider_interfaces) == (
        "search-api-provider",
        "reader-extraction-provider",
        "browser-worker-agent-browser-provider",
        "custom-third-party-provider",
    )
    assert {item["providerCallAllowed"] for item in provider_interfaces} == {False}
    assert {
        item["futureLiveSurface"] for item in provider_interfaces
    } == {"ADK FunctionTool through ToolHost"}
    assert web_acquisition.config_schema["properties"]["webAcquisitionOrchestratorAttached"]["default"] is False  # type: ignore[index]
    assert web_acquisition.config_schema["properties"]["longRunningFunctionToolScope"]["default"] == (  # type: ignore[index]
        "individual long crawl/render/export jobs only"
    )
    dumped = web_acquisition.model_dump(by_alias=True)
    assert "entrypoint" not in repr(dumped).lower()
    assert "handler" not in repr(dumped).lower()
    assert "route" not in repr(dumped).lower()


def test_security_posture_manifest_is_metadata_only_default_enabled() -> None:
    security = _by_id()["openmagi.security-posture"]

    assert security.name == "OpenMagi Security Posture"
    assert security.default_installed is True
    assert security.default_enabled is True
    assert security.opt_out_allowed is False
    assert security.security_critical is True
    assert security.audit_required is True
    assert security.permissions == ()
    assert security.services == ()
    assert security.tools == ()
    assert security.hooks == ()
    assert "Metadata-only" in security.description
    assert "no live" in security.runtime.adk_compatibility
    assert _secret_sources(security) == {}
    assert security.harness_rules == (
        "security_posture_matrix",
        "external_surface_fail_closed",
        "sandbox_preflight",
        "credential_pass_through_policy",
        "context_file_injection_guard",
        "supply_chain_advisory",
    )
    assert _capability_names(security, "harness") == security.harness_rules
    assert security.config_schema["properties"]["posturePreflightAttached"]["default"] is False  # type: ignore[index]
    assert security.config_schema["properties"]["externalSurfaceDispatchAttached"]["default"] is False  # type: ignore[index]
    assert security.config_schema["properties"]["credentialBrokerAttached"]["default"] is False  # type: ignore[index]
    assert security.config_schema["properties"]["contextGuardBlocksPromptProjection"]["default"] is False  # type: ignore[index]
    assert security.config_schema["properties"]["supplyChainStartupBannerAttached"]["default"] is False  # type: ignore[index]


def test_agentmemory_manifest_is_metadata_only_default_enabled_provider_candidate() -> None:
    agentmemory = _by_id()["openmagi.agentmemory"]

    assert agentmemory.name == "OpenMagi AgentMemory"
    assert agentmemory.default_installed is True
    assert agentmemory.default_enabled is True
    assert agentmemory.opt_out_allowed is True
    assert agentmemory.security_critical is False
    assert agentmemory.audit_required is True
    assert agentmemory.permissions == ()
    assert agentmemory.services == ("agentmemory-provider-endpoint",)
    assert _tool_names(agentmemory) == ("AgentMemorySearch", "AgentMemoryRemember")
    assert tuple(hook.name for hook in agentmemory.hooks) == (
        "agentmemory.recall",
        "agentmemory.observe",
    )
    assert _tool_capability_names(agentmemory) == (
        "AgentMemorySearch",
        "AgentMemoryRemember",
    )
    assert tuple(
        capability.name
        for capability in agentmemory.capabilities
        if capability.type == "hook"
    ) == ("agentmemory.recall", "agentmemory.observe")
    assert "memory_agentmemory_provider_boundary" in agentmemory.harness_rules
    assert agentmemory.config_schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    assert _secret_sources(agentmemory) == {}


def test_scheduled_work_manifest_is_metadata_only_default_enabled_policy_surface() -> None:
    scheduled_work = _by_id()["openmagi.scheduled-work"]

    expected_tools = (
        "CronCreate",
        "CronList",
        "CronUpdate",
        "CronDelete",
        "TaskWait",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
    )
    assert scheduled_work.name == "OpenMagi Scheduled Work"
    assert scheduled_work.default_installed is True
    assert scheduled_work.default_enabled is True
    assert scheduled_work.opt_out_allowed is True
    assert scheduled_work.security_critical is False
    assert scheduled_work.audit_required is True
    assert scheduled_work.permissions == ()
    assert scheduled_work.services == ()
    assert _secret_sources(scheduled_work) == {}
    assert _tool_names(scheduled_work) == expected_tools
    assert _tool_capability_names(scheduled_work) == expected_tools
    assert _capability_names(scheduled_work, "harness") == (
        "Scheduler",
        "ScriptCronRunner",
        "scheduled_work_recipe_policy",
    )
    assert scheduled_work.harness_rules == ("scheduled_work_recipe_policy",)
    assert scheduled_work.runtime.adk_compatibility == (
        "first-party recipe/native-plugin policy surface only; future Cron tools "
        "may wrap through ADK FunctionTool via ToolHost policy after approval; "
        "scheduler/background runtime is not LongRunningFunctionTool"
    )
    assert scheduled_work.config_schema == {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schedulerAttached": {"type": "boolean", "default": False},
            "scriptCronRunnerAttached": {"type": "boolean", "default": False},
            "channelDeliveryAttached": {"type": "boolean", "default": False},
        },
    }


def test_methodology_and_superpowers_compat_are_not_native_plugin_catalog_entries() -> None:
    manifests = _by_id()

    assert "openmagi.agent-methodology" not in manifests
    assert "openmagi.superpowers-compat" not in manifests
    assert native_plugin_by_id("openmagi.agent-methodology") is None
    assert native_plugin_by_id("openmagi.superpowers-compat") is None


def test_catalog_returns_defensive_manifest_config_schema_and_tool_copies() -> None:
    first = native_plugin_manifests()
    knowledge = next(manifest for manifest in first if manifest.plugin_id == "openmagi.knowledge")

    knowledge.config_schema["properties"]["collections"]["items"]["type"] = "integer"  # type: ignore[index]
    object.__setattr__(knowledge.tools[0], "name", "MutatedKnowledgeSearch")

    fresh = native_plugin_by_id("openmagi.knowledge")
    assert fresh is not knowledge
    assert fresh.config_schema["properties"]["collections"]["items"]["type"] == "string"  # type: ignore[index]
    assert fresh.tools[0] is not knowledge.tools[0]
    assert fresh.tools[0].name == "KnowledgeSearch"


def test_native_catalog_resolves_to_enabled_metadata_only_state_and_opt_out_removes_tools() -> None:
    manifests = native_plugin_manifests()

    state = resolve_plugin_state(manifests)

    assert tuple(status.plugin_id for status in state.plugins) == EXPECTED_NATIVE_PLUGIN_IDS
    agentmemory_status = next(
        status for status in state.plugins if status.plugin_id == "openmagi.agentmemory"
    )
    scheduled_work_status = next(
        status for status in state.plugins if status.plugin_id == "openmagi.scheduled-work"
    )
    web_status = next(status for status in state.plugins if status.plugin_id == "openmagi.web")
    browser_status = next(status for status in state.plugins if status.plugin_id == "openmagi.browser")
    web_acquisition_status = next(
        status for status in state.plugins if status.plugin_id == "openmagi.web-acquisition"
    )
    assert agentmemory_status.enabled is True
    assert agentmemory_status.status_reason == "enabled"
    assert scheduled_work_status.installed is True
    assert scheduled_work_status.enabled is True
    assert scheduled_work_status.default_enabled is True
    assert scheduled_work_status.status_reason == "enabled"
    assert web_status.enabled is True
    assert web_status.status_reason == "enabled"
    assert browser_status.enabled is True
    assert browser_status.status_reason == "enabled"
    assert web_acquisition_status.enabled is True
    assert web_acquisition_status.status_reason == "enabled"
    assert all(status.enabled for status in state.plugins)
    assert state.active_tools == (
        "AgentMemoryRemember",
        "AgentMemorySearch",
        "ArtifactDelete",
        "ArtifactUpdate",
        "BatchRead",
        "Browser",
        "CodeDiagnostics",
        "CodeIntelligence",
        "CodeSymbolSearch",
        "CodeWorkspace",
        "CodingBenchmark",
        "CommitCheckpoint",
        "CronCreate",
        "CronDelete",
        "CronList",
        "CronUpdate",
        "DateRange",
        "DocumentWrite",
        "ExternalSourceCache",
        "ExternalSourceRead",
        "ExternalToolLoader",
        "KnowledgeSearch",
        "KnowledgeWrite",
        "MemoryRedact",
        "MissionLedger",
        "NotifyUser",
        "PackageDependencyResolve",
        "ProjectVerificationPlanner",
        "RepoMap",
        "RepoTaskState",
        "RepositoryMap",
        "SafeCommand",
        "SkillLoader",
        "SkillRuntimeHooks",
        "SocialBrowser",
        "SpawnAgent",
        "SpawnWorktreeApply",
        "SpreadsheetWrite",
        "SwitchToActMode",
        "TaskBoard",
        "TaskGet",
        "TaskList",
        "TaskOutput",
        "TaskStop",
        "TaskWait",
        "WebFetch",
        "WebSearch",
        "apify_run_actor",
        "apify_search_actors",
        "knowledge-search",
        "knowledge-write",
        "web-search",
        "web_search",
    )
    assert "agentmemory.recall" in state.active_hooks
    assert "agentmemory.observe" in state.active_hooks
    assert "mission_coordination_scope" in state.active_harness_rules
    assert "security_posture_matrix" in state.active_harness_rules
    assert "external_surface_fail_closed" in state.active_harness_rules
    assert "sandbox_preflight" in state.active_harness_rules
    assert "credential_pass_through_policy" in state.active_harness_rules
    assert "context_file_injection_guard" in state.active_harness_rules
    assert "supply_chain_advisory" in state.active_harness_rules
    assert "scheduled_work_recipe_policy" in state.active_harness_rules
    assert "web_acquisition_provider_boundary" in state.active_harness_rules
    assert "DocumentRead" not in state.active_tools
    assert "MissionExport" not in state.active_tools
    assert state.traffic_attached is False
    assert state.execution_attached is False
    assert all(status.traffic_attached is False for status in state.plugins)
    assert all(status.execution_attached is False for status in state.plugins)

    opted_out = resolve_plugin_state(
        manifests,
        (
            PluginOptOutRecord(
                pluginId="openmagi.web",
                scope="bot",
                actor="user:test",
                reason="catalog contract test",
                ts="2026-05-15T00:00:00Z",
            ),
        ),
    )

    assert "WebSearch" not in opted_out.active_tools
    assert "web-search" not in opted_out.active_tools
    assert "web_search" not in opted_out.active_tools
    assert "WebFetch" not in opted_out.active_tools
    web_status = next(status for status in opted_out.plugins if status.plugin_id == "openmagi.web")
    assert web_status.enabled is False
    assert web_status.status_reason == "opted_out"
    assert opted_out.opt_outs[0].affected_tools == ("WebSearch", "web-search", "web_search", "WebFetch")

    knowledge_opted_out = resolve_plugin_state(
        manifests,
        (
            PluginOptOutRecord(
                pluginId="openmagi.knowledge",
                scope="bot",
                actor="user:test",
                reason="catalog contract test",
                ts="2026-05-15T00:00:00Z",
            ),
        ),
    )

    assert "KnowledgeSearch" not in knowledge_opted_out.active_tools
    assert "knowledge-search" not in knowledge_opted_out.active_tools
    assert "KnowledgeWrite" not in knowledge_opted_out.active_tools
    assert "knowledge-write" not in knowledge_opted_out.active_tools
    knowledge_status = next(
        status for status in knowledge_opted_out.plugins if status.plugin_id == "openmagi.knowledge"
    )
    assert knowledge_status.enabled is False
    assert knowledge_status.status_reason == "opted_out"
    assert knowledge_opted_out.opt_outs[0].affected_tools == (
        "KnowledgeSearch",
        "knowledge-search",
        "KnowledgeWrite",
        "knowledge-write",
    )

    documents_opted_out = resolve_plugin_state(
        manifests,
        (
            PluginOptOutRecord(
                pluginId="openmagi.documents",
                scope="bot",
                actor="user:test",
                reason="catalog contract test",
                ts="2026-05-15T00:00:00Z",
            ),
        ),
    )

    assert "DocumentWrite" not in documents_opted_out.active_tools
    assert "SpreadsheetWrite" not in documents_opted_out.active_tools
    assert "FileDeliver" not in documents_opted_out.active_tools
    assert "FileSend" not in documents_opted_out.active_tools
    documents_status = next(
        status for status in documents_opted_out.plugins if status.plugin_id == "openmagi.documents"
    )
    assert documents_status.enabled is False
    assert documents_status.status_reason == "opted_out"
    assert documents_opted_out.opt_outs[0].affected_tools == (
        "DocumentWrite",
        "SpreadsheetWrite",
        "FileDeliver",
        "FileSend",
    )


def test_security_critical_native_plugin_cannot_be_opted_out() -> None:
    manifests = native_plugin_manifests()

    try:
        resolve_plugin_state(
            manifests,
            (
                PluginOptOutRecord(
                    pluginId="openmagi.security-posture",
                    scope="bot",
                    actor="user:test",
                    reason="catalog contract test",
                    ts="2026-05-15T00:00:00Z",
                ),
            ),
        )
    except ValueError as exc:
        assert str(exc) == "plugin cannot be opted out: openmagi.security-posture"
    else:  # pragma: no cover
        raise AssertionError("security posture plugin opt-out unexpectedly succeeded")


def test_file_delivery_native_catalog_entries_are_documents_metadata_only() -> None:
    documents = _by_id()["openmagi.documents"]
    dumped = documents.model_dump(by_alias=True)

    assert _tool_names(documents) == ("DocumentWrite", "SpreadsheetWrite")
    assert _tool_capability_names(documents) == (
        "DocumentWrite",
        "SpreadsheetWrite",
        "FileDeliver",
        "FileSend",
    )
    assert documents.default_enabled is True
    assert "chat-proxy" in documents.services
    assert dumped["defaultEnabled"] is True
    assert "trafficAttached" not in dumped
    assert "executionAttached" not in dumped
    assert "FileDeliver" not in repr(dumped["tools"])
    assert "FileSend" not in repr(dumped["tools"])
    assert "handler" not in repr(dumped).lower()
    assert "route" not in repr(dumped).lower()

    state = resolve_plugin_state(native_plugin_manifests())
    documents_status = next(
        status for status in state.plugins if status.plugin_id == "openmagi.documents"
    )
    assert documents_status.traffic_attached is False
    assert documents_status.execution_attached is False
    assert "FileDeliver" not in state.active_tools
    assert "FileSend" not in state.active_tools


def test_unknown_native_plugin_id_returns_none() -> None:
    assert native_plugin_by_id("openmagi.missing") is None


def test_apify_plugin_manifest_contract() -> None:
    manifest = native_plugin_by_id("openmagi.apify")
    assert manifest is not None
    assert manifest.kind is PluginKind.NATIVE
    assert manifest.default_installed is True
    assert manifest.default_enabled is True
    tool_names = {tool.name for tool in manifest.tools}
    assert tool_names == {"apify_search_actors", "apify_run_actor"}
    secret = {s.name: s.source for s in manifest.secrets}
    assert secret == {"APIFY_TOKEN": "user"}
    assert set(manifest.permissions) == {"read", "net"}


def test_native_catalog_import_boundary_does_not_load_adk_runtime_routes_or_native_modules() -> None:
    script = """
import importlib
import sys

importlib.import_module("magi_agent.plugins.native_catalog")
forbidden_prefixes = (
    "google.adk",
    "magi_agent.adk_bridge",
    "magi_agent.runtime",
    "magi_agent.tools.dispatcher",
    "magi_agent.tools.registry",
    "magi_agent.hooks.bus",
    "magi_agent.transport",
    "magi_agent.plugins.manager",
    "magi_agent.plugins.native.",
)
loaded = [
    name
    for name in sys.modules
    if name == forbidden_prefixes[0] or name.startswith(forbidden_prefixes)
]
if loaded:
    raise AssertionError(f"native catalog import loaded forbidden modules: {loaded}")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
